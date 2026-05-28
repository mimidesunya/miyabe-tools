from __future__ import annotations

import shlex
import signal
import subprocess
import time
import re
from pathlib import Path

from deploy.scraper_runtime.celery.app import app
from deploy.scraper_runtime.celery import runtime as celery_runtime
from tools.tasks.runner import process_group_popen_kwargs, terminate_process_group
from tools.tasks import status as batch_status
from tools.gijiroku import gijiroku_targets
from tools.reiki import reiki_targets


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
def _gijiroku_scrape_command() -> list[str]:
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
        ]
    )
    if not _scraper_build_search_index():
        command.append("--no-build-index")
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
def _reiki_scrape_command() -> list[str]:
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
def _run_index_update_command_with_status(kind: str, slug: str, state: dict[str, object], progress_total: int) -> None:
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
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        print(line, flush=True)
        current = None
        match = INDEX_BULK_RE.match(line) or INDEX_DONE_RE.match(line)
        if match is not None:
            current = max(0, int(match.group("total") if "total" in match.groupdict() else match.group("count")))
        if current is not None:
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
    try:
        _run_index_update_command_with_status(kind, slug, state, progress_total)
        ok = True
        message = "インデックス更新完了"
    except Exception as exc:
        message = str(exc)
        raise
    finally:
        finished_at = batch_status.now_text()
        batch_status.update_item(
            state,
            slug,
            status="ok" if ok else "failed",
            message=message,
            finished_at=finished_at,
            returncode=0 if ok else -1,
            progress_current=1 if ok else None,
            progress_total=1 if ok else None,
            progress_unit="municipality" if ok else "",
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
def _run_gijiroku_scrape_impl() -> None:
    _run_command("gijiroku scrape", _gijiroku_scrape_command())


# 例規集 backfill タスクの実処理を起動する。
def _run_reiki_backfill_impl() -> None:
    _run_command("reiki backfill", _reiki_backfill_command())


# 例規集 scrape cycle の実処理を起動する。
def _run_reiki_scrape_impl() -> None:
    _run_command("reiki scrape", _reiki_scrape_command())


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
    schedule_seconds = celery_runtime.env_int("CELERY_GIJIROKU_SCHEDULE_SECONDS", 6 * 60 * 60, minimum=60)
    if not celery_runtime.cycle_is_due("gijiroku", schedule_seconds):
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
def run_gijiroku_cycle(self) -> dict[str, object]:
    # 実際の会議録スクレイピングを起動する Celery タスク。
    # 失敗時は retry marker を置き、beat からの重複投入も同じ待ち時間だけ抑える。
    try:
        celery_runtime.clear_retry_marker("gijiroku")
        _recover_stale_metadata("gijiroku")
        _run_gijiroku_scrape_impl()
        celery_runtime.clear_retry_marker("gijiroku")
        return {"ok": True, "task": "gijiroku_cycle"}
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


@app.task(name="deploy.scraper_runtime.celery.tasks.run_gijiroku_index_update")
# 会議録の自治体別 OpenSearch 増分更新タスク。
def run_gijiroku_index_update(slug: str) -> dict[str, object]:
    _run_index_update_impl("gijiroku", slug)
    return {"ok": True, "task": "gijiroku_index_update", "slug": slug}


@app.task(name="deploy.scraper_runtime.celery.tasks.run_reiki_backfill")
# 手動/管理用の例規集 index 全再構築タスク。
def run_reiki_backfill() -> dict[str, object]:
    _run_reiki_backfill_impl()
    return {"ok": True, "task": "reiki_backfill"}


@app.task(bind=True, name="deploy.scraper_runtime.celery.tasks.run_reiki_cycle", max_retries=None)
# 例規集 scrape cycle を実行し、失敗時は Celery retry と retry marker を設定する。
def run_reiki_cycle(self) -> dict[str, object]:
    # 実際の例規集スクレイピングを起動する Celery タスク。
    # 会議録と同じ失敗時クールダウンで、連続失敗時の負荷を抑える。
    try:
        celery_runtime.clear_retry_marker("reiki")
        _recover_stale_metadata("reiki")
        _run_reiki_scrape_impl()
        celery_runtime.clear_retry_marker("reiki")
        return {"ok": True, "task": "reiki_cycle"}
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


@app.task(name="deploy.scraper_runtime.celery.tasks.run_reiki_index_update")
# 例規集の自治体別 OpenSearch 増分更新タスク。
def run_reiki_index_update(slug: str) -> dict[str, object]:
    _run_index_update_impl("reiki", slug)
    return {"ok": True, "task": "reiki_index_update", "slug": slug}
