"""会議録・例規集で共有する一括スクレイピングの実行ループ。

両バッチは「優先度順に子スクレイパを並列起動し、完了した自治体から
OpenSearch 増分更新を行い、background task state と CSV に結果を残す」
という同じ構造を持つ。分野ごとの差分は BatchSpec に集約し、
制御ループ本体はこの 1 ファイルだけで保守する。
"""

from __future__ import annotations

import argparse
import csv
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tools.gijiroku import build_locks
from tools.tasks import backfill as task_backfill
from tools.tasks import priority as scraping_priority
from tools.tasks import status as batch_status
from tools.tasks.runner import (
    PriorityTargetQueue,
    close_worker_streams,
    count_active_by_host,
    extract_warning_lines,
    extract_worker_progress_from_log,
    extract_worker_progress_from_state,
    install_stop_signal_handlers,
    now_ts,
    process_group_popen_kwargs,
    scrape_state_warning_lines,
    summarize_worker,
    tail_text_lines,
    target_host,
    target_matches,
    terminate_process_group,
)


# 子スクレイパ標準出力の [PROGRESS] 行だけを拾い、自治体単位の current/total へ反映する。
PROGRESS_RE = re.compile(
    r"^\[PROGRESS\]\s+unit=(?P<unit>[a-z_]+)\s+current=(?P<current>\d+)\s+total=(?P<total>\d+)\s*$"
)
INDEX_BULK_RE = re.compile(r"^\[BULK\]\s+.*\btotal=(?P<total>\d+)\b")
INDEX_DONE_RE = re.compile(r"^\[DONE\]\s+.*\bcount=(?P<count>\d+)\b")

# index 更新の build lock をこの秒数まで待ち、取れなければスキップして次回に回す。
INDEX_LOCK_WAIT_SECONDS = 600.0


@dataclass(frozen=True)
class BatchSpec:
    """分野ごとの差分だけを束ね、run_batch へ渡す設定。"""

    task_name: str  # background task 名 ("gijiroku" / "reiki")
    progress_unit: str  # 進捗表示の単位 ("meeting" / "ordinance")
    index_doc_type: str  # build_opensearch_index.py の --doc-type
    batch_dir: Path  # 集計 CSV・子プロセスログの置き場所
    project_root: Path  # 子プロセスの作業ディレクトリ
    priority: scraping_priority.PriorityCalculator
    # 自治体 1 件を取得する子スクレイパ起動コマンドを作る。
    build_child_command: Callable[[argparse.Namespace, dict], list[str]]
    # 子スクレイパが進捗を書く scrape_state.json のパスを返す。
    scrape_state_path: Callable[[dict], Path]
    # 保存済み成果物を数え、終了判定に使う実取得件数 (current, total) を返す。
    actual_scrape_progress: Callable[[dict], tuple[int, int]]
    # returncode=0 でも完了とみなせない場合のエラーメッセージを返す（空文字は完了）。
    scrape_completion_error: Callable[[dict, dict | None], str]
    # スクレイプ成功後の鮮度メタデータを成果物から推定する。
    target_freshness: Callable[[dict], dict[str, str]]
    # この実行でスクレイプ後の index 更新を行うか（--no-build-index 等の判定）。
    index_enabled: Callable[[argparse.Namespace], bool] = field(
        default=lambda args: not args.no_build_index
    )

    @property
    def lock_owner(self) -> str:
        return f"scrape_all_{self.task_name}"


# 両バッチで共通の CLI オプションを parser に追加する。
def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-targets",
        type=int,
        default=0,
        help="処理する自治体数の上限（0 は無制限）",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=6,
        help="同時に走らせる自治体数",
    )
    parser.add_argument(
        "--index-parallel",
        type=int,
        default=1,
        help="同時に走らせる OpenSearch 自治体別増分更新数",
    )
    parser.add_argument(
        "--per-host-parallel",
        type=int,
        default=1,
        help="同一ホストに対して同時に走らせる自治体数",
    )
    parser.add_argument(
        "--per-host-start-interval",
        type=float,
        default=2.0,
        help="同一ホストで次の自治体を起動するまでの最小待機秒数",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=5.0,
        help="進捗表示の更新間隔（秒）",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="slug / code / name / full_name に部分一致する自治体だけを対象にする",
    )
    parser.add_argument(
        "--python-command",
        default=sys.executable,
        help="Python 系子プロセス起動に使うコマンド",
    )
    parser.add_argument(
        "--no-build-index",
        action="store_true",
        help="スクレイプ完了後の OpenSearch 自治体別増分更新を行わない",
    )
    parser.add_argument(
        "--index-dispatch",
        choices=["inline", "celery"],
        default="inline",
        help="OpenSearch 増分更新の実行方法。remote では celery でスクレイピングと分離する。",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="対象自治体一覧だけ表示して終了する",
    )


# 共通 CLI オプションの値を検証し、問題があればエラーメッセージを返す。
def validate_common_args(args: argparse.Namespace) -> str:
    if args.parallel < 1:
        return "--parallel は 1 以上を指定してください。"
    if args.index_parallel < 1:
        return "--index-parallel は 1 以上を指定してください。"
    if args.per_host_parallel < 1:
        return "--per-host-parallel は 1 以上を指定してください。"
    return ""


# --filter の部分一致で対象自治体を絞り込む。
def filter_targets(targets: list[dict], keyword: str) -> list[dict]:
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        return targets
    return [target for target in targets if target_matches(target, keyword, extra_fields=("system_type",))]


# 画面表示用に、state 優先・ログ補助で worker の進捗を取り出す。
def extract_worker_progress_for_display(spec: BatchSpec, worker: dict) -> dict[str, object] | None:
    progress = extract_worker_progress_from_state(worker["state_path"], default_unit=spec.progress_unit)
    if progress is not None:
        return progress
    return extract_worker_progress_from_log(worker["stdout_path"], PROGRESS_RE)


def remove_stale_scrape_state(state_path: Path) -> None:
    """前回実行の scrape_state.json が今回の進捗として読まれないようにする。"""
    try:
        state_path.unlink(missing_ok=True)
    except Exception:
        pass


def preserve_previous_failed_items(status_state: dict, task_name: str) -> None:
    """今回の実行対象から外した失敗済み item を main state に残す。"""
    items = status_state.setdefault("items", {})
    if not isinstance(items, dict):
        return
    for slug, item in task_backfill.previous_failed_items(task_name).items():
        if slug not in items:
            items[slug] = item
    batch_status.refresh_counts(status_state)


# index 更新の進捗母数として、直前のスクレイピング総件数を返す。
def index_worker_total(spec: BatchSpec, index_worker: dict) -> int:
    # 実ファイル数の計測は queue_index_worker で 1 回だけ行い、ここではその値を使う。
    # ハートビートのたびにダウンロードディレクトリを全走査しないため。
    cached_total = int(index_worker.get("scrape_total") or 0)
    if cached_total > 0:
        return cached_total
    scrape_worker = index_worker.get("scrape_worker")
    if not isinstance(scrape_worker, dict):
        return 0
    progress = extract_worker_progress_for_display(spec, scrape_worker)
    if not isinstance(progress, dict):
        return 0
    return max(0, int(progress.get("progress_total") or progress.get("progress_current") or 0))


# OpenSearch 更新ログから、document 追加済み件数を表示用進捗に変換する。
def extract_index_progress_for_display(spec: BatchSpec, index_worker: dict) -> dict[str, object] | None:
    current: int | None = None
    for line in reversed(tail_text_lines(index_worker["stdout_path"], max_bytes=16_384)):
        stripped = line.strip()
        done_match = INDEX_DONE_RE.match(stripped)
        if done_match:
            current = int(done_match.group("count"))
            break
        bulk_match = INDEX_BULK_RE.match(stripped)
        if bulk_match:
            current = int(bulk_match.group("total"))
            break

    total = index_worker_total(spec, index_worker)
    if current is None and total <= 0:
        return None
    current = max(0, int(current or 0))
    if total <= 0:
        total = current
    return {
        "progress_current": min(current, total) if total > 0 else current,
        "progress_total": total,
        "progress_unit": "document",
    }


# index worker のログから、画面に出す短い作業メッセージを作る。
def summarize_index_worker(spec: BatchSpec, index_worker: dict) -> str:
    stderr_path = index_worker["stderr_path"]
    if stderr_path.exists() and stderr_path.stat().st_size > 0:
        return f"エラー出力あり {stderr_path.stat().st_size}バイト"

    progress = extract_index_progress_for_display(spec, index_worker)
    if progress is not None:
        current = int(progress["progress_current"])
        total = int(progress["progress_total"])
        if total > 0 and current >= total:
            return "検索可能状態へ反映中"
        return "インデックスへ追加中"

    for line in reversed(tail_text_lines(index_worker["stdout_path"], max_bytes=16_384)):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[DELETE] "):
            return "既存インデックスを整理中"
        if stripped.startswith("[PUBLISH] "):
            return "検索可能状態へ反映中"
        if stripped.startswith("[DONE] "):
            return "インデックス更新完了"
        if stripped.startswith("[BULK] "):
            return "インデックスへ追加中"
        if stripped.startswith("[INFO] "):
            return stripped[7:].strip() or "インデックス更新中"
        return "インデックス更新中"
    return "インデックス更新を起動中"


# 自治体 1 件のスクレイピング子プロセスを起動し、worker 辞書を返す。
def launch_worker(
    spec: BatchSpec,
    target: dict,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict:
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.log"
    stderr_path = run_logs_dir / f"{slug}.err.log"
    state_path = spec.scrape_state_path(target)
    remove_stale_scrape_state(state_path)

    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = None
    try:
        stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")
        process = subprocess.Popen(
            spec.build_child_command(args, target),
            cwd=str(spec.project_root),
            stdout=stdout_handle,
            stderr=stderr_handle,
            **process_group_popen_kwargs(),
        )
    except Exception:
        # 起動に失敗したらログハンドルを残さない。結果記録は呼び出し側が行う。
        for handle in (stdout_handle, stderr_handle):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        raise

    return {
        "target": target,
        "host": target_host(target),
        "process": process,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "stdout_handle": stdout_handle,
        "stderr_handle": stderr_handle,
        "started_at": batch_status.now_text(),
        "state_path": state_path,
    }


# 自治体 1 件分の OpenSearch index 更新コマンドを作る。
def build_index_command(spec: BatchSpec, args: argparse.Namespace, target: dict) -> list[str]:
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            str(Path("tools") / "search" / "build_opensearch_index.py"),
            "--mode",
            "update",
            "--doc-type",
            spec.index_doc_type,
            "--slug",
            str(target["slug"]),
        ]
    )
    return cmd


# Celery の index キューへ自治体別更新タスクを投入する。
def enqueue_index_update_task(spec: BatchSpec, target: dict) -> str:
    from deploy.scraper_runtime.celery import app as celery_app

    queue = getattr(celery_app, f"{spec.task_name.upper()}_INDEX_QUEUE")
    result = celery_app.app.send_task(
        f"deploy.scraper_runtime.celery.tasks.run_{spec.task_name}_index_update",
        kwargs={"slug": str(target["slug"])},
        queue=queue,
    )
    return str(result.id)


# inline index 更新待ちにするため、完了済み scrape worker を index キュー項目へ包む。
def queue_index_worker(spec: BatchSpec, scrape_worker: dict, scrape_returncode: int) -> dict:
    # 進捗表示の母数になる実取得総数は、ここで一度だけ数えて以後使い回す。
    try:
        _, scrape_total = spec.actual_scrape_progress(scrape_worker["target"])
    except Exception:
        scrape_total = 0
    return {
        "target": scrape_worker["target"],
        "host": str(scrape_worker["host"]),
        "scrape_worker": scrape_worker,
        "scrape_returncode": int(scrape_returncode),
        "scrape_total": max(0, int(scrape_total)),
        "queued_at": batch_status.now_text(),
        "deadline_at": time.time() + INDEX_LOCK_WAIT_SECONDS,
    }


# inline 実行用の index worker を起動する。ロック取得できなければ None を返す。
def launch_index_worker(
    spec: BatchSpec,
    queued_worker: dict,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict | None:
    target = queued_worker["target"]
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.index.log"
    stderr_path = run_logs_dir / f"{slug}.index.err.log"
    lock_path = build_locks.acquire_build_lock(
        slug,
        owner=spec.lock_owner,
        wait_seconds=0.0,
    )
    if lock_path is None:
        return None

    stdout_handle = None
    stderr_handle = None
    try:
        stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
        stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")
        process = subprocess.Popen(
            build_index_command(spec, args, target),
            cwd=str(spec.project_root),
            stdout=stdout_handle,
            stderr=stderr_handle,
            **process_group_popen_kwargs(),
        )
        return {
            "target": target,
            "host": str(queued_worker["host"]),
            "scrape_worker": queued_worker["scrape_worker"],
            "scrape_returncode": int(queued_worker["scrape_returncode"]),
            "scrape_total": int(queued_worker.get("scrape_total") or 0),
            "process": process,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "stdout_handle": stdout_handle,
            "stderr_handle": stderr_handle,
            "started_at": batch_status.now_text(),
            "lock_path": lock_path,
        }
    except Exception:
        for handle in (stdout_handle, stderr_handle):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        build_locks.release_build_lock(lock_path)
        raise


# index worker のログを閉じ、取得していた build lock を解放する。
def close_index_worker(index_worker: dict) -> None:
    close_worker_streams(index_worker)
    lock_path = index_worker.get("lock_path")
    if isinstance(lock_path, Path):
        build_locks.release_build_lock(lock_path)
        index_worker["lock_path"] = None


# 自治体 1 件の最終結果を CSV と task state に書き込む。
def record_target_result(
    spec: BatchSpec,
    writer,
    handle,
    *,
    status_state: dict,
    target: dict,
    host: str,
    overall_status: str,
    overall_returncode: int,
    scrape_returncode: int,
    index_status: str,
    index_returncode: int | str,
    started_at: str,
    finished_at: str,
    stdout_log: str,
    stderr_log: str,
    index_stdout_log: str,
    index_stderr_log: str,
    message: str,
    progress: dict[str, object] | None = None,
) -> None:
    # 1 自治体の終了結果を CSV と background_tasks JSON の両方へ反映する。
    # ここで警告・取得件数・鮮度メタデータをまとめ、画面表示の元データを確定する。
    writer.writerow(
        {
            "slug": str(target["slug"]),
            "code": str(target["code"]),
            "name": str(target["name"]),
            "system_type": str(target["system_type"]),
            "host": host,
            "source_url": str(target["source_url"]),
            "status": overall_status,
            "returncode": overall_returncode,
            "scrape_returncode": scrape_returncode,
            "index_status": index_status,
            "index_returncode": index_returncode,
            "started_at": started_at,
            "finished_at": finished_at,
            "stdout_log": stdout_log,
            "stderr_log": stderr_log,
            "index_stdout_log": index_stdout_log,
            "index_stderr_log": index_stderr_log,
        }
    )
    handle.flush()
    update_kwargs: dict[str, object] = {
        "status": overall_status,
        "message": message,
        "finished_at": finished_at,
        "returncode": int(overall_returncode),
    }
    if started_at:
        update_kwargs["started_at"] = started_at
    if progress is not None:
        update_kwargs.update(progress)
    warning_lines = extract_warning_lines(
        Path(stderr_log),
        Path(index_stderr_log),
        Path(stdout_log),
        Path(index_stdout_log),
    )
    for line in scrape_state_warning_lines(spec.scrape_state_path(target)):
        if line not in warning_lines:
            warning_lines.append(line)
    latest_freshness = (
        spec.target_freshness(target)
        if overall_status == "ok"
        else {
            "freshness_date": str(target.get("freshness_date", "") or ""),
            "freshness_basis": str(target.get("freshness_basis", "") or ""),
        }
    )
    extra_fields: dict[str, object] = {
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "index_stdout_log": index_stdout_log,
        "index_stderr_log": index_stderr_log,
        "scrape_returncode": int(scrape_returncode),
        "index_status": str(index_status),
        "index_returncode": index_returncode,
        "warning_count": len(warning_lines),
        "warning_lines": warning_lines,
        "freshness_date": latest_freshness["freshness_date"],
        "freshness_basis": latest_freshness["freshness_basis"],
    }
    if overall_status == "ok":
        extra_fields["last_checked_at"] = finished_at
    update_kwargs["extra_fields"] = extra_fields
    batch_status.update_item(
        status_state,
        str(target["slug"]),
        **update_kwargs,
    )
    batch_status.write_state(spec.task_name, status_state)
    if overall_status == "ok" and progress is not None:
        sync_success_snapshot(
            spec,
            target=target,
            host=host,
            finished_at=finished_at,
            message=message,
            progress=progress,
            extra_fields=extra_fields,
        )


# 成功結果を snapshot state に保存し、次回の優先度計算で参照できるようにする。
def sync_success_snapshot(
    spec: BatchSpec,
    *,
    target: dict,
    host: str,
    finished_at: str,
    message: str,
    progress: dict[str, object],
    extra_fields: dict[str, object],
) -> None:
    # 成功した自治体だけを snapshot に残す。
    # 次回バッチが途中で止まっても、前回成功した取得件数を優先度計算に使えるようにする。
    snapshot_task = f"{spec.task_name}_snapshot"
    snapshot_state = batch_status.read_state(snapshot_task)
    if not isinstance(snapshot_state.get("items"), dict):
        snapshot_state = batch_status.build_state(
            snapshot_task,
            run_id=f"incremental_{now_ts()}",
            total_count=0,
            summary_path=Path(""),
            log_dir=Path(""),
        )
        snapshot_state["running"] = False
        snapshot_state["started_at"] = ""
        snapshot_state["finished_at"] = ""
        snapshot_state["last_finished_at"] = ""

    slug = str(target["slug"])
    if slug not in snapshot_state.setdefault("items", {}):
        batch_status.register_target(snapshot_state, target, host)

    batch_status.update_item(
        snapshot_state,
        slug,
        status="snapshot",
        message="前回成功結果",
        finished_at=finished_at,
        returncode=0,
        progress_current=progress.get("progress_current"),
        progress_total=progress.get("progress_total"),
        progress_unit=str(progress.get("progress_unit") or spec.progress_unit),
        extra_fields=extra_fields,
    )
    snapshot_state["running"] = False
    snapshot_state["active_count"] = 0
    snapshot_state["finished_at"] = finished_at
    snapshot_state["last_finished_at"] = finished_at
    batch_status.write_state(snapshot_task, snapshot_state)


# 他プロセスが index 更新中でスキップした場合も、scrape 成功として結果に記録する。
def record_busy_index_result(
    spec: BatchSpec,
    writer,
    handle,
    *,
    status_state: dict,
    queued_worker: dict,
    run_logs_dir: Path,
) -> None:
    target = queued_worker["target"]
    scrape_worker = queued_worker["scrape_worker"]
    progress = extract_worker_progress_for_display(spec, scrape_worker)
    stdout_path = run_logs_dir / f"{target['slug']}.index.log"
    stderr_path = run_logs_dir / f"{target['slug']}.index.err.log"
    skip_note = "別プロセスが検索インデックスを更新中のため、このバッチからのインデックス更新はスキップしました"
    stdout_path.write_text(skip_note + "。\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    summary = (
        f"{summarize_worker(scrape_worker['stdout_path'], scrape_worker['stderr_path'])} / {skip_note}"
    )
    finished_at = batch_status.now_text()
    record_target_result(
        spec,
        writer,
        handle,
        status_state=status_state,
        target=target,
        host=str(queued_worker["host"]),
        overall_status="ok",
        overall_returncode=int(queued_worker["scrape_returncode"]),
        scrape_returncode=int(queued_worker["scrape_returncode"]),
        index_status="busy",
        index_returncode=0,
        started_at=str(scrape_worker["started_at"]),
        finished_at=finished_at,
        stdout_log=str(scrape_worker["stdout_path"]),
        stderr_log=str(scrape_worker["stderr_path"]),
        index_stdout_log=str(stdout_path),
        index_stderr_log=str(stderr_path),
        message=summary,
        progress=progress,
    )
    batch_status.invalidate_runtime_caches()


# コンソール向けに、現在稼働中の scrape/index worker 一覧を出力する。
def print_status(
    active_workers: list[dict],
    active_index_workers: list[dict],
    queued_index_count: int,
    completed_count: int,
    total_count: int,
) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(
        f"[STATUS {stamp}] completed {completed_count}/{total_count}, "
        f"scrape_active {len(active_workers)}, index_active {len(active_index_workers)}, "
        f"index_queued {queued_index_count}",
        flush=True,
    )
    for label, workers in (("scrape", active_workers), ("index", active_index_workers)):
        for worker in workers:
            target = worker["target"]
            summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
            print(
                f"  - [{label}] {target['slug']} [{target['system_type']}] {worker['host']} "
                f"pid={worker['process'].pid}: {summary}",
                flush=True,
            )


# 稼働中 worker の進捗を state に反映し、heartbeat を更新する。
def refresh_active_worker_heartbeats(
    spec: BatchSpec,
    status_state: dict,
    active_workers: list[dict],
    active_index_workers: list[dict],
    pending_index_workers: list[dict],
    *,
    args: argparse.Namespace,
) -> None:
    if not active_workers and not active_index_workers:
        return
    for worker in active_workers:
        update_kwargs: dict[str, object] = {
            "message": summarize_worker(worker["stdout_path"], worker["stderr_path"]),
        }
        progress = extract_worker_progress_for_display(spec, worker)
        if progress is not None:
            update_kwargs.update(progress)
        batch_status.update_item(status_state, str(worker["target"]["slug"]), **update_kwargs)
    for worker in active_index_workers:
        update_kwargs = {
            "message": summarize_index_worker(spec, worker),
        }
        progress = extract_index_progress_for_display(spec, worker)
        if progress is not None:
            update_kwargs.update(progress)
        batch_status.update_item(status_state, str(worker["target"]["slug"]), **update_kwargs)
    batch_status.update_runtime_metrics(
        status_state,
        running_label="スクレイピング中",
        worker_capacity=args.parallel,
        worker_active_count=len(active_workers),
        index_capacity=args.index_parallel,
        index_active_count=len(active_index_workers),
        index_queue_count=len(pending_index_workers),
        per_host_capacity=args.per_host_parallel,
    )
    batch_status.write_state(spec.task_name, status_state)


# 実行せず、対象自治体と優先度情報だけを標準出力へ一覧表示する。
def list_targets(spec: BatchSpec, targets: list[dict]) -> None:
    print(f"[INFO] 対象自治体数: {len(targets)}")
    for target in targets:
        priority = spec.priority.target_priority_info(target)
        print(
            f"{target['slug']}\t{target['code']}\tscore={priority['priority_score']}\t{priority['priority_label']}\t"
            f"{priority['current_count']}/{priority['total_count']}\t{target['system_type']}\t"
            f"fresh={priority.get('freshness_date', '') or '-'}\tchecked={priority.get('last_checked_at', '') or '-'}\t"
            f"{target_host(target)}\t{target['name']}\t{target['source_url']}"
        )


# score が 0 より大きい自治体だけを抽出し、優先度順に並べる。
def select_runnable_targets(spec: BatchSpec, targets: list[dict]) -> list[dict]:
    skipped_zero_score = 0
    label_counts: dict[str, int] = {}
    runnable_targets: list[dict] = []
    for target in targets:
        try:
            priority = spec.priority.target_priority_info(target)
        except Exception:
            priority = {"priority_score": 2_000_000_000, "priority_label": "unknown_total"}
        label = str(priority.get("priority_label") or "unknown")
        label_counts[label] = label_counts.get(label, 0) + 1
        if int(priority.get("priority_score") or 0) <= 0:
            skipped_zero_score += 1
            continue
        runnable_targets.append(target)

    priority_summary = " ".join(f"{label}={count}" for label, count in sorted(label_counts.items()))
    print(
        "[INFO] 実行対象を priority queue で選定しました: "
        f"{priority_summary} skip_score_zero={skipped_zero_score}",
        flush=True,
    )
    return spec.priority.sort_targets_by_priority(runnable_targets)


# CSV の列構成。run_batch と record_target_result で共有する。
SUMMARY_FIELDNAMES = [
    "slug",
    "code",
    "name",
    "system_type",
    "host",
    "source_url",
    "status",
    "returncode",
    "scrape_returncode",
    "index_status",
    "index_returncode",
    "started_at",
    "finished_at",
    "stdout_log",
    "stderr_log",
    "index_stdout_log",
    "index_stderr_log",
]


# 一括スクレイピング全体の制御ループ。優先度選定〜結果記録までを共通で行う。
def run_batch(spec: BatchSpec, args: argparse.Namespace, targets: list[dict]) -> int:
    stop_controller = install_stop_signal_handlers()

    targets = filter_targets(targets, args.filter)
    targets = select_runnable_targets(spec, targets)
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if not targets:
        print("[INFO] 対象自治体がありません。", flush=True)
        return 0

    if args.list_targets:
        list_targets(spec, targets)
        return 0

    run_id = now_ts()
    summary_path = spec.batch_dir / f"run_{run_id}.csv"
    run_logs_dir = spec.batch_dir / f"logs_{run_id}"
    spec.batch_dir.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    index_enabled = spec.index_enabled(args)
    print(f"[INFO] 対象自治体数: {len(targets)}", flush=True)
    print(f"[INFO] 並列数: {args.parallel}", flush=True)
    print(
        f"[INFO] OpenSearch 自治体別増分更新: {'有効' if index_enabled else '無効'} "
        f"(並列数 {args.index_parallel})",
        flush=True,
    )
    print(f"[INFO] ホストごとの並列数: {args.per_host_parallel}", flush=True)
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)
    print(f"[INFO] ログディレクトリ: {run_logs_dir}", flush=True)

    status_state = batch_status.build_state(spec.task_name, run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, target_host(target))
    preserve_previous_failed_items(status_state, spec.task_name)

    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()

        # 実行順はここで一度だけ priority queue に載せ、以後は先頭から取り出す。
        pending_targets = PriorityTargetQueue(targets, spec.priority.priority_queue_key)
        active_workers: list[dict] = []
        pending_index_workers: list[dict] = []
        active_index_workers: list[dict] = []
        completed_count = 0
        launched_count = 0
        last_status_at = 0.0
        host_last_start_at: dict[str, float] = {}
        shutdown_started = False

        # スクレイピング枠と index 枠を分けて state に書き、画面でも別々に見せる。
        def write_status_state() -> None:
            batch_status.update_runtime_metrics(
                status_state,
                running_label="スクレイピング中",
                worker_capacity=args.parallel,
                worker_active_count=len(active_workers),
                index_capacity=args.index_parallel,
                index_active_count=len(active_index_workers),
                index_queue_count=len(pending_index_workers),
                per_host_capacity=args.per_host_parallel,
            )
            batch_status.write_state(spec.task_name, status_state)

        # 完了した scrape worker の結果を判定し、index 更新へ回すか結果を確定する。
        def handle_completed_scrape_worker(worker: dict, returncode: int) -> None:
            nonlocal completed_count
            target = worker["target"]
            summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
            progress = extract_worker_progress_for_display(spec, worker)
            validation_error = spec.scrape_completion_error(target, progress) if returncode == 0 else ""
            scrape_ok = returncode == 0 and validation_error == ""
            overall_status = "ok" if scrape_ok else "failed"
            overall_returncode = int(returncode if validation_error == "" else -1)
            if validation_error:
                summary = f"{summary} / {validation_error}"

            if scrape_ok and index_enabled and args.index_dispatch == "celery":
                finished_at = batch_status.now_text()
                try:
                    index_task_id = enqueue_index_update_task(spec, target)
                    index_status = "queued"
                    index_message = f"{summary} / インデックス更新を別キューへ投入"
                    index_returncode: int | str = ""
                except Exception as exc:
                    index_task_id = ""
                    index_status = "failed"
                    index_message = f"{summary} / インデックス更新投入失敗: {exc}"
                    index_returncode = -1
                record_target_result(
                    spec,
                    writer,
                    handle,
                    status_state=status_state,
                    target=target,
                    host=str(worker["host"]),
                    overall_status=overall_status,
                    overall_returncode=overall_returncode,
                    scrape_returncode=int(returncode),
                    index_status=index_status,
                    index_returncode=index_returncode,
                    started_at=str(worker["started_at"]),
                    finished_at=finished_at,
                    stdout_log=str(worker["stdout_path"]),
                    stderr_log=str(worker["stderr_path"]),
                    index_stdout_log="",
                    index_stderr_log="",
                    message=index_message,
                    progress=progress,
                )
                batch_status.invalidate_runtime_caches()
                completed_count += 1
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"[{target['system_type']}] returncode={overall_returncode} {summary} "
                    f"/ index_task={index_task_id or index_status}",
                    flush=True,
                )
                return

            if scrape_ok and index_enabled:
                pending_index_workers.append(queue_index_worker(spec, worker, returncode))
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    status="running",
                    message="インデックス待機中",
                    # 取得件数はここで打ち止めなので、index build 中は
                    # 「100% のまま固まった」表示を避ける。
                    progress_current=None,
                    progress_total=None,
                    progress_unit="",
                )
                write_status_state()
                print(f"[INDEX-QUEUE] {target['slug']} 検索インデックス更新を待機キューへ追加", flush=True)
                return

            finished_at = batch_status.now_text()
            record_target_result(
                spec,
                writer,
                handle,
                status_state=status_state,
                target=target,
                host=str(worker["host"]),
                overall_status=overall_status,
                overall_returncode=overall_returncode,
                scrape_returncode=int(returncode),
                index_status="skipped",
                index_returncode="",
                started_at=str(worker["started_at"]),
                finished_at=finished_at,
                stdout_log=str(worker["stdout_path"]),
                stderr_log=str(worker["stderr_path"]),
                index_stdout_log="",
                index_stderr_log="",
                message=summary,
                progress=progress,
            )
            if overall_returncode == 0:
                batch_status.invalidate_runtime_caches()
            completed_count += 1
            print(
                f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                f"[{target['system_type']}] returncode={overall_returncode} {summary}",
                flush=True,
            )
            if overall_returncode != 0:
                print(f"[WARN] {target['slug']} は returncode={overall_returncode} で終了しました。", flush=True)

        # 完了した index worker の結果を集約し、自治体の最終結果を確定する。
        def handle_completed_index_worker(index_worker: dict, returncode: int) -> None:
            nonlocal completed_count
            target = index_worker["target"]
            scrape_worker = index_worker["scrape_worker"]
            scrape_summary = summarize_worker(scrape_worker["stdout_path"], scrape_worker["stderr_path"])
            index_summary = summarize_worker(index_worker["stdout_path"], index_worker["stderr_path"])
            overall_status = "ok" if returncode == 0 else "failed"
            overall_returncode = int(index_worker["scrape_returncode"] if returncode == 0 else returncode)
            progress = extract_worker_progress_for_display(spec, scrape_worker)
            finished_at = batch_status.now_text()
            status_state["index_finished_at"] = finished_at
            record_target_result(
                spec,
                writer,
                handle,
                status_state=status_state,
                target=target,
                host=str(index_worker["host"]),
                overall_status=overall_status,
                overall_returncode=overall_returncode,
                scrape_returncode=int(index_worker["scrape_returncode"]),
                index_status="ok" if returncode == 0 else "failed",
                index_returncode=int(returncode),
                started_at=str(scrape_worker["started_at"]),
                finished_at=finished_at,
                stdout_log=str(scrape_worker["stdout_path"]),
                stderr_log=str(scrape_worker["stderr_path"]),
                index_stdout_log=str(index_worker["stdout_path"]),
                index_stderr_log=str(index_worker["stderr_path"]),
                message=f"{scrape_summary} / {index_summary}",
                progress=progress,
            )
            if returncode == 0:
                batch_status.invalidate_runtime_caches()
            completed_count += 1
            print(
                f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                f"[{target['system_type']}] returncode={overall_returncode} {scrape_summary} / {index_summary}",
                flush=True,
            )
            if returncode != 0:
                print(f"[WARN] {target['slug']} のインデックス更新は returncode={returncode} で終了しました。", flush=True)

        write_status_state()

        while pending_targets or active_workers or pending_index_workers or active_index_workers:
            now = time.time()
            made_progress = False

            if stop_controller.should_stop() and not shutdown_started:
                shutdown_started = True
                print("[STOP] 停止シグナルを受信しました。新規起動を止め、実行中の子プロセスを終了します。", flush=True)
                finished_at = batch_status.now_text()
                for target in pending_targets.remaining_targets():
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="failed",
                        message="停止により未実行",
                        finished_at=finished_at,
                        returncode=stop_controller.returncode(),
                    )
                pending_targets.clear()
                for queued_worker in pending_index_workers:
                    batch_status.update_item(
                        status_state,
                        str(queued_worker["target"]["slug"]),
                        status="failed",
                        message="停止によりインデックス更新を中断",
                        finished_at=finished_at,
                        returncode=stop_controller.returncode(),
                    )
                pending_index_workers.clear()
                write_status_state()
                for worker in active_index_workers:
                    terminate_process_group(worker["process"])
                for worker in active_workers:
                    terminate_process_group(worker["process"])
                made_progress = True

            # 完了済み子プロセスを回収する。
            completed_workers: list[tuple[dict, int]] = []
            still_running: list[dict] = []
            for worker in active_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_running.append(worker)
                    continue
                close_worker_streams(worker)
                completed_workers.append((worker, int(returncode)))
            active_workers = still_running

            completed_index_workers: list[tuple[dict, int]] = []
            still_index_running: list[dict] = []
            for worker in active_index_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_index_running.append(worker)
                    continue
                close_index_worker(worker)
                completed_index_workers.append((worker, int(returncode)))
            active_index_workers = still_index_running

            if completed_workers or completed_index_workers:
                made_progress = True
            for worker, returncode in completed_workers:
                handle_completed_scrape_worker(worker, returncode)
            for index_worker, returncode in completed_index_workers:
                handle_completed_index_worker(index_worker, returncode)

            # 空いた枠に、優先度順で起動可能な自治体を割り当てる。
            host_active_counts = count_active_by_host(active_workers)
            while pending_targets and len(active_workers) < args.parallel and not shutdown_started:
                # 同一ホストへの同時接続数と起動間隔だけを実行直前に判定する。
                # 優先度そのものはキュー内で維持される。
                def can_launch_target(target: dict) -> bool:
                    host = target_host(target)
                    if host_active_counts.get(host, 0) >= args.per_host_parallel:
                        return False
                    last_started_at = host_last_start_at.get(host, 0.0)
                    if last_started_at and now - last_started_at < args.per_host_start_interval:
                        return False
                    return True

                target = pending_targets.pop_runnable(can_launch_target)
                if target is None:
                    break

                host = target_host(target)
                launched_count += 1
                try:
                    worker = launch_worker(spec, target, args=args, run_logs_dir=run_logs_dir)
                except Exception as exc:
                    finished_at = batch_status.now_text()
                    stdout_path = run_logs_dir / f"{target['slug']}.log"
                    stderr_path = run_logs_dir / f"{target['slug']}.err.log"
                    error_message = f"起動失敗: {exc}"
                    try:
                        stderr_path.write_text(error_message + "\n", encoding="utf-8")
                    except Exception:
                        pass
                    record_target_result(
                        spec,
                        writer,
                        handle,
                        status_state=status_state,
                        target=target,
                        host=host,
                        overall_status="failed",
                        overall_returncode=-1,
                        scrape_returncode=-1,
                        index_status="skipped",
                        index_returncode="",
                        started_at="",
                        finished_at=finished_at,
                        stdout_log=str(stdout_path),
                        stderr_log=str(stderr_path),
                        index_stdout_log="",
                        index_stderr_log="",
                        message=error_message,
                    )
                    completed_count += 1
                    made_progress = True
                    print(f"[WARN] {target['slug']} の子プロセス起動に失敗しました: {exc}", flush=True)
                    now = time.time()
                    continue

                active_workers.append(worker)
                host_active_counts[host] = host_active_counts.get(host, 0) + 1
                host_last_start_at[host] = time.time()
                made_progress = True
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    status="running",
                    message="起動中",
                    started_at=str(worker["started_at"]),
                    pid=int(worker["process"].pid),
                )
                write_status_state()
                print(
                    f"[START {launched_count}/{len(targets)}] {target['name']} "
                    f"({target['slug']}, {target['system_type']}, {host}) pid={worker['process'].pid}",
                    flush=True,
                )
                now = time.time()

            # 待機中の index 更新を、build lock が取れたものから起動する。
            while pending_index_workers and len(active_index_workers) < args.index_parallel and not shutdown_started:
                launched_worker: dict | None = None
                for index, queued_worker in enumerate(pending_index_workers):
                    try:
                        candidate = launch_index_worker(spec, queued_worker, args=args, run_logs_dir=run_logs_dir)
                    except Exception as exc:
                        target = queued_worker["target"]
                        scrape_worker = queued_worker["scrape_worker"]
                        progress = extract_worker_progress_for_display(spec, scrape_worker)
                        finished_at = batch_status.now_text()
                        record_target_result(
                            spec,
                            writer,
                            handle,
                            status_state=status_state,
                            target=target,
                            host=str(queued_worker["host"]),
                            overall_status="failed",
                            overall_returncode=-1,
                            scrape_returncode=int(queued_worker["scrape_returncode"]),
                            index_status="failed",
                            index_returncode=-1,
                            started_at=str(scrape_worker["started_at"]),
                            finished_at=finished_at,
                            stdout_log=str(scrape_worker["stdout_path"]),
                            stderr_log=str(scrape_worker["stderr_path"]),
                            index_stdout_log="",
                            index_stderr_log="",
                            message=(
                                f"{summarize_worker(scrape_worker['stdout_path'], scrape_worker['stderr_path'])} / "
                                f"インデックス更新起動失敗: {exc}"
                            ),
                            progress=progress,
                        )
                        pending_index_workers.pop(index)
                        completed_count += 1
                        made_progress = True
                        print(f"[WARN] {target['slug']} のインデックス更新起動に失敗しました: {exc}", flush=True)
                        break

                    if candidate is None:
                        if time.time() >= float(queued_worker["deadline_at"]):
                            record_busy_index_result(
                                spec,
                                writer,
                                handle,
                                status_state=status_state,
                                queued_worker=queued_worker,
                                run_logs_dir=run_logs_dir,
                            )
                            pending_index_workers.pop(index)
                            completed_count += 1
                            made_progress = True
                            print(
                                f"[WARN] {queued_worker['target']['slug']} のインデックス更新はロック待ちのためスキップしました。",
                                flush=True,
                            )
                            break
                        continue

                    pending_index_workers.pop(index)
                    launched_worker = candidate
                    break

                if launched_worker is None:
                    break

                active_index_workers.append(launched_worker)
                made_progress = True
                status_state["index_started_at"] = batch_status.now_text()
                batch_status.update_item(
                    status_state,
                    str(launched_worker["target"]["slug"]),
                    status="running",
                    message=summarize_index_worker(spec, launched_worker),
                    pid=int(launched_worker["process"].pid),
                    **(extract_index_progress_for_display(spec, launched_worker) or {
                        "progress_current": 0,
                        "progress_total": index_worker_total(spec, launched_worker) or None,
                        "progress_unit": "document",
                    }),
                )
                write_status_state()
                print(
                    f"[INDEX-START] {launched_worker['target']['slug']} "
                    f"検索インデックスを更新中 pid={launched_worker['process'].pid}",
                    flush=True,
                )

            now = time.time()
            if (active_workers or active_index_workers) and (
                last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds
            ):
                refresh_active_worker_heartbeats(
                    spec,
                    status_state,
                    active_workers,
                    active_index_workers,
                    pending_index_workers,
                    args=args,
                )
                print_status(
                    active_workers,
                    active_index_workers,
                    len(pending_index_workers),
                    completed_count,
                    len(targets),
                )
                last_status_at = now

            if pending_targets or active_workers or pending_index_workers or active_index_workers:
                if not made_progress:
                    time.sleep(1.0)

        for worker in active_workers:
            close_worker_streams(worker)
        for worker in active_index_workers:
            close_index_worker(worker)

    batch_status.finish_batch(status_state)
    batch_status.update_runtime_metrics(
        status_state,
        running_label="スクレイピング中",
        worker_capacity=args.parallel,
        worker_active_count=0,
        index_capacity=args.index_parallel,
        index_active_count=0,
        index_queue_count=0,
        per_host_capacity=args.per_host_parallel,
    )
    batch_status.write_state(spec.task_name, status_state)
    print(f"[DONE] {summary_path}", flush=True)
    return 143 if stop_controller.should_stop() else 0
