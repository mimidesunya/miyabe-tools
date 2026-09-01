from __future__ import annotations

import shlex
import signal
import subprocess
import time
import re
from pathlib import Path

from deploy.scraper_runtime.celery import app as app_module
from deploy.scraper_runtime.celery.app import app
from deploy.scraper_runtime.celery import runtime as celery_runtime
from tools.tasks.runner import process_group_popen_kwargs, terminate_process_group
from tools.tasks import status as batch_status
from tools.tasks import generation_sweep_state
from tools.tasks import index_outbox
from tools.gijiroku import gijiroku_targets
from tools.gijiroku import audit_minutes_robots
from tools.reiki import reiki_targets


# 一時的な失敗をその場で投げ直す回数。これを超えたものは待ち行列側に任せる。
INDEX_UPDATE_MAX_RETRIES = celery_runtime.env_int("INDEX_UPDATE_MAX_RETRIES", 3, minimum=0)


# その場の投げ直しは短く。長く空けるのは待ち行列の掃き取りの役目。
def _index_retry_countdown(retries: int) -> int:
    return min(15 * 60, 60 * (2 ** max(0, int(retries))))


ROOT = Path(__file__).resolve().parents[3]
INDEX_BULK_RE = re.compile(r"^\[BULK\]\s+.*\btotal=(?P<total>\d+)\b")
INDEX_DONE_RE = re.compile(r"^\[DONE\]\s+.*\bcount=(?P<count>\d+)\b")


# スクレイパ起動に使う Python コマンド文字列を環境変数から読む。
def _python_command_text() -> str:
    return celery_runtime.env_text("SCRAPER_PYTHON_COMMAND", "python3")


# Python コマンド文字列を subprocess 用の配列へ分解する。
def _python_command() -> list[str]:
    return shlex.split(_python_command_text())


# 例規集 PHP スクレイパ起動に使う PHP コマンド文字列を読む。
def _php_command_text() -> str:
    return celery_runtime.env_text("SCRAPER_PHP_COMMAND", "php")


# 子コマンドを起動し、Celery 停止シグナル時は子プロセスも止める。
def _run_command(label: str, command: list[str]) -> None:
    print(f"[CELERY] {label}: {celery_runtime.command_text(command)}", flush=True)
    stop_requested = False
    previous_handlers: dict[int, object] = {}

    # Celery worker への停止要求を、起動中の子コマンド停止へつなげる。
    def request_stop(signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        print(f"[CELERY] {label}: received signal {int(signum)}; stopping child process", flush=True)

    for signame in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)

    process = subprocess.Popen(command, cwd=str(ROOT), **process_group_popen_kwargs())
    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            if stop_requested:
                returncode = terminate_process_group(process)
                if returncode is None:
                    returncode = -signal.SIGTERM
                break
            time.sleep(1.0)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {returncode}")


# スクレイピング直後に検索 index 更新まで行う設定かを読む。
def _scraper_build_search_index() -> bool:
    return celery_runtime.env_bool("SCRAPER_BUILD_SEARCH_INDEX", False)


# 会議録 index を全再構築するコマンドを作る。
def _gijiroku_backfill_command() -> list[str]:
    return _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "rebuild",
        "--doc-type",
        "minutes",
    ]


# 会議録一括スクレイパを remote 用オプション付きで起動するコマンドを作る。
def _gijiroku_scrape_command(*, retry_failed: bool = False, name_filter: str = "") -> list[str]:
    command = _python_command() + ["tools/gijiroku/scrape_all_minutes.py"]
    if celery_runtime.env_bool("SCRAPER_GIJIROKU_ACK_ROBOTS", True):
        command.append("--ack-robots")
    command.extend(
        [
            "--parallel",
            str(celery_runtime.env_int("SCRAPER_GIJIROKU_PARALLEL", 8, minimum=1)),
            "--index-parallel",
            str(celery_runtime.env_int("SCRAPER_GIJIROKU_INDEX_PARALLEL", 1, minimum=1)),
            "--per-host-parallel",
            str(celery_runtime.env_int("SCRAPER_GIJIROKU_PER_HOST_PARALLEL", 1, minimum=1)),
            "--per-host-start-interval",
            str(
                celery_runtime.env_float(
                    "SCRAPER_GIJIROKU_PER_HOST_START_INTERVAL",
                    2.0,
                    minimum=0.0,
                )
            ),
            "--python-command",
            _python_command_text(),
            "--index-dispatch",
            "celery",
            # DBSR 系の一覧収集は既定 900 秒で打ち切られる。件数の多い自治体は
            # その範囲で全一覧を走査し切れず partial_planned のまま再投入され続ける。
            # 上限なし再走査では 0（無制限）を渡せるよう env で上書きできるようにする。
            "--dbsr-discovery-timeout-seconds",
            str(
                celery_runtime.env_int(
                    "SCRAPER_GIJIROKU_DBSR_DISCOVERY_TIMEOUT",
                    900,
                    minimum=0,
                )
            ),
        ]
    )
    if not _scraper_build_search_index():
        command.append("--no-build-index")
    if retry_failed:
        command.append("--retry-failed")
    if name_filter.strip():
        command.extend(["--filter", name_filter.strip()])
    return command


# 例規集 index を全再構築するコマンドを作る。
def _reiki_backfill_command() -> list[str]:
    return _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "rebuild",
        "--doc-type",
        "reiki",
    ]


# stale な background_tasks メタ情報を実データから復旧するコマンドを作る。
def _metadata_reconcile_command(task_name: str) -> list[str]:
    return _python_command() + [
        "tools/tasks/backfill.py",
        "--tasks",
        task_name,
    ]


# 例規集一括スクレイパを remote 用オプション付きで起動するコマンドを作る。
def _reiki_scrape_command(*, retry_failed: bool = False, name_filter: str = "") -> list[str]:
    command = _python_command() + ["tools/reiki/scrape_all_reiki.py"]
    if celery_runtime.env_bool("SCRAPER_REIKI_CHECK_UPDATES", True):
        command.append("--check-updates")
    command.extend(
        [
            "--parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_PARALLEL", 1, minimum=1)),
            "--index-parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_INDEX_PARALLEL", 1, minimum=1)),
            "--per-host-parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_PER_HOST_PARALLEL", 1, minimum=1)),
            "--per-host-start-interval",
            str(
                celery_runtime.env_float(
                    "SCRAPER_REIKI_PER_HOST_START_INTERVAL",
                    2.0,
                    minimum=0.0,
                )
            ),
            "--python-command",
            _python_command_text(),
            "--php-command",
            _php_command_text(),
            "--index-dispatch",
            "celery",
        ]
    )
    if not _scraper_build_search_index():
        command.append("--no-build-index")
    if retry_failed:
        command.append("--retry-failed")
    if name_filter.strip():
        command.extend(["--filter", name_filter.strip()])
    return command


# 手動再構築タスク用に、doc_type を切り替えた index rebuild コマンドを作る。
def _rebuild_command(kind: str, name_filter: str) -> list[str]:
    doc_type = "minutes" if kind == "minutes" else "reiki"
    command = _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "rebuild",
        "--doc-type",
        doc_type,
    ]
    if name_filter.strip() != "":
        print("[CELERY] name_filter is ignored by OpenSearch rebuild", flush=True)
    return command


# 自治体 1 件分だけ OpenSearch index を増分更新するコマンドを作る。
def _index_update_command(doc_type: str, slug: str) -> list[str]:
    return _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "update",
        "--doc-type",
        doc_type,
        "--slug",
        slug,
    ]


# slug から target 定義を探し、見つからない場合も表示用の最低限情報を返す。
# 待ち行列のファイル名に使う doc_type。会議録は minutes、例規は reiki。
def _index_doc_type(kind: str) -> str:
    return "minutes" if kind in {"gijiroku", "minutes"} else "reiki"


def _target_by_slug(kind: str, slug: str) -> dict[str, object]:
    targets = gijiroku_targets.iter_gijiroku_targets() if kind == "gijiroku" else reiki_targets.iter_reiki_targets()
    for target in targets:
        if str(target.get("slug") or "").strip() == slug:
            return target
    return {"slug": slug, "code": "", "name": slug, "full_name": slug, "system_type": "", "source_url": ""}


# index 更新の進捗母数にする対象 document 数を数える。
def _index_document_total(kind: str, slug: str) -> int:
    try:
        from tools.search import build_opensearch_index as search_index

        slugs = {slug}
        if kind == "gijiroku":
            return max(0, int(search_index.count_minutes_documents_by_slug(slugs=slugs).get(slug, 0)))
        return max(0, int(search_index.count_reiki_documents_by_slug(slugs=slugs).get(slug, 0)))
    except Exception as exc:
        print(f"[CELERY] index document count failed for {kind} {slug}: {exc}", flush=True)
        return 0


# *_reflect タスクの実行中 state を作り、現在処理中の自治体を 1 件だけ表示する。
def _reflect_state(task_name: str, target: dict[str, object], *, progress_total: int = 0) -> dict[str, object]:
    # インデックス更新はスクレイピングとは別タスクとして表示する。
    # ここで *_reflect の state を作り、処理中自治体と document 件数を見えるようにする。
    state = batch_status.read_state(task_name)
    now = batch_status.now_text()
    if not state or not isinstance(state.get("items"), dict):
        state = batch_status.build_state(
            task_name,
            now.replace("-", "").replace(":", "").replace(" ", "_"),
            0,
            ROOT / "data" / "background_tasks" / f"{task_name}.csv",
            ROOT / "data" / "background_tasks",
        )
    state["task"] = task_name
    state["running"] = True
    state["running_label"] = "インデックス更新中"
    state["started_at"] = str(state.get("started_at") or now)
    state["last_started_at"] = now
    state["finished_at"] = ""
    state["worker_capacity"] = 1
    state["worker_active_count"] = 1
    state["worker_idle_count"] = 0
    state["index_capacity"] = 1
    state["index_active_count"] = 1
    state["index_idle_count"] = 0
    state["index_queue_count"] = 0
    items = state.setdefault("items", {})
    slug = str(target.get("slug") or "").strip()
    for existing_slug, item in list(items.items()):
        if existing_slug == slug or not isinstance(item, dict):
            continue
        if str(item.get("status") or "").strip() == "running":
            item["status"] = "failed"
            item["message"] = "新しいインデックス更新開始により終了扱い"
            item["finished_at"] = now
            item["updated_at"] = now
            item["returncode"] = -signal.SIGTERM
            item["pid"] = None
    items[slug] = {
        "slug": slug,
        "code": str(target.get("code") or "").strip(),
        "name": str(target.get("name") or "").strip(),
        "full_name": str(target.get("full_name") or "").strip(),
        "system_type": str(target.get("system_type") or "").strip(),
        "host": "",
        "source_url": str(target.get("source_url") or "").strip(),
        "status": "running",
        "message": "インデックス更新中",
        "started_at": now,
        "finished_at": "",
        "updated_at": now,
        "progress_updated_at": "",
        "returncode": None,
        "pid": None,
        "progress_current": 0 if progress_total > 0 else None,
        "progress_total": progress_total if progress_total > 0 else None,
        "progress_unit": "document" if progress_total > 0 else "",
    }
    batch_status.refresh_counts(state)
    return state


# index 更新コマンドを実行し、ログから進捗を読み取って *_reflect state を更新する。
def _run_index_update_command_with_status(kind: str, slug: str, state: dict[str, object], progress_total: int) -> int:
    # build_opensearch_index.py の [BULK]/[DONE] ログから投入済み件数を拾い、
    # 画面の「追加済 n/m 件」に反映する。
    command = _index_update_command("minutes" if kind == "gijiroku" else "reiki", slug)
    print(f"[CELERY] {kind} index update {slug}: {celery_runtime.command_text(command)}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **process_group_popen_kwargs(),
    )
    batch_status.update_item(state, slug, pid=int(process.pid))
    batch_status.write_state(f"{kind}_reflect", state)
    assert process.stdout is not None
    last_count = 0
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        print(line, flush=True)
        current = None
        match = INDEX_BULK_RE.match(line) or INDEX_DONE_RE.match(line)
        if match is not None:
            current = max(0, int(match.group("total") if "total" in match.groupdict() else match.group("count")))
        if current is not None:
            last_count = current
            batch_status.update_item(
                state,
                slug,
                message="インデックス更新中",
                progress_current=min(current, progress_total) if progress_total > 0 else current,
                progress_total=progress_total if progress_total > 0 else current,
                progress_unit="document",
            )
            batch_status.write_state(f"{kind}_reflect", state)
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"{kind} index update {slug} failed with exit code {returncode}")
    return last_count


# Celery の自治体別 index 更新タスク本体。開始・終了 state を必ず書く。
def _run_index_update_impl(kind: str, slug: str) -> None:
    slug = slug.strip()
    if slug == "":
        raise ValueError("slug is required")
    task_name = f"{kind}_reflect"
    target = _target_by_slug(kind, slug)
    progress_total = _index_document_total(kind, slug)
    state = _reflect_state(task_name, target, progress_total=progress_total)
    batch_status.write_state(task_name, state)
    ok = False
    message = ""
    indexed_count = 0
    try:
        indexed_count = _run_index_update_command_with_status(kind, slug, state, progress_total)
        # 検索に載る本文が 0 件でも失敗にしない。目次しか公開していない
        # 取得元では起こりうるし、失敗にすると毎回やり直しの対象になる。
        # 検索できる件数が 0 であることは収集状況ページ側に出る。
        ok = True
        message = (
            "インデックス更新完了"
            if indexed_count > 0
            else "検索に載る本文がありません（インデックス0件）"
        )
    except Exception as exc:
        message = str(exc)
        # 取得は成功しているのに公開へ載っていない。待ち行列へ残し、
        # 次の取得（最大 30 日後）を待たずに掃き取りが投げ直せるようにする。
        index_outbox.mark_failed(_index_doc_type(kind), slug, message)
        raise
    else:
        # 成功したときだけ待ち行列から消す。投げた時点では消さない。
        index_outbox.mark_done(_index_doc_type(kind), slug)
    finally:
        finished_at = batch_status.now_text()
        batch_status.update_item(
            state,
            slug,
            status="ok" if ok else "failed",
            message=message,
            finished_at=finished_at,
            returncode=0 if ok else -1,
            progress_current=indexed_count if ok else None,
            progress_total=indexed_count if ok else None,
            progress_unit="document" if ok else "",
        )
        state["running"] = False
        state["finished_at"] = finished_at
        state["last_finished_at"] = finished_at
        state["worker_active_count"] = 0
        state["worker_idle_count"] = 1
        state["index_active_count"] = 0
        state["index_idle_count"] = 1
        batch_status.refresh_counts(state)
        batch_status.write_state(task_name, state)
        batch_status.invalidate_runtime_caches(include_homepage_payload=True)


# 会議録 backfill タスクの実処理を起動する。
def _run_gijiroku_backfill_impl() -> None:
    _run_command("gijiroku backfill", _gijiroku_backfill_command())


# 会議録 scrape cycle の実処理を起動する。
def _run_gijiroku_scrape_impl(*, retry_failed: bool = False, name_filter: str = "") -> None:
    _run_command(
        "gijiroku scrape",
        _gijiroku_scrape_command(retry_failed=retry_failed, name_filter=name_filter),
    )


# TSV の URL/system_type 差分だけ robots.txt を再監査する。
def _audit_gijiroku_registry_changes() -> bool:
    if not celery_runtime.env_bool("SCRAPER_GIJIROKU_AUTO_AUDIT", True):
        return False
    try:
        summary = audit_minutes_robots.audit_registry(
            write=True,
            stale_only=True,
            workers=celery_runtime.env_int("SCRAPER_GIJIROKU_AUDIT_WORKERS", 4, minimum=1),
            timeout=celery_runtime.env_float("SCRAPER_GIJIROKU_AUDIT_TIMEOUT", 12.0, minimum=1.0),
            cache_path=audit_minutes_robots.DEFAULT_POLICY_CACHE,
        )
    except Exception as exc:
        # 変更行は loader 側でも review_required になるため取得されない。
        # 既存の enabled 対象の通常巡回まで止めないよう、監査失敗だけを記録する。
        print(f"[CELERY] gijiroku registry audit failed: {type(exc).__name__}: {exc}", flush=True)
        return False
    if summary.selected_rows:
        print(
            "[CELERY] gijiroku registry audit: "
            f"selected={summary.selected_rows} changed={summary.changed_rows} "
            f"enabled_changed={summary.enabled_targets_changed}",
            flush=True,
        )
    return summary.enabled_targets_changed


# 例規集 backfill タスクの実処理を起動する。
def _run_reiki_backfill_impl() -> None:
    _run_command("reiki backfill", _reiki_backfill_command())


# 例規集 scrape cycle の実処理を起動する。
def _run_reiki_scrape_impl(*, retry_failed: bool = False, name_filter: str = "") -> None:
    _run_command(
        "reiki scrape",
        _reiki_scrape_command(retry_failed=retry_failed, name_filter=name_filter),
    )


# stale running が残っている場合だけ、次回実行前にメタ情報を復旧する。
def _recover_stale_metadata(task_name: str) -> None:
    # 前回プロセスが異常終了して running=true だけ残った場合、次の投入前に実態へ寄せ直す。
    if not celery_runtime.task_is_stale_running(task_name):
        return
    _run_command(f"{task_name} metadata reconcile", _metadata_reconcile_command(task_name))


@app.task(name="deploy.scraper_runtime.celery.tasks.dispatch_gijiroku_cycle")
# 会議録の周期 scrape を投入するか判定し、必要なら run_gijiroku_cycle をキューへ送る。
def dispatch_gijiroku_cycle() -> dict[str, object]:
    # beat から呼ばれる入口。ここでは「投入するか」だけを決め、実処理は run_*_cycle へ渡す。
    enabled_targets_changed = _audit_gijiroku_registry_changes()
    schedule_seconds = celery_runtime.env_int("CELERY_GIJIROKU_SCHEDULE_SECONDS", 6 * 60 * 60, minimum=60)
    if not celery_runtime.cycle_is_due(
        "gijiroku",
        schedule_seconds,
        force_due=enabled_targets_changed,
    ):
        return {"enqueued": False, "task": "gijiroku", "reason": "not_due"}
    result = app.send_task("deploy.scraper_runtime.celery.tasks.run_gijiroku_cycle", queue="gijiroku")
    return {"enqueued": True, "task": "gijiroku", "task_id": result.id}


@app.task(name="deploy.scraper_runtime.celery.tasks.dispatch_reiki_cycle")
# 例規集の周期 scrape を投入するか判定し、必要なら run_reiki_cycle をキューへ送る。
def dispatch_reiki_cycle() -> dict[str, object]:
    # 会議録と同じ投入ゲート。優先度キューの中身は scrape_all_reiki.py 側で決める。
    schedule_seconds = celery_runtime.env_int("CELERY_REIKI_SCHEDULE_SECONDS", 6 * 60 * 60, minimum=60)
    if not celery_runtime.cycle_is_due("reiki", schedule_seconds):
        return {"enqueued": False, "task": "reiki", "reason": "not_due"}
    result = app.send_task("deploy.scraper_runtime.celery.tasks.run_reiki_cycle", queue="reiki")
    return {"enqueued": True, "task": "reiki", "task_id": result.id}


@app.task(name="deploy.scraper_runtime.celery.tasks.run_gijiroku_backfill")
# 手動/管理用の会議録 index 全再構築タスク。
def run_gijiroku_backfill() -> dict[str, object]:
    _run_gijiroku_backfill_impl()
    return {"ok": True, "task": "gijiroku_backfill"}


@app.task(bind=True, name="deploy.scraper_runtime.celery.tasks.run_gijiroku_cycle", max_retries=None)
# 会議録 scrape cycle を実行し、失敗時は Celery retry と retry marker を設定する。
def run_gijiroku_cycle(self, retry_failed: bool = False, name_filter: str = "") -> dict[str, object]:
    # 実際の会議録スクレイピングを起動する Celery タスク。
    # 失敗時は retry marker を置き、beat からの重複投入も同じ待ち時間だけ抑える。
    try:
        celery_runtime.clear_retry_marker("gijiroku")
        _recover_stale_metadata("gijiroku")
        _run_gijiroku_scrape_impl(retry_failed=bool(retry_failed), name_filter=str(name_filter or ""))
        celery_runtime.clear_retry_marker("gijiroku")
        return {"ok": True, "task": "gijiroku_cycle", "filter": str(name_filter or "")}
    except Exception as exc:
        delay_seconds = celery_runtime.env_int("SCRAPER_FAIL_SLEEP_SECONDS", 15 * 60, minimum=60)
        celery_runtime.set_retry_marker("gijiroku", delay_seconds)
        print(f"[CELERY] gijiroku cycle failed; retrying in {delay_seconds}s", flush=True)
        raise self.retry(exc=exc, countdown=delay_seconds)


@app.task(name="deploy.scraper_runtime.celery.tasks.run_gijiroku_rebuild")
# 会議録 index rebuild を Celery から起動する手動タスク。
def run_gijiroku_rebuild(name_filter: str = "") -> dict[str, object]:
    _run_command("gijiroku rebuild", _rebuild_command("minutes", name_filter))
    return {"ok": True, "task": "gijiroku_rebuild", "filter": name_filter}


@app.task(
    bind=True,
    name="deploy.scraper_runtime.celery.tasks.run_gijiroku_index_update",
    max_retries=INDEX_UPDATE_MAX_RETRIES,
)
# 会議録の自治体別 OpenSearch 増分更新タスク。
# 一時的な失敗はここで少しだけ待って投げ直す。決定的な失敗は待ち行列に残り、
# `sweep_index_outbox` が間隔を空けて拾い直す。
def run_gijiroku_index_update(self, slug: str) -> dict[str, object]:
    try:
        _run_index_update_impl("gijiroku", slug)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=_index_retry_countdown(self.request.retries))
    return {"ok": True, "task": "gijiroku_index_update", "slug": slug}


@app.task(name="deploy.scraper_runtime.celery.tasks.run_reiki_backfill")
# 手動/管理用の例規集 index 全再構築タスク。
def run_reiki_backfill() -> dict[str, object]:
    _run_reiki_backfill_impl()
    return {"ok": True, "task": "reiki_backfill"}


@app.task(bind=True, name="deploy.scraper_runtime.celery.tasks.run_reiki_cycle", max_retries=None)
# 例規集 scrape cycle を実行し、失敗時は Celery retry と retry marker を設定する。
def run_reiki_cycle(self, retry_failed: bool = False, name_filter: str = "") -> dict[str, object]:
    # 実際の例規集スクレイピングを起動する Celery タスク。
    # 会議録と同じ失敗時クールダウンで、連続失敗時の負荷を抑える。
    try:
        celery_runtime.clear_retry_marker("reiki")
        _recover_stale_metadata("reiki")
        _run_reiki_scrape_impl(retry_failed=bool(retry_failed), name_filter=str(name_filter or ""))
        celery_runtime.clear_retry_marker("reiki")
        return {"ok": True, "task": "reiki_cycle", "filter": str(name_filter or "")}
    except Exception as exc:
        delay_seconds = celery_runtime.env_int("SCRAPER_FAIL_SLEEP_SECONDS", 15 * 60, minimum=60)
        celery_runtime.set_retry_marker("reiki", delay_seconds)
        print(f"[CELERY] reiki cycle failed; retrying in {delay_seconds}s", flush=True)
        raise self.retry(exc=exc, countdown=delay_seconds)


@app.task(name="deploy.scraper_runtime.celery.tasks.run_reiki_rebuild")
# 例規集 index rebuild を Celery から起動する手動タスク。
def run_reiki_rebuild(name_filter: str = "") -> dict[str, object]:
    _run_command("reiki rebuild", _rebuild_command("reiki", name_filter))
    return {"ok": True, "task": "reiki_rebuild", "filter": name_filter}


@app.task(
    bind=True,
    name="deploy.scraper_runtime.celery.tasks.run_reiki_index_update",
    max_retries=INDEX_UPDATE_MAX_RETRIES,
)
# 例規集の自治体別 OpenSearch 増分更新タスク。
def run_reiki_index_update(self, slug: str) -> dict[str, object]:
    try:
        _run_index_update_impl("reiki", slug)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=_index_retry_countdown(self.request.retries))
    return {"ok": True, "task": "reiki_index_update", "slug": slug}


# 待ち行列に残っている自治体を投げ直す。**取得のやり直しを待たない。**
#
# celery の retry はメッセージが生きている間しか効かない。worker が強制終了
# されたり、メッセージそのものが失われたりすると、失敗の記録すら残らない。
# 待ち行列は消えない場所にあるので、そこから拾い直すのがこの掃き取りである。
@app.task(name="deploy.scraper_runtime.celery.tasks.sweep_index_outbox")
def sweep_index_outbox(limit: int = 0) -> dict[str, object]:
    requeued: dict[str, list[str]] = {}
    for kind, doc_type, queue, task_name in (
        ("gijiroku", "minutes", app_module.GIJIROKU_INDEX_QUEUE, "run_gijiroku_index_update"),
        ("reiki", "reiki", app_module.REIKI_INDEX_QUEUE, "run_reiki_index_update"),
    ):
        slugs = index_outbox.due_slugs(doc_type, limit=int(limit or 0))
        for slug in slugs:
            try:
                app.send_task(
                    f"deploy.scraper_runtime.celery.tasks.{task_name}",
                    kwargs={"slug": slug},
                    queue=queue,
                )
            except Exception as exc:
                index_outbox.mark_failed(doc_type, slug, f"再投入に失敗: {exc}")
                continue
            # 投げ直したことを記録して、次の掃き取りまで間を空ける。
            index_outbox.mark_attempted(doc_type, slug)
            requeued.setdefault(kind, []).append(slug)
        stuck = index_outbox.stuck_entries(doc_type)
        if stuck:
            print(
                f"[CELERY] {kind} index outbox: 試行上限に達した自治体 {len(stuck)} 件 "
                f"({', '.join(sorted(stuck)[:10])})",
                flush=True,
            )
    total = sum(len(items) for items in requeued.values())
    if total > 0:
        print(f"[CELERY] index outbox から {total} 件を投げ直しました: {requeued}", flush=True)
    return {"ok": True, "task": "sweep_index_outbox", "requeued": requeued}


# 索引側パーサの世代印が古い自治体を、取得のやり直しを待たずに積み直す。
#
# 保存済みのファイルはそのままに、解釈だけを直すことがある。公布日の見出し判定が
# それで、直しても再索引するまで公開検索は古いままになる。再取得は 30 日周期
# なので、放っておくと最大 30 日ずれる。群馬県の空公布日 710 件がこの形だった。
# ダウンロードはやり直さない。保存済みのファイルを読み直すだけである。
# index キューにこれ以上待っているなら、世代の追いつきは積まない。
STALE_SWEEP_QUEUE_LIMIT = celery_runtime.env_int("CELERY_STALE_SWEEP_QUEUE_LIMIT", 8, minimum=1)
# 索引に 1 件も無い自治体は少ないので、1 回に積む数も小さくてよい。
NEVER_INDEXED_SWEEP_LIMIT = celery_runtime.env_int("CELERY_NEVER_INDEXED_SWEEP_LIMIT", 5, minimum=1)


# broker の待ち行列の長さ。読めなければ None を返し、呼び出し側は制限しない。
def _queue_length(queue: str) -> int | None:
    try:
        with app.connection_or_acquire() as connection:
            return int(connection.default_channel.client.llen(queue))
    except Exception:
        return None


@app.task(name="deploy.scraper_runtime.celery.tasks.sweep_stale_parser_generation")
def sweep_stale_parser_generation(limit: int = 0) -> dict[str, object]:
    import sys as _sys

    search_dir = str(ROOT / "tools" / "search")
    if search_dir not in _sys.path:
        _sys.path.insert(0, search_dir)
    from opensearch_client import OpenSearchClient  # type: ignore
    from parser_generation import PARSER_GENERATION  # type: ignore
    import stale_generation  # type: ignore

    sweep_limit = int(limit or 0) or stale_generation.DEFAULT_SWEEP_LIMIT
    client = OpenSearchClient(
        celery_runtime.env_text("OPENSEARCH_URL", "http://localhost:9200"),
        user=celery_runtime.env_text("OPENSEARCH_USER", ""),
        password=celery_runtime.env_text("OPENSEARCH_PASSWORD", ""),
        insecure_dev=celery_runtime.env_text("OPENSEARCH_INSECURE_DEV", "").lower() in ("1", "true", "yes", "on"),
    )
    requeued: dict[str, list[str]] = {}
    for kind, doc_type, alias_env, alias_default, queue, task_name in (
        (
            "gijiroku",
            "minutes",
            "MIYABE_MINUTES_ALIAS",
            "miyabe-minutes-current",
            app_module.GIJIROKU_INDEX_QUEUE,
            "run_gijiroku_index_update",
        ),
        (
            "reiki",
            "reiki",
            "MIYABE_REIKI_ALIAS",
            "miyabe-reiki-current",
            app_module.REIKI_INDEX_QUEUE,
            "run_reiki_index_update",
        ),
    ):
        # 取得のあとの通常の索引更新が、追いつき分の後ろで待たされないようにする。
        # 積む速さは 20 件/時だが、大きい自治体から積むので捌く速さがそれに
        # 届かない。行列が伸びている間は積むのをやめて、次の周回に回す。
        waiting = _queue_length(queue)
        if waiting is not None and waiting >= STALE_SWEEP_QUEUE_LIMIT:
            print(
                f"[CELERY] {kind} index キューに {waiting} 件待っています。"
                "世代の追いつきは次の周回に回します。",
                flush=True,
            )
            continue
        alias = celery_runtime.env_text(alias_env, alias_default)
        # mapping に世代の項目が無いまま積むと、書いた世代が捨てられて
        # 同じ自治体を毎回積み直す。移行が済むまでは何もしない。
        if not stale_generation.generation_field_is_mapped(client, alias):
            print(
                f"[CELERY] {alias} の mapping に parser_generation がありません。"
                "移行が済むまで世代の掃き取りは行いません。",
                flush=True,
            )
            continue
        try:
            stale = stale_generation.stale_slugs(
                client, alias, doc_type, PARSER_GENERATION, limit=sweep_limit
            )
        except Exception as exc:
            # 索引が読めないのは掃き取り側の都合。次の周回でやり直す。
            print(f"[CELERY] {kind} 世代の照会に失敗しました: {exc}", flush=True)
            continue
        # 大きい自治体から積むので、1 回の間隔では捌けないことがある。
        # まだ待っている自治体をもう一度積むと、キューだけが伸びる。
        counts = dict(stale)
        pending = generation_sweep_state.filter_recently_queued(
            doc_type, [slug for slug, _ in stale]
        )
        queued_now: list[str] = []
        for slug in pending:
            try:
                app.send_task(
                    f"deploy.scraper_runtime.celery.tasks.{task_name}",
                    kwargs={"slug": slug},
                    queue=queue,
                )
            except Exception as exc:
                print(f"[CELERY] {kind} {slug} の再索引を積めませんでした: {exc}", flush=True)
                continue
            queued_now.append(slug)
            requeued.setdefault(kind, []).append(f"{slug}({counts.get(slug, 0)})")
        generation_sweep_state.mark_queued(doc_type, queued_now)
    total = sum(len(items) for items in requeued.values())
    if total > 0:
        print(
            f"[CELERY] 世代 {PARSER_GENERATION} より古い {total} 件を再索引に積みました: {requeued}",
            flush=True,
        )
    return {"ok": True, "task": "sweep_stale_parser_generation", "requeued": requeued}


# 取得できているのに索引へ 1 件も載っていない自治体を積み直す。
#
# 世代の掃き取りは「載っている文書の世代」を見るので、**1 件も載っていない
# 自治体は見えない**。鳥栖市は例規 588 件を取得済みなのに公開へ 1 件も出て
# おらず、待ち行列にも残っていなかった。取得は成功、索引は走らなかった、
# という形はどの経路にも引っかからない。
@app.task(name="deploy.scraper_runtime.celery.tasks.sweep_never_indexed")
def sweep_never_indexed(limit: int = 0) -> dict[str, object]:
    import sys as _sys

    search_dir = str(ROOT / "tools" / "search")
    if search_dir not in _sys.path:
        _sys.path.insert(0, search_dir)
    from opensearch_client import OpenSearchClient  # type: ignore
    import stale_generation  # type: ignore
    from tools.search import build_opensearch_index as search_index

    sweep_limit = int(limit or 0) or NEVER_INDEXED_SWEEP_LIMIT
    client = OpenSearchClient(
        celery_runtime.env_text("OPENSEARCH_URL", "http://localhost:9200"),
        user=celery_runtime.env_text("OPENSEARCH_USER", ""),
        password=celery_runtime.env_text("OPENSEARCH_PASSWORD", ""),
        insecure_dev=celery_runtime.env_text("OPENSEARCH_INSECURE_DEV", "").lower() in ("1", "true", "yes", "on"),
    )
    requeued: dict[str, list[str]] = {}
    for kind, doc_type, alias_env, alias_default, queue, task_name in (
        (
            "gijiroku",
            "minutes",
            "MIYABE_MINUTES_ALIAS",
            "miyabe-minutes-current",
            app_module.GIJIROKU_INDEX_QUEUE,
            "run_gijiroku_index_update",
        ),
        (
            "reiki",
            "reiki",
            "MIYABE_REIKI_ALIAS",
            "miyabe-reiki-current",
            app_module.REIKI_INDEX_QUEUE,
            "run_reiki_index_update",
        ),
    ):
        waiting = _queue_length(queue)
        if waiting is not None and waiting >= STALE_SWEEP_QUEUE_LIMIT:
            continue
        alias = celery_runtime.env_text(alias_env, alias_default)
        try:
            present = stale_generation.slugs_with_documents(client, alias, doc_type)
        except Exception as exc:
            print(f"[CELERY] {kind} 索引の自治体一覧を読めませんでした: {exc}", flush=True)
            continue
        targets = (
            gijiroku_targets.iter_gijiroku_targets()
            if kind == "gijiroku"
            else reiki_targets.iter_reiki_targets()
        )
        missing = {
            str(target.get("slug") or "").strip()
            for target in targets
            if str(target.get("slug") or "").strip()
        } - present
        if not missing:
            continue
        # 保存ファイルの数は、索引に無い自治体のぶんだけ数える。全国を毎回
        # 歩くと 100 万件超のファイルを触ることになる。
        try:
            if kind == "gijiroku":
                counts = search_index.count_minutes_documents_by_slug(slugs=missing)
            else:
                counts = search_index.count_reiki_documents_by_slug(slugs=missing)
        except Exception as exc:
            print(f"[CELERY] {kind} 保存件数を数えられませんでした: {exc}", flush=True)
            continue
        found = stale_generation.never_indexed_slugs(
            client,
            alias,
            doc_type,
            [(slug, int(counts.get(slug, 0))) for slug in sorted(missing)],
            limit=sweep_limit,
            present=present,
        )
        pending = generation_sweep_state.filter_recently_queued(
            f"{doc_type}_never", [slug for slug, _ in found]
        )
        counts_by_slug = dict(found)
        queued_now: list[str] = []
        for slug in pending:
            try:
                app.send_task(
                    f"deploy.scraper_runtime.celery.tasks.{task_name}",
                    kwargs={"slug": slug},
                    queue=queue,
                )
            except Exception as exc:
                print(f"[CELERY] {kind} {slug} の初回索引を積めませんでした: {exc}", flush=True)
                continue
            queued_now.append(slug)
            requeued.setdefault(kind, []).append(f"{slug}({counts_by_slug.get(slug, 0)})")
        generation_sweep_state.mark_queued(f"{doc_type}_never", queued_now)
    total = sum(len(items) for items in requeued.values())
    if total > 0:
        print(f"[CELERY] 索引に無い {total} 件を積みました: {requeued}", flush=True)
    return {"ok": True, "task": "sweep_never_indexed", "requeued": requeued}


# 題名の月日と、索引の日付が食い違う件数を自治体ごとに数える。
#
# 無作為に取った標本で見る。全件を歩くと重いし、傾向を見るには標本で足りる。
def _date_mismatch_samples(client, alias: str, doc_type: str) -> dict[str, tuple[int, int]]:
    import re as _re

    date_field = "held_on" if doc_type == "minutes" else "promulgated_on"
    month_day = _re.compile(r"(?<![0-9])([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日")
    body = {
        "size": 4000,
        "query": {
            "function_score": {
                "query": {"bool": {"filter": [
                    {"exists": {"field": date_field}},
                    {"exists": {"field": "title"}},
                ]}},
                "random_score": {"seed": 41, "field": "_seq_no"},
            }
        },
        "_source": ["slug", "title", date_field],
    }
    try:
        response = client.request("POST", f"/{alias}/_search", body=body)
    except Exception as exc:
        print(f"[CELERY] {alias} 日付の食い違いを見られませんでした: {exc}", flush=True)
        return {}
    found: dict[str, list[int]] = {}
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source") or {}
        matched = month_day.search(str(source.get("title") or ""))
        value = str(source.get(date_field) or "")[:10]
        if not matched or len(value) != 10:
            continue
        slug = str(source.get("slug") or "")
        counts = found.setdefault(slug, [0, 0])
        counts[0] += 1
        try:
            same = (int(value[5:7]), int(value[8:10])) == (
                int(matched.group(1)),
                int(matched.group(2)),
            )
        except ValueError:
            continue
        if not same:
            counts[1] += 1
    return {slug: (counts[0], counts[1]) for slug, counts in found.items()}


# 自治体マスタの全コード。台帳の分母はここから作る。取得先レジストリを
# 分母にすると、取得元を登録できていない自治体が数え上げから消える。
def _master_codes() -> set[str]:
    import csv as _csv

    path = ROOT / "data" / "municipalities" / "municipality_master.tsv"
    found: set[str] = set()
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in _csv.DictReader(handle, delimiter="	"):
                code = str(row.get("jis_code", "")).strip()
                if code:
                    found.add(code)
    except Exception as exc:
        print(f"[CELERY] 自治体マスタを読めませんでした: {exc}", flush=True)
        return set()
    return found


# 取得元が「N 件ある」と申告した数を、保存済みの走査記録から拾う。
#
# 取得元は自分が持っている数を出している（gijiroku.com の「1,607件の日程が
# ヒットしました」、legal-square の件数表示、d1-law OpenSearch の総数）。
# 公開件数同士を比べるより、この数と比べるほうが正しい。
def _declared_totals(kind: str, targets: list[dict[str, object]]) -> dict[str, int]:
    import json as _json
    from pathlib import Path as _Path

    found: dict[str, int] = {}
    for target in targets:
        slug = str(target.get("slug") or "").strip()
        if not slug:
            continue
        try:
            if kind == "gijiroku":
                state_path = _Path(str(target["downloads_dir"])).parent / "scrape_state.json"
                payload = _json.loads(state_path.read_text(encoding="utf-8"))
                coverage = payload.get("source_coverage") or {}
            else:
                coverage_path = _Path(str(target["source_dir"])).parent / "source_coverage.json"
                coverage = _json.loads(coverage_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        declared = int(coverage.get("declared_total") or 0)
        if declared > 0:
            found[slug] = declared
    return found


# 取りこぼしの台帳を書き出す。**直す仕組みではなく、見える仕組み。**
#
# 工程ごとの成功は既に記録している。足りないのは端から端までの答えで、
# 「この自治体は公開検索に出ているか」を誰も見ていなかった。能代市は取得の
# 失敗が記録され続けていたのに、何ヶ月も誰の目にも入らなかった。
@app.task(name="deploy.scraper_runtime.celery.tasks.write_coverage_ledger")
def write_coverage_ledger() -> dict[str, object]:
    import sys as _sys

    search_dir = str(ROOT / "tools" / "search")
    if search_dir not in _sys.path:
        _sys.path.insert(0, search_dir)
    from opensearch_client import OpenSearchClient  # type: ignore
    import stale_generation  # type: ignore
    from tools.search import build_opensearch_index as search_index
    from tools.tasks import coverage_ledger

    client = OpenSearchClient(
        celery_runtime.env_text("OPENSEARCH_URL", "http://localhost:9200"),
        user=celery_runtime.env_text("OPENSEARCH_USER", ""),
        password=celery_runtime.env_text("OPENSEARCH_PASSWORD", ""),
        insecure_dev=celery_runtime.env_text("OPENSEARCH_INSECURE_DEV", "").lower() in ("1", "true", "yes", "on"),
    )
    sections: list[dict[str, object]] = []
    # 数えられなかった区分。空のまま書くと「異常 0 件」に見える。
    failures: list[str] = []
    for kind, doc_type, alias_env, alias_default, iter_targets, counter in (
        (
            "gijiroku",
            "minutes",
            "MIYABE_MINUTES_ALIAS",
            "miyabe-minutes-current",
            gijiroku_targets.iter_gijiroku_targets,
            search_index.count_minutes_documents_by_slug,
        ),
        (
            "reiki",
            "reiki",
            "MIYABE_REIKI_ALIAS",
            "miyabe-reiki-current",
            reiki_targets.iter_reiki_targets,
            search_index.count_reiki_documents_by_slug,
        ),
    ):
        alias = celery_runtime.env_text(alias_env, alias_default)
        try:
            published = stale_generation.slugs_with_documents(client, alias, doc_type)
        except Exception as exc:
            # 数えられなかった区分を黙って飛ばすと、その区分は「異常 0 件」に
            # 見える。飛ばした事実を残し、最後に台帳の状態へ反映する。
            print(f"[CELERY] {kind} 索引の自治体一覧を読めませんでした: {exc}", flush=True)
            failures.append(f"{doc_type}: 索引の自治体一覧を読めなかった: {exc}")
            continue
        targets = list(iter_targets())
        try:
            section = coverage_ledger.build_section(
                doc_type,
                targets,
                published,
                lambda slugs, _counter=counter: _counter(slugs=slugs),
                # 取得元を登録できていない自治体も分母に入れる。数えなければ
                # 「問題が無い」ではなく「見ていない」である。
                master_codes=_master_codes(),
            )
        except Exception as exc:
            print(f"[CELERY] {kind} 台帳を組み立てられませんでした: {exc}", flush=True)
            failures.append(f"{doc_type}: 台帳を組み立てられなかった: {exc}")
            continue
        # 0 件でなくても取りこぼしは起きる。同じ系統の仲間と比べて極端に
        # 少ない自治体を挙げる。富士市は 1,666 件あるところを 14 件しか
        # 公開しておらず、0 件ではないので健全に見えていた。
        try:
            response = client.request(
                "POST",
                f"/{alias}/_search",
                body={"size": 0, "aggs": {"slugs": {"terms": {"field": "slug", "size": 5000}}}},
            )
            buckets = (response.get("aggregations") or {}).get("slugs", {}).get("buckets") or []
            counts_by_slug = {str(b["key"]): int(b["doc_count"]) for b in buckets}
            system_by_slug = {
                str(t.get("slug") or ""): str(t.get("system_type") or "?") for t in targets
            }
            section["thin_rows"] = coverage_ledger.thin_slugs(counts_by_slug, system_by_slug)
            section["thin"] = len(section["thin_rows"])
            # 取得元が申告した母数と比べる。**これが本来の指標。**仲間の
            # 中央値は、母数が読めない取得元のための最後の網でしかない。
            declared_by_slug = _declared_totals(kind, targets)
            section["shortfall_rows"] = coverage_ledger.declared_shortfall(
                declared_by_slug, counts_by_slug
            )
            section["shortfall"] = len(section["shortfall_rows"])
            section["declared_known"] = len(declared_by_slug)
            # 題名の月日と日付が食い違う自治体。件数だけを見ていると、
            # 空欄ではなく「もっともらしく誤る」形が見えない。
            samples = _date_mismatch_samples(client, alias, doc_type)
            section["date_mismatch_rows"] = coverage_ledger.date_mismatch_rows(samples)
            section["date_mismatch"] = len(section["date_mismatch_rows"])
            # 本文がほとんど空の自治体。件数では出てこない。公開はされていて、
            # 中身だけが無い。
            empty_response = client.request(
                "POST",
                f"/{alias}/_search",
                body={
                    "size": 0,
                    "query": {"range": {"body_length": {"lt": coverage_ledger.EMPTY_BODY_LENGTH}}},
                    "aggs": {"slugs": {"terms": {"field": "slug", "size": 5000}}},
                },
            )
            empty_buckets = (
                (empty_response.get("aggregations") or {}).get("slugs", {}).get("buckets") or []
            )
            empty_by_slug = {str(b["key"]): int(b["doc_count"]) for b in empty_buckets}
            section["empty_body_rows"] = coverage_ledger.empty_body_rows(
                counts_by_slug, empty_by_slug
            )
            section["empty_body"] = len(section["empty_body_rows"])
        except Exception as exc:
            print(f"[CELERY] {kind} 件数の偏りを見られませんでした: {exc}", flush=True)
            # 見られなかったことを「偏り 0 件」と書かない。
            section["thin_rows"] = []
            section["thin"] = None
            section["shortfall_rows"] = []
            section["shortfall"] = None
            section["date_mismatch_rows"] = []
            section["date_mismatch"] = None
            section["empty_body_rows"] = []
            section["empty_body"] = None
            section["errors"] = [f"件数の偏りを見られなかった: {exc}"]
        sections.append(section)
        print(
            f"[CELERY] {kind}: マスタ {section['targets']} 件"
            f"（取得元を登録済み {section.get('configured', 0)} 件）のうち "
            f"公開 {section['published']} 件、未公開 {section['missing']} 件 "
            f"{section['reasons']}、申告母数に届かない {section.get('shortfall', 0)} 件"
            f"（母数が読めた {section.get('declared_known', 0)} 件）"
            f"、仲間より極端に少ない {section.get('thin', 0)} 件"
            f"、題名と日付が食い違う {section.get('date_mismatch', 0)} 件"
            f"、本文がほぼ空 {section.get('empty_body', 0)} 件",
            flush=True,
        )
    if sections:
        coverage_ledger.write_ledger(sections)
    status = coverage_ledger.measurement_status(sections)
    if status != coverage_ledger.MEASUREMENT_COMPLETE:
        # 台帳を数え切れなかった実行は失敗として扱う。成功として返すと、
        # 「異常が無い」と「数えられなかった」が同じ見え方になる。
        raise RuntimeError(
            f"取りこぼし台帳を数え切れませんでした（{status}）: {failures or '区分が欠けています'}"
        )
    return {
        "ok": True,
        "task": "write_coverage_ledger",
        "measurement_status": status,
        "sections": [
            {
                "doc_type": s["doc_type"],
                "missing": s["missing"],
                "reasons": s["reasons"],
                "thin": s.get("thin", 0),
                "shortfall": s.get("shortfall", 0),
                "declared_known": s.get("declared_known", 0),
                "date_mismatch": s.get("date_mismatch", 0),
                "empty_body": s.get("empty_body", 0),
            }
            for s in sections
        ],
    }
