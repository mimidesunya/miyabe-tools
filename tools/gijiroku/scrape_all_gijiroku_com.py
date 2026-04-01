#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
import gijiroku_targets


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="assembly_minutes_system_urls.tsv の gijiroku.com / voices 対象を一括スクレイピングします。"
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
        "--delay-seconds",
        type=float,
        default=1.5,
        help="各会議アクセス間の待機秒数",
    )
    parser.add_argument(
        "--delay-between-targets",
        type=float,
        default=2.0,
        help="自治体起動間の待機秒数",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=10_000,
        help="Playwright 操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="ダウンロード失敗時に会議詳細HTMLを保存する",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="ブラウザを表示して実行する",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=6,
        help="同時に走らせる自治体数",
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
        help="slug / code / name に部分一致する自治体だけを対象にする",
    )
    return parser


def batch_dir() -> Path:
    return gijiroku_targets.project_root() / "work" / "gijiroku" / "_gijiroku_com_batch"


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
    ]
    return any(keyword in value for value in haystacks)


def build_child_command(args: argparse.Namespace, scrape_script: Path, slug: str) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(scrape_script),
        "--slug",
        slug,
        "--ack-robots",
        "--delay-seconds",
        str(args.delay_seconds),
        "--timeout-ms",
        str(args.timeout_ms),
    ]
    if args.per_target_max_meetings > 0:
        cmd.extend(["--max-meetings", str(args.per_target_max_meetings)])
    if args.save_html:
        cmd.append("--save-html")
    if args.headful:
        cmd.append("--headful")
    return cmd


def tail_text_lines(path: Path, max_lines: int = 4, max_bytes: int = 8192) -> list[str]:
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
    scrape_script: Path,
    run_logs_dir: Path,
) -> dict:
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.log"
    stderr_path = run_logs_dir / f"{slug}.err.log"
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        build_child_command(args, scrape_script, slug),
        cwd=str(gijiroku_targets.project_root()),
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
    )

    return {
        "seq": seq,
        "target": target,
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
            f"  - {target['slug']} ({target['code']} {target['name']}) "
            f"pid={worker['process'].pid}: {summary}",
            flush=True,
        )


def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。", flush=True)
        return 2
    if args.parallel < 1:
        print("[ERROR] --parallel は 1 以上を指定してください。", flush=True)
        return 2

    targets = gijiroku_targets.iter_gijiroku_targets(
        expected_system="gijiroku.com",
        configured_only=args.configured_only,
    )
    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if not targets:
        print("[INFO] 対象自治体がありません。", flush=True)
        return 0

    run_id = now_ts()
    batch_root = batch_dir()
    summary_path = summary_output_path(run_id)
    run_logs_dir = logs_dir(run_id)
    batch_root.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    scrape_script = Path(__file__).with_name("scrape_gijiroku_com.py")
    print(f"[INFO] 対象自治体数: {len(targets)}", flush=True)
    print(f"[INFO] 並列数: {args.parallel}", flush=True)
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)
    print(f"[INFO] ログディレクトリ: {run_logs_dir}", flush=True)

    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slug",
                "code",
                "name",
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

        while pending_targets or active_workers:
            while pending_targets and len(active_workers) < args.parallel:
                target = pending_targets.pop(0)
                launched_count += 1
                worker = launch_worker(
                    target,
                    launched_count,
                    args=args,
                    scrape_script=scrape_script,
                    run_logs_dir=run_logs_dir,
                )
                active_workers.append(worker)
                print(
                    f"[START {launched_count}/{len(targets)}] {target['name']} "
                    f"({target['slug']}, {target['code']}) pid={worker['process'].pid}",
                    flush=True,
                )
                if pending_targets and len(active_workers) < args.parallel and args.delay_between_targets > 0:
                    time.sleep(args.delay_between_targets)

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
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"returncode={returncode} {summary}",
                    flush=True,
                )
                if returncode != 0:
                    print(f"[WARN] {target['slug']} は returncode={returncode} で終了しました。", flush=True)

            active_workers = still_running

            now = time.time()
            if active_workers and (last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds):
                print_status(active_workers, completed_count, len(targets))
                last_status_at = now

            if pending_targets or active_workers:
                time.sleep(1.0)

        for worker in active_workers:
            close_worker_streams(worker)

    print(f"[DONE] {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
