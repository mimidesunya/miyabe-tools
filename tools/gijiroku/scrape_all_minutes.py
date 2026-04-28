#!/usr/bin/env python3
from __future__ import annotations

# system_type ごとの子スクレイパを束ね、全国一括実行と進捗記録を担当する。

import argparse
import csv
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import batch_status
from batch_runner_common import (
    close_worker_streams,
    count_active_by_host,
    extract_worker_progress_from_log as common_extract_worker_progress_from_log,
    extract_worker_progress_from_state as common_extract_worker_progress_from_state,
    now_ts,
    summarize_worker,
    target_host,
    target_matches,
)
import build_locks
import gijiroku_priority
import gijiroku_targets


SUPPORTED_SYSTEMS = {
    "gijiroku.com": "scrape_gijiroku_com.py",
    "kaigiroku.net": "scrape_kaigiroku_net.py",
    "dbsr": "scrape_dbsr.py",
    "kensakusystem": "scrape_kensakusystem.py",
    "kami-city-pdf": "scrape_kami_city_pdf.py",
}
SUPPORTED_INPUT_SYSTEMS = set(SUPPORTED_SYSTEMS.keys()) | {"voices", "db-search", "kaigiroku-indexphp"}
# 子スクレイパ標準出力の [PROGRESS] 行だけを拾い、自治体単位の current/total へ反映する。
PROGRESS_RE = re.compile(
    r"^\[PROGRESS\]\s+unit=(?P<unit>[a-z_]+)\s+current=(?P<current>\d+)\s+total=(?P<total>\d+)\s*$"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="assembly_minutes_system_urls.tsv の対応済み system_type をまとめてスクレイピングします。"
    )
    parser.add_argument(
        "--ack-robots",
        action="store_true",
        help="robots.txt・利用規約・許諾確認済みとして実行する",
    )
    parser.add_argument(
        "--systems",
        default=",".join(SUPPORTED_SYSTEMS.keys()),
        help="対象 system_type のカンマ区切り一覧",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        default=0,
        help="処理する自治体数の上限（0 は無制限）",
    )
    parser.add_argument(
        "--per-target-max-meetings",
        type=int,
        default=0,
        help="各自治体で処理する会議数上限（0 は無制限）",
    )
    parser.add_argument(
        "--per-target-max-years",
        type=int,
        default=0,
        help="kaigiroku.net 系の取得年数上限（0 は無制限）",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.5,
        help="各会議アクセス間の待機秒数",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=10_000,
        help="各スクレイパの操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="HTML 保存に対応する system_type で調査用 HTML を保存する",
    )
    parser.add_argument(
        "--save-debug-json",
        action="store_true",
        help="kaigiroku.net 系で調査用 JSON を保存する",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="ブラウザを表示して実行する",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="既存の保存結果を無視して最初から取り直す",
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
        help="同時に走らせる自治体インデックス更新数",
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
        help="子スクレイパ起動に使う Python コマンド",
    )
    parser.add_argument(
        "--no-build-index",
        action="store_true",
        help="自治体ごとのスクレイプ完了後に minutes.sqlite を更新しない",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="対象自治体一覧だけ表示して終了する",
    )
    return parser


def batch_dir() -> Path:
    return gijiroku_targets.project_root() / "work" / "gijiroku" / "_minutes_batch"


def summary_output_path(run_id: str) -> Path:
    return batch_dir() / f"run_{run_id}.csv"


def logs_dir(run_id: str) -> Path:
    return batch_dir() / f"logs_{run_id}"


def parse_requested_systems(value: str) -> list[str]:
    systems = [item.strip() for item in str(value).split(",") if item.strip()]
    if not systems:
        return list(SUPPORTED_SYSTEMS.keys())
    requested_systems: list[str] = []
    unsupported: list[str] = []
    for system in systems:
        if system not in SUPPORTED_INPUT_SYSTEMS:
            unsupported.append(system)
            continue
        if system not in requested_systems:
            requested_systems.append(system)
    if unsupported:
        raise ValueError(f"Unsupported system_type: {', '.join(unsupported)}")
    return requested_systems

def child_script_path(system_type: str) -> str:
    system_family = gijiroku_targets.canonical_minutes_system_type(system_type)
    script_name = SUPPORTED_SYSTEMS[system_family]
    return str(Path("tools") / "gijiroku" / script_name)


def build_child_command(args: argparse.Namespace, target: dict) -> list[str]:
    system_type = str(target["system_type"])
    system_family = str(target.get("system_family", "")).strip() or gijiroku_targets.canonical_minutes_system_type(system_type)
    slug = str(target["slug"])
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            child_script_path(system_type),
            "--slug",
            slug,
            "--ack-robots",
            "--delay-seconds",
            str(args.delay_seconds),
            "--timeout-ms",
            str(args.timeout_ms),
        ]
    )
    if args.per_target_max_meetings > 0:
        cmd.extend(["--max-meetings", str(args.per_target_max_meetings)])
    if system_family == "kaigiroku.net" and args.per_target_max_years > 0:
        cmd.extend(["--max-years", str(args.per_target_max_years)])
    if args.save_html and system_family in {"gijiroku.com", "dbsr", "kensakusystem", "kami-city-pdf"}:
        cmd.append("--save-html")
    if args.save_debug_json and system_family == "kaigiroku.net":
        cmd.append("--save-debug-json")
    if args.headful:
        cmd.append("--headful")
    if args.no_resume:
        cmd.append("--no-resume")
    return cmd


def extract_worker_progress(stdout_path: Path) -> dict[str, object] | None:
    return extract_worker_progress_from_log(stdout_path)


def extract_worker_progress_from_state(state_path: Path) -> dict[str, object] | None:
    return common_extract_worker_progress_from_state(state_path, default_unit="meeting")


def extract_worker_progress_from_log(stdout_path: Path) -> dict[str, object] | None:
    return common_extract_worker_progress_from_log(stdout_path, PROGRESS_RE)


def extract_worker_progress_for_display(worker: dict) -> dict[str, object] | None:
    state_path = worker.get("state_path")
    if isinstance(state_path, Path):
        progress = extract_worker_progress_from_state(state_path)
        if progress is not None:
            return progress
    return extract_worker_progress_from_log(worker["stdout_path"])


def launch_worker(
    target: dict,
    seq: int,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict:
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.log"
    stderr_path = run_logs_dir / f"{slug}.err.log"
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")

    process = subprocess.Popen(
        build_child_command(args, target),
        cwd=str(gijiroku_targets.project_root()),
        stdout=stdout_handle,
        stderr=stderr_handle,
    )

    return {
        "seq": seq,
        "target": target,
        "host": target_host(target),
        "process": process,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "stdout_handle": stdout_handle,
        "stderr_handle": stderr_handle,
        "started_at": batch_status.now_text(),
        "state_path": Path(target["work_dir"]) / "scrape_state.json",
    }


def build_index_command(args: argparse.Namespace, target: dict) -> list[str]:
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            str(Path("tools") / "gijiroku" / "build_minutes_index.py"),
            "--slug",
            str(target["slug"]),
            "--downloads-dir",
            str(target["downloads_dir"]),
            "--index-json",
            str(target["index_json_path"]),
            "--output-db",
            str(target["db_path"]),
        ]
    )
    return cmd

def queue_index_worker(scrape_worker: dict, scrape_returncode: int) -> dict:
    return {
        "target": scrape_worker["target"],
        "host": str(scrape_worker["host"]),
        "scrape_worker": scrape_worker,
        "scrape_returncode": int(scrape_returncode),
        "queued_at": batch_status.now_text(),
        "deadline_at": time.time() + 600.0,
    }


def launch_index_worker(
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
        owner="scrape_all_minutes",
        wait_seconds=0.0,
    )
    if lock_path is None:
        return None

    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")
    try:
        process = subprocess.Popen(
            build_index_command(args, target),
            cwd=str(gijiroku_targets.project_root()),
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        return {
            "target": target,
            "host": str(queued_worker["host"]),
            "scrape_worker": queued_worker["scrape_worker"],
            "scrape_returncode": int(queued_worker["scrape_returncode"]),
            "process": process,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "stdout_handle": stdout_handle,
            "stderr_handle": stderr_handle,
            "started_at": batch_status.now_text(),
            "lock_path": lock_path,
        }
    except Exception:
        try:
            stdout_handle.close()
        except Exception:
            pass
        try:
            stderr_handle.close()
        except Exception:
            pass
        build_locks.release_build_lock(lock_path)
        raise
 

def close_index_worker(index_worker: dict) -> None:
    close_worker_streams(index_worker)
    lock_path = index_worker.get("lock_path")
    if isinstance(lock_path, Path):
        build_locks.release_build_lock(lock_path)
        index_worker["lock_path"] = None


def record_busy_index_result(
    writer,
    handle,
    *,
    status_state: dict,
    task_name: str,
    queued_worker: dict,
    run_logs_dir: Path,
) -> None:
    target = queued_worker["target"]
    scrape_worker = queued_worker["scrape_worker"]
    progress = extract_worker_progress_for_display(scrape_worker)
    stdout_path = run_logs_dir / f"{target['slug']}.index.log"
    stderr_path = run_logs_dir / f"{target['slug']}.index.err.log"
    stdout_path.write_text(
        "別プロセスが minutes.sqlite を更新中のため、このバッチからのインデックス更新はスキップしました。\n",
        encoding="utf-8",
    )
    stderr_path.write_text("", encoding="utf-8")
    summary = (
        f"{summarize_worker(scrape_worker['stdout_path'], scrape_worker['stderr_path'])} / "
        "別プロセスが minutes.sqlite を更新中のため、このバッチからのインデックス更新はスキップしました"
    )
    finished_at = batch_status.now_text()
    record_target_result(
        writer,
        handle,
        status_state=status_state,
        task_name=task_name,
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
    for worker in active_workers:
        target = worker["target"]
        summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
        print(
            f"  - [scrape] {target['slug']} [{target['system_type']}] {worker['host']} "
            f"pid={worker['process'].pid}: {summary}",
            flush=True,
        )
    for worker in active_index_workers:
        target = worker["target"]
        summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
        print(
            f"  - [index] {target['slug']} [{target['system_type']}] {worker['host']} "
            f"pid={worker['process'].pid}: {summary}",
            flush=True,
        )

def refresh_active_worker_heartbeats(
    status_state: dict,
    task_name: str,
    active_workers: list[dict],
    active_index_workers: list[dict],
    pending_index_workers: list[dict],
    *,
    worker_capacity: int,
    index_capacity: int,
    per_host_capacity: int,
) -> None:
    if not active_workers and not active_index_workers:
        return
    for worker in active_workers:
        target = worker["target"]
        update_kwargs = {
            "message": summarize_worker(worker["stdout_path"], worker["stderr_path"]),
        }
        progress = extract_worker_progress_for_display(worker)
        if progress is not None:
            update_kwargs.update(progress)
        batch_status.update_item(
            status_state,
            str(target["slug"]),
            **update_kwargs,
        )
    for worker in active_index_workers:
        target = worker["target"]
        summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
        batch_status.update_item(
            status_state,
            str(target["slug"]),
            message=f"インデックス更新中: {summary}",
        )
    batch_status.update_runtime_metrics(
        status_state,
        running_label="スクレイピング中",
        worker_capacity=worker_capacity,
        worker_active_count=len(active_workers),
        index_capacity=index_capacity,
        index_active_count=len(active_index_workers),
        index_queue_count=len(pending_index_workers),
        per_host_capacity=per_host_capacity,
    )
    batch_status.write_state(task_name, status_state)


def record_target_result(
    writer,
    handle,
    *,
    status_state: dict,
    task_name: str,
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
    update_kwargs = {
        "status": overall_status,
        "message": message,
        "finished_at": finished_at,
        "returncode": int(overall_returncode),
    }
    if started_at:
        update_kwargs["started_at"] = started_at
    if progress is not None:
        update_kwargs.update(progress)
    batch_status.update_item(
        status_state,
        str(target["slug"]),
        **update_kwargs,
    )
    batch_status.write_state(task_name, status_state)


def list_targets(targets: list[dict]) -> None:
    print(f"[INFO] 対象自治体数: {len(targets)}")
    for target in targets:
        priority = gijiroku_priority.target_priority_info(target)
        print(
            f"{target['slug']}\t{target['code']}\t{priority['priority_label']}\t{target['system_type']}\t"
            f"{target_host(target)}\t{target['name']}\t{target['source_url']}"
        )


def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots and not args.list_targets:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。", flush=True)
        return 2
    if args.parallel < 1:
        print("[ERROR] --parallel は 1 以上を指定してください。", flush=True)
        return 2
    if args.index_parallel < 1:
        print("[ERROR] --index-parallel は 1 以上を指定してください。", flush=True)
        return 2
    if args.per_host_parallel < 1:
        print("[ERROR] --per-host-parallel は 1 以上を指定してください。", flush=True)
        return 2

    try:
        requested_systems = parse_requested_systems(args.systems)
    except ValueError as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 2

    targets = [
        target
        for target in gijiroku_targets.iter_gijiroku_targets()
        if (
            str(target.get("system_type", "")).strip() in requested_systems
            or (
                str(target.get("system_family", "")).strip()
                or gijiroku_targets.canonical_minutes_system_type(str(target.get("system_type", "")))
            )
            in requested_systems
        )
    ]

    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword, extra_fields=("system_type",))]

    targets = gijiroku_priority.sort_targets_by_priority(targets)
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if not targets:
        print("[INFO] 対象自治体がありません。", flush=True)
        return 0

    if args.list_targets:
        list_targets(targets)
        return 0

    run_id = now_ts()
    batch_root = batch_dir()
    summary_path = summary_output_path(run_id)
    run_logs_dir = logs_dir(run_id)
    batch_root.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 対象自治体数: {len(targets)}", flush=True)
    print(f"[INFO] 並列数: {args.parallel}", flush=True)
    print(f"[INFO] インデックス並列数: {args.index_parallel}", flush=True)
    print(f"[INFO] ホストごとの並列数: {args.per_host_parallel}", flush=True)
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)
    print(f"[INFO] ログディレクトリ: {run_logs_dir}", flush=True)

    status_state = batch_status.build_state("gijiroku", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, target_host(target))

    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
            ],
        )
        writer.writeheader()

        pending_targets = list(targets)
        active_workers: list[dict] = []
        pending_index_workers: list[dict] = []
        active_index_workers: list[dict] = []
        completed_count = 0
        launched_count = 0
        last_status_at = 0.0
        host_last_start_at: dict[str, float] = {}

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
            batch_status.write_state("gijiroku", status_state)

        write_status_state()

        while pending_targets or active_workers or pending_index_workers or active_index_workers:
            now = time.time()
            made_progress = False
            completed_workers: list[tuple[dict, int]] = []
            completed_index_workers: list[tuple[dict, int]] = []
            still_running: list[dict] = []
            still_index_running: list[dict] = []

            for worker in active_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_running.append(worker)
                    continue
                close_worker_streams(worker)
                completed_workers.append((worker, int(returncode)))

            active_workers = still_running
            if completed_workers:
                made_progress = True

            for worker in active_index_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_index_running.append(worker)
                    continue
                close_index_worker(worker)
                completed_index_workers.append((worker, int(returncode)))

            active_index_workers = still_index_running
            if completed_index_workers:
                made_progress = True

            for worker, returncode in completed_workers:
                target = worker["target"]
                scrape_status = "ok" if returncode == 0 else "failed"
                summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
                overall_status = scrape_status
                overall_returncode = int(returncode)
                index_status = "skipped"
                index_returncode = ""
                index_stdout_log = ""
                index_stderr_log = ""

                if returncode == 0 and not args.no_build_index:
                    pending_index_workers.append(queue_index_worker(worker, returncode))
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="running",
                        message="インデックス待機中",
                        # 子スクレイパの取得件数は完了済みなので、ここからは
                        # 「最後の 100% で止まった」ように見えないよう一旦外す。
                        progress_current=None,
                        progress_total=None,
                        progress_unit="",
                    )
                    write_status_state()
                    print(f"[INDEX-QUEUE] {target['slug']} minutes.sqlite の更新を待機キューへ追加", flush=True)
                    made_progress = True
                    continue

                progress = extract_worker_progress_for_display(worker)
                finished_at = batch_status.now_text()
                record_target_result(
                    writer,
                    handle,
                    status_state=status_state,
                    task_name="gijiroku",
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
                    index_stdout_log=index_stdout_log,
                    index_stderr_log=index_stderr_log,
                    message=summary,
                    progress=progress,
                )
                if overall_returncode == 0 and args.no_build_index:
                    batch_status.invalidate_runtime_caches()
                completed_count += 1
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"[{target['system_type']}] returncode={overall_returncode} {summary}",
                    flush=True,
                )
                if overall_returncode != 0:
                    print(f"[WARN] {target['slug']} は returncode={overall_returncode} で終了しました。", flush=True)

            for index_worker, returncode in completed_index_workers:
                target = index_worker["target"]
                scrape_worker = index_worker["scrape_worker"]
                scrape_summary = summarize_worker(scrape_worker["stdout_path"], scrape_worker["stderr_path"])
                index_summary = summarize_worker(index_worker["stdout_path"], index_worker["stderr_path"])
                overall_status = "ok" if returncode == 0 else "failed"
                overall_returncode = int(index_worker["scrape_returncode"] if returncode == 0 else returncode)
                progress = extract_worker_progress_for_display(scrape_worker)
                finished_at = batch_status.now_text()
                record_target_result(
                    writer,
                    handle,
                    status_state=status_state,
                    task_name="gijiroku",
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

            host_active_counts = count_active_by_host(active_workers)
            while pending_targets and len(active_workers) < args.parallel:
                launch_index = None
                for index, target in enumerate(pending_targets):
                    host = target_host(target)
                    if host_active_counts.get(host, 0) >= args.per_host_parallel:
                        continue
                    last_started_at = host_last_start_at.get(host, 0.0)
                    if last_started_at and now - last_started_at < args.per_host_start_interval:
                        continue
                    launch_index = index
                    break

                if launch_index is None:
                    break

                target = pending_targets.pop(launch_index)
                host = target_host(target)
                launched_count += 1
                try:
                    worker = launch_worker(target, launched_count, args=args, run_logs_dir=run_logs_dir)
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
                        writer,
                        handle,
                        status_state=status_state,
                        task_name="gijiroku",
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
                    print(
                        f"[WARN] {target['slug']} の子プロセス起動に失敗しました: {exc}",
                        flush=True,
                    )
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

            while pending_index_workers and len(active_index_workers) < args.index_parallel:
                launch_index = None
                launched_worker: dict | None = None
                for index, queued_worker in enumerate(pending_index_workers):
                    try:
                        candidate = launch_index_worker(queued_worker, args=args, run_logs_dir=run_logs_dir)
                    except Exception as exc:
                        target = queued_worker["target"]
                        scrape_worker = queued_worker["scrape_worker"]
                        progress = extract_worker_progress_for_display(scrape_worker)
                        finished_at = batch_status.now_text()
                        record_target_result(
                            writer,
                            handle,
                            status_state=status_state,
                            task_name="gijiroku",
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
                        print(
                            f"[WARN] {target['slug']} のインデックス更新起動に失敗しました: {exc}",
                            flush=True,
                        )
                        break

                    if candidate is None:
                        if time.time() >= float(queued_worker["deadline_at"]):
                            record_busy_index_result(
                                writer,
                                handle,
                                status_state=status_state,
                                task_name="gijiroku",
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

                    launch_index = index
                    launched_worker = candidate
                    break

                if launch_index is None or launched_worker is None:
                    break

                pending_index_workers.pop(launch_index)
                active_index_workers.append(launched_worker)
                made_progress = True
                batch_status.update_item(
                    status_state,
                    str(launched_worker["target"]["slug"]),
                    status="running",
                    message="インデックス更新中",
                    pid=int(launched_worker["process"].pid),
                    progress_current=None,
                    progress_total=None,
                    progress_unit="",
                )
                write_status_state()
                print(
                    f"[INDEX-START] {launched_worker['target']['slug']} "
                    f"minutes.sqlite を更新中 pid={launched_worker['process'].pid}",
                    flush=True,
                )

            now = time.time()
            if (active_workers or active_index_workers) and (
                last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds
            ):
                refresh_active_worker_heartbeats(
                    status_state,
                    "gijiroku",
                    active_workers,
                    active_index_workers,
                    pending_index_workers,
                    worker_capacity=args.parallel,
                    index_capacity=args.index_parallel,
                    per_host_capacity=args.per_host_parallel,
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
    batch_status.write_state("gijiroku", status_state)
    print(f"[DONE] {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
