#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import batch_status
import gijiroku_targets


SUPPORTED_SYSTEMS = {
    "gijiroku.com": "scrape_gijiroku_com.py",
    "kaigiroku.net": "scrape_kaigiroku_net.py",
    "dbsr": "scrape_dbsr.py",
}


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


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
        "--configured-only",
        action="store_true",
        help="config.json に登録済みの自治体だけを対象にする",
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
        default=4,
        help="同時に走らせる自治体数",
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
        default=5.0,
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


def target_matches(target: dict, keyword: str) -> bool:
    if keyword == "":
        return True
    haystacks = [
        str(target.get("slug", "")).lower(),
        str(target.get("code", "")).lower(),
        str(target.get("name", "")).lower(),
        str(target.get("full_name", "")).lower(),
        str(target.get("system_type", "")).lower(),
    ]
    return any(keyword in value for value in haystacks)


def parse_requested_systems(value: str) -> list[str]:
    systems = [item.strip() for item in str(value).split(",") if item.strip()]
    if not systems:
        return list(SUPPORTED_SYSTEMS.keys())
    unsupported = [system for system in systems if system not in SUPPORTED_SYSTEMS]
    if unsupported:
        raise ValueError(f"Unsupported system_type: {', '.join(unsupported)}")
    return systems


def target_host(target: dict) -> str:
    source_url = str(target.get("source_url", "")).strip()
    host = (urlsplit(source_url).hostname or "").strip().lower()
    return host or "unknown-host"


def child_script_path(system_type: str) -> str:
    script_name = SUPPORTED_SYSTEMS[system_type]
    return str(Path("tools") / "gijiroku" / script_name)


def build_child_command(args: argparse.Namespace, target: dict) -> list[str]:
    system_type = str(target["system_type"])
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
    if system_type == "kaigiroku.net" and args.per_target_max_years > 0:
        cmd.extend(["--max-years", str(args.per_target_max_years)])
    if args.save_html and system_type in {"gijiroku.com", "dbsr"}:
        cmd.append("--save-html")
    if args.save_debug_json and system_type == "kaigiroku.net":
        cmd.append("--save-debug-json")
    if args.headful:
        cmd.append("--headful")
    if args.no_resume:
        cmd.append("--no-resume")
    return cmd


def tail_text_lines(path: Path, max_bytes: int = 8192) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        read_size = min(size, max_bytes)
        handle.seek(-read_size, os.SEEK_END)
        chunk = handle.read(read_size)
    text = chunk.decode("utf-8", errors="replace")
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def summarize_worker(stdout_path: Path, stderr_path: Path) -> str:
    if stderr_path.exists() and stderr_path.stat().st_size > 0:
        return f"stderr {stderr_path.stat().st_size} bytes"

    lines = tail_text_lines(stdout_path)
    if not lines:
        return "starting..."

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[INFO] "):
            return stripped[7:]
        if stripped.startswith("[DONE] "):
            return stripped[7:]
        if stripped.startswith("[ERROR] "):
            return stripped
        if re.match(r"^\[\d+/\d+\]", stripped):
            return stripped
        return stripped
    return "starting..."


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
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def close_worker_streams(worker: dict) -> None:
    for key in ("stdout_handle", "stderr_handle"):
        handle = worker.get(key)
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:
            pass
        worker[key] = None


def print_status(active_workers: list[dict], completed_count: int, total_count: int) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[STATUS {stamp}] completed {completed_count}/{total_count}, active {len(active_workers)}", flush=True)
    for worker in active_workers:
        target = worker["target"]
        summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
        print(
            f"  - {target['slug']} [{target['system_type']}] {worker['host']} "
            f"pid={worker['process'].pid}: {summary}",
            flush=True,
        )


def count_active_by_host(active_workers: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for worker in active_workers:
        host = str(worker["host"])
        counts[host] = counts.get(host, 0) + 1
    return counts


def list_targets(targets: list[dict]) -> None:
    print(f"[INFO] 対象自治体数: {len(targets)}")
    for target in targets:
        print(
            f"{target['slug']}\t{target['code']}\t{target['system_type']}\t"
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
        for target in gijiroku_targets.iter_gijiroku_targets(configured_only=args.configured_only)
        if str(target.get("system_type", "")) in requested_systems
    ]

    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

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
    print(f"[INFO] ホストごとの並列数: {args.per_host_parallel}", flush=True)
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)
    print(f"[INFO] ログディレクトリ: {run_logs_dir}", flush=True)

    status_state = batch_status.build_state("gijiroku", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, target_host(target))
    batch_status.write_state("gijiroku", status_state)

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
                "started_at",
                "finished_at",
                "stdout_log",
                "stderr_log",
            ],
        )
        writer.writeheader()

        pending_targets = list(targets)
        active_workers: list[dict] = []
        completed_count = 0
        launched_count = 0
        last_status_at = 0.0
        host_last_start_at: dict[str, float] = {}

        while pending_targets or active_workers:
            now = time.time()
            host_active_counts = count_active_by_host(active_workers)
            launched_any = False

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
                worker = launch_worker(target, launched_count, args=args, run_logs_dir=run_logs_dir)
                active_workers.append(worker)
                host_active_counts[host] = host_active_counts.get(host, 0) + 1
                host_last_start_at[host] = time.time()
                launched_any = True
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    status="running",
                    message="起動中",
                    started_at=str(worker["started_at"]),
                    pid=int(worker["process"].pid),
                )
                batch_status.write_state("gijiroku", status_state)
                print(
                    f"[START {launched_count}/{len(targets)}] {target['name']} "
                    f"({target['slug']}, {target['system_type']}, {host}) pid={worker['process'].pid}",
                    flush=True,
                )
                now = time.time()

            still_running: list[dict] = []
            for worker in active_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_running.append(worker)
                    continue

                close_worker_streams(worker)
                completed_count += 1
                target = worker["target"]
                finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
                status = "ok" if returncode == 0 else "failed"
                writer.writerow(
                    {
                        "slug": str(target["slug"]),
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "system_type": str(target["system_type"]),
                        "host": str(worker["host"]),
                        "source_url": str(target["source_url"]),
                        "status": status,
                        "returncode": returncode,
                        "started_at": worker["started_at"],
                        "finished_at": finished_at,
                        "stdout_log": str(worker["stdout_path"]),
                        "stderr_log": str(worker["stderr_path"]),
                    }
                )
                handle.flush()
                summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    status=status,
                    message=summary,
                    finished_at=finished_at,
                    returncode=int(returncode),
                )
                batch_status.write_state("gijiroku", status_state)
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"[{target['system_type']}] returncode={returncode} {summary}",
                    flush=True,
                )
                if returncode != 0:
                    print(f"[WARN] {target['slug']} は returncode={returncode} で終了しました。", flush=True)

            active_workers = still_running

            now = time.time()
            if active_workers and (last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds):
                for worker in active_workers:
                    target = worker["target"]
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        message=summarize_worker(worker["stdout_path"], worker["stderr_path"]),
                    )
                batch_status.write_state("gijiroku", status_state)
                print_status(active_workers, completed_count, len(targets))
                last_status_at = now

            if pending_targets or active_workers:
                if not launched_any:
                    time.sleep(1.0)

        for worker in active_workers:
            close_worker_streams(worker)

    batch_status.finish_batch(status_state)
    batch_status.write_state("gijiroku", status_state)
    print(f"[DONE] {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
