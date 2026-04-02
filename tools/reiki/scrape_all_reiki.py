#!/usr/bin/env python3
from __future__ import annotations

# 例規集スクレイパを束ね、全国一括実行と進捗記録を担当する。

import argparse
import csv
import json
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
import reiki_priority
import reiki_targets


SUPPORTED_SYSTEMS = {
    "d1-law": ("python", "download_d1_law.py"),
    "taikei": ("php", "download_taikei.php"),
}
# 子スクレイパ標準出力の [PROGRESS] 行だけを拾い、自治体単位の current/total へ反映する。
PROGRESS_RE = re.compile(
    r"^\[PROGRESS\]\s+unit=(?P<unit>[a-z_]+)\s+current=(?P<current>\d+)\s+total=(?P<total>\d+)\s*$"
)


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="reiki_system_urls.tsv の対応済み system_type をまとめてスクレイピングします。"
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
        "--per-target-limit",
        type=int,
        default=0,
        help="taikei 系で各自治体から取得する件数上限（0 は無制限）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存ソースがあっても取り直す",
    )
    parser.add_argument(
        "--check-updates",
        action="store_true",
        help="既存条例も再取得して更新を確認する",
    )
    parser.add_argument(
        "--crawl-only",
        action="store_true",
        help="taikei 系では体系クロールだけ行い本文取得を省く",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=6,
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
        "--php-command",
        default="php",
        help="PHP 系子プロセス起動に使うコマンド",
    )
    parser.add_argument(
        "--no-build-index",
        action="store_true",
        help="自治体ごとのスクレイプ完了後に ordinances.sqlite を更新しない",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="対象自治体一覧だけ表示して終了する",
    )
    return parser


def batch_dir() -> Path:
    return reiki_targets.project_root() / "work" / "reiki" / "_reiki_batch"


def summary_output_path(run_id: str) -> Path:
    return batch_dir() / f"run_{run_id}.csv"


def logs_dir(run_id: str) -> Path:
    return batch_dir() / f"logs_{run_id}"


def parse_requested_systems(value: str) -> list[str]:
    systems = [item.strip() for item in str(value).split(",") if item.strip()]
    if not systems:
        return list(SUPPORTED_SYSTEMS.keys())
    unsupported = [system for system in systems if system not in SUPPORTED_SYSTEMS]
    if unsupported:
        raise ValueError(f"Unsupported system_type: {', '.join(unsupported)}")
    return systems


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


# 同一ホストへ過剰集中しないよう、source_url の host 単位で並列数を絞る。
def target_host(target: dict) -> str:
    source_url = str(target.get("source_url", "")).strip()
    host = (urlsplit(source_url).hostname or "").strip().lower()
    return host or "unknown-host"


def build_child_command(args: argparse.Namespace, target: dict) -> list[str]:
    system_type = str(target["system_type"])
    runner_kind, script_name = SUPPORTED_SYSTEMS[system_type]
    slug = str(target["slug"])
    script_path = str(Path("tools") / "reiki" / script_name)

    if runner_kind == "python":
        cmd = shlex.split(str(args.python_command))
        cmd.extend([script_path, "--slug", slug])
    else:
        cmd = shlex.split(str(args.php_command))
        cmd.extend(
            [
                script_path,
                "--slug",
                slug,
                "--code",
                str(target["code"]),
                "--name",
                str(target["name"]),
                "--source-url",
                str(target["source_url"]),
            ]
        )

    if args.force:
        cmd.append("--force")
    if args.check_updates:
        cmd.append("--check-updates")
    if system_type == "taikei" and args.crawl_only:
        cmd.append("--crawl-only")
    if system_type == "taikei" and args.per_target_limit > 0:
        cmd.append(f"--limit={args.per_target_limit}")
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
        if stripped.startswith("[PROGRESS] "):
            continue
        if re.match(r"^\[\d+/\d+\]", stripped):
            return stripped
        return stripped
    return "starting..."


def extract_worker_progress(stdout_path: Path) -> dict[str, object] | None:
    lines = tail_text_lines(stdout_path, max_bytes=16_384)
    for line in reversed(lines):
        match = PROGRESS_RE.match(line.strip())
        if not match:
            continue
        return {
            "progress_current": int(match.group("current")),
            "progress_total": int(match.group("total")),
            "progress_unit": match.group("unit"),
        }
    return None


def extract_worker_progress_from_state(state_path: Path) -> dict[str, object] | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    current = payload.get("progress_current")
    total = payload.get("progress_total")
    if current is None or total is None:
        return None

    try:
        progress_current = int(current)
        progress_total = int(total)
    except Exception:
        return None

    return {
        "progress_current": max(0, progress_current),
        "progress_total": max(0, progress_total),
        "progress_unit": str(payload.get("progress_unit") or "ordinance").strip() or "ordinance",
    }


def extract_worker_progress_for_display(worker: dict) -> dict[str, object] | None:
    # 子プロセスが逐次更新する state を優先し、古い実装との互換としてログ tail も残す。
    progress = extract_worker_progress_from_state(worker["state_path"])
    if progress is not None:
        return progress
    return extract_worker_progress(worker["stdout_path"])


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
        cwd=str(reiki_targets.project_root()),
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
        "state_path": Path(target["work_root"]) / "scrape_state.json",
    }


def build_index_command(args: argparse.Namespace, target: dict) -> list[str]:
    manifest_json = Path(target["work_root"]) / "source_manifest.json.gz"
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            str(Path("tools") / "reiki" / "build_ordinance_index.py"),
            "--slug",
            str(target["slug"]),
            "--clean-html-dir",
            str(target["html_dir"]),
            "--classification-dir",
            str(target["classification_dir"]),
            "--markdown-dir",
            str(target["markdown_dir"]),
            "--manifest-json",
            str(manifest_json),
            "--output-db",
            str(target["db_path"]),
        ]
    )
    return cmd


def run_index_builder(
    target: dict,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict:
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.index.log"
    stderr_path = run_logs_dir / f"{slug}.index.err.log"
    with stdout_path.open("w", encoding="utf-8", newline="") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8", newline=""
    ) as stderr_handle:
        result = subprocess.run(
            build_index_command(args, target),
            cwd=str(reiki_targets.project_root()),
            stdout=stdout_handle,
            stderr=stderr_handle,
        )

    return {
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": int(result.returncode),
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "summary": summarize_worker(stdout_path, stderr_path),
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
        priority = reiki_priority.target_priority_info(target)
        print(
            f"{target['slug']}\t{target['code']}\t{priority['priority_label']}\t{target['system_type']}\t"
            f"{target_host(target)}\t{target['name']}\t{target['source_url']}"
        )


def main() -> int:
    args = build_parser().parse_args()
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
        for target in reiki_targets.iter_reiki_targets()
        if str(target.get("system_type", "")) in requested_systems
    ]

    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

    targets = reiki_priority.sort_targets_by_priority(targets)
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

    status_state = batch_status.build_state("reiki", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, target_host(target))
    batch_status.write_state("reiki", status_state)

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
                batch_status.write_state("reiki", status_state)
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
                target = worker["target"]
                finished_at = batch_status.now_text()
                scrape_status = "ok" if returncode == 0 else "failed"
                summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
                overall_status = scrape_status
                overall_returncode = int(returncode)
                index_status = "skipped"
                index_returncode = ""
                index_stdout_log = ""
                index_stderr_log = ""

                if returncode == 0 and not args.no_build_index and not args.crawl_only:
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="running",
                        message="インデックス更新中",
                    )
                    batch_status.write_state("reiki", status_state)
                    print(f"[INDEX] {target['slug']} ordinances.sqlite を更新中", flush=True)
                    index_result = run_index_builder(target, args=args, run_logs_dir=run_logs_dir)
                    index_status = str(index_result["status"])
                    index_returncode = int(index_result["returncode"])
                    index_stdout_log = str(index_result["stdout_path"])
                    index_stderr_log = str(index_result["stderr_path"])
                    summary = f"{summary} / {index_result['summary']}"
                    if index_result["returncode"] != 0:
                        overall_status = "failed"
                        overall_returncode = int(index_result["returncode"])
                    else:
                        batch_status.invalidate_runtime_caches()

                writer.writerow(
                    {
                        "slug": str(target["slug"]),
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "system_type": str(target["system_type"]),
                        "host": str(worker["host"]),
                        "source_url": str(target["source_url"]),
                        "status": overall_status,
                        "returncode": overall_returncode,
                        "scrape_returncode": int(returncode),
                        "index_status": index_status,
                        "index_returncode": index_returncode,
                        "started_at": worker["started_at"],
                        "finished_at": finished_at,
                        "stdout_log": str(worker["stdout_path"]),
                        "stderr_log": str(worker["stderr_path"]),
                        "index_stdout_log": index_stdout_log,
                        "index_stderr_log": index_stderr_log,
                    }
                )
                handle.flush()
                update_kwargs = {
                    "status": overall_status,
                    "message": summary,
                    "finished_at": finished_at,
                    "returncode": int(overall_returncode),
                }
                progress = extract_worker_progress_for_display(worker)
                if progress is not None:
                    update_kwargs.update(progress)
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    **update_kwargs,
                )
                batch_status.write_state("reiki", status_state)
                if overall_returncode == 0 and (args.no_build_index or args.crawl_only):
                    batch_status.invalidate_runtime_caches()
                completed_count += 1
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"[{target['system_type']}] returncode={overall_returncode} {summary}",
                    flush=True,
                )
                if overall_returncode != 0:
                    print(f"[WARN] {target['slug']} は returncode={overall_returncode} で終了しました。", flush=True)

            active_workers = still_running

            now = time.time()
            if active_workers and (last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds):
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
                batch_status.write_state("reiki", status_state)
                print_status(active_workers, completed_count, len(targets))
                last_status_at = now

            if pending_targets or active_workers:
                if not launched_any:
                    time.sleep(1.0)

        for worker in active_workers:
            close_worker_streams(worker)

    batch_status.finish_batch(status_state)
    batch_status.write_state("reiki", status_state)
    print(f"[DONE] {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
