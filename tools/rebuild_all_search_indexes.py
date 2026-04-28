#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tools"))
sys.path.append(str(ROOT / "tools" / "gijiroku"))
sys.path.append(str(ROOT / "tools" / "reiki"))

import batch_status
from batch_runner_common import close_worker_streams, extract_worker_progress_from_state, summarize_worker
import build_ordinance_index
import gijiroku_targets
import reiki_targets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ローカルに実データがある自治体の検索 DB を一括再ビルドします。"
    )
    parser.add_argument(
        "--kinds",
        default="minutes,reiki",
        help="rebuild 対象。minutes,reiki のカンマ区切り",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="slug / code / name / full_name に部分一致する自治体だけ対象にする",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="結果 CSV の出力先",
    )
    parser.add_argument(
        "--minutes-parallel",
        type=int,
        default=4,
        help="会議録再構築の同時実行自治体数",
    )
    parser.add_argument(
        "--python-command",
        default=sys.executable,
        help="会議録再構築の子プロセス起動に使う Python コマンド",
    )
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def matches_filter(target: dict, needle: str) -> bool:
    token = needle.strip().lower()
    if token == "":
        return True
    candidates = (
        str(target.get("slug", "")).lower(),
        str(target.get("code", "")).lower(),
        str(target.get("name", "")).lower(),
        str(target.get("full_name", "")).lower(),
    )
    return any(token in candidate for candidate in candidates)


def has_any_file(root: Path, suffixes: tuple[str, ...]) -> bool:
    if not root.exists():
        return False
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in suffixes:
            return True
    return False


def discover_minutes_targets(name_filter: str) -> list[dict]:
    targets: list[dict] = []
    for target in gijiroku_targets.iter_gijiroku_targets():
        if not matches_filter(target, name_filter):
            continue
        downloads_dir = Path(target["downloads_dir"])
        if not has_any_file(downloads_dir, (".txt", ".html", ".htm", ".gz")):
            continue
        targets.append(target)
    return targets


def discover_reiki_targets(name_filter: str) -> list[dict]:
    targets: list[dict] = []
    for target in reiki_targets.iter_reiki_targets():
        if not matches_filter(target, name_filter):
            continue
        html_dir = Path(target["html_dir"])
        if not has_any_file(html_dir, (".html", ".htm")):
            continue
        targets.append(target)
    return targets


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def default_output_csv() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "work" / "maintenance" / f"rebuild_all_search_indexes_{stamp}.csv"


def active_slugs_from_background_task(task_name: str) -> set[str]:
    path = ROOT / "data" / "background_tasks" / f"{task_name}.json"
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, dict) or not payload.get("running"):
        return set()
    items = payload.get("items")
    if not isinstance(items, dict):
        return set()
    slugs: set[str] = set()
    for slug, item in items.items():
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "")).strip() == "running":
            slugs.add(str(slug).strip())
    return {slug for slug in slugs if slug}


def is_recently_updated(path: Path, *, within_seconds: float) -> bool:
    if not path.exists():
        return False
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds >= 0 and age_seconds <= within_seconds


def looks_like_active_minutes_scrape(target: dict, *, within_seconds: float = 15 * 60) -> bool:
    state_path = Path(target["work_dir"]) / "scrape_state.json"
    return is_recently_updated(state_path, within_seconds=within_seconds)


def build_minutes_child_command(
    args: argparse.Namespace,
    target: dict,
    *,
    state_path: Path,
    result_json: Path,
) -> list[str]:
    command = shlex.split(str(args.python_command))
    command.extend(
        [
            str(Path("tools") / "gijiroku" / "rebuild_minutes_target.py"),
            "--slug",
            str(target["slug"]),
            "--state-path",
            str(state_path),
            "--result-json",
            str(result_json),
        ]
    )
    return command


def launch_minutes_worker(
    target: dict,
    seq: int,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict[str, object]:
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.log"
    stderr_path = run_logs_dir / f"{slug}.err.log"
    state_path = run_logs_dir / f"{slug}.state.json"
    result_path = run_logs_dir / f"{slug}.result.json"
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")
    process = subprocess.Popen(
        build_minutes_child_command(args, target, state_path=state_path, result_json=result_path),
        cwd=str(ROOT),
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
        "state_path": state_path,
        "result_path": result_path,
        "started_at": now_text(),
    }


def load_minutes_worker_result(worker: dict[str, object], returncode: int) -> dict[str, object]:
    result_path = Path(worker["result_path"])
    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

    summary = summarize_worker(Path(worker["stdout_path"]), Path(worker["stderr_path"]))
    return {
        "status": "failed" if returncode != 0 else "ok",
        "returncode": int(returncode),
        "indexed": 0,
        "skipped": 0,
        "total_files": 0,
        "seconds": 0.0,
        "message": summary,
    }


def task_host(target: dict) -> str:
    source_url = str(target.get("source_url", "")).strip()
    if source_url == "":
        return ""
    try:
        from urllib.parse import urlsplit

        return str(urlsplit(source_url).netloc or "").strip()
    except Exception:
        return ""


def run_reiki_rebuild(target: dict) -> dict[str, object]:
    slug = str(target["slug"])
    started = time.monotonic()
    stats = build_ordinance_index.build_index(
        slug=slug,
        clean_html_dir=Path(target["html_dir"]),
        classification_dir=Path(target["classification_dir"]),
        markdown_dir=Path(target["markdown_dir"]),
        manifest_json=Path(target["work_root"]) / "source_manifest.json.gz",
        output_db=Path(target["db_path"]),
    )
    return {
        "kind": "reiki",
        "slug": slug,
        "code": str(target["code"]),
        "name": str(target["name"]),
        "status": "ok",
        "detail": (
            f"indexed={stats['indexed']} classified={stats['classified']} "
            f"unclassified={stats['unclassified']} skipped={stats['skipped']}"
        ),
        "seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    args = parse_args()
    if args.minutes_parallel < 1:
        raise SystemExit("--minutes-parallel は 1 以上を指定してください。")
    requested_kinds = {item.strip() for item in str(args.kinds).split(",") if item.strip()}
    unsupported = requested_kinds - {"minutes", "reiki"}
    if unsupported:
        raise SystemExit(f"Unsupported kinds: {', '.join(sorted(unsupported))}")

    output_csv = args.output_csv or default_output_csv()
    ensure_parent(output_csv)

    active_minutes_slugs = active_slugs_from_background_task("gijiroku")
    active_reiki_slugs = active_slugs_from_background_task("reiki")
    minutes_targets = discover_minutes_targets(args.filter) if "minutes" in requested_kinds else []
    reiki_targets_list = discover_reiki_targets(args.filter) if "reiki" in requested_kinds else []

    print(
        f"[INFO] minutes targets={len(minutes_targets)} reiki targets={len(reiki_targets_list)} "
        f"active_minutes={len(active_minutes_slugs)} active_reiki={len(active_reiki_slugs)} "
        f"output_csv={output_csv}",
        flush=True,
    )

    minutes_state = None
    minutes_logs_dir = None
    if minutes_targets:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        minutes_logs_dir = output_csv.parent / f"gijiroku_rebuild_logs_{run_id}"
        minutes_logs_dir.mkdir(parents=True, exist_ok=True)
        minutes_state = batch_status.build_state("gijiroku_rebuild", run_id, len(minutes_targets), output_csv, minutes_logs_dir)
        for target in minutes_targets:
            batch_status.register_target(minutes_state, target, task_host(target))
        batch_status.update_runtime_metrics(
            minutes_state,
            running_label="再構築中",
            worker_capacity=args.minutes_parallel,
            worker_active_count=0,
        )
        batch_status.write_state("gijiroku_rebuild", minutes_state)
        batch_status.invalidate_runtime_caches()

    rows: list[dict[str, object]] = []
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["kind", "slug", "code", "name", "status", "detail", "seconds", "started_at", "finished_at"],
        )
        writer.writeheader()

        def write_minutes_state(active_count: int) -> None:
            if not isinstance(minutes_state, dict):
                return
            batch_status.update_runtime_metrics(
                minutes_state,
                running_label="再構築中",
                worker_capacity=args.minutes_parallel,
                worker_active_count=active_count,
            )
            batch_status.write_state("gijiroku_rebuild", minutes_state)

        active_workers: list[dict[str, object]] = []
        pending_minutes_targets = list(minutes_targets)
        launched_minutes = 0
        completed_minutes = 0

        while pending_minutes_targets or active_workers:
            made_progress = False
            completed_workers: list[tuple[dict[str, object], int]] = []
            still_running: list[dict[str, object]] = []

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

            while pending_minutes_targets and len(active_workers) < args.minutes_parallel:
                target = pending_minutes_targets.pop(0)
                slug = str(target["slug"])
                started_at = now_text()

                if slug in active_minutes_slugs or looks_like_active_minutes_scrape(target):
                    row = {
                        "kind": "minutes",
                        "slug": slug,
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "status": "skipped_active",
                        "detail": "会議録バッチが実行中のためスキップしました",
                        "seconds": 0.0,
                        "started_at": started_at,
                        "finished_at": now_text(),
                    }
                    writer.writerow(row)
                    handle.flush()
                    rows.append(row)
                    completed_minutes += 1
                    if isinstance(minutes_state, dict):
                        batch_status.update_item(
                            minutes_state,
                            slug,
                            status="ok",
                            message=str(row["detail"]),
                            finished_at=str(row["finished_at"]),
                            returncode=0,
                        )
                        write_minutes_state(len(active_workers))
                        batch_status.invalidate_runtime_caches()
                    print(
                        f"[DONE minutes {completed_minutes}/{len(minutes_targets)}] {slug} "
                        f"{row['status']} {row['detail']}",
                        flush=True,
                    )
                    made_progress = True
                    continue

                launched_minutes += 1
                try:
                    worker = launch_minutes_worker(
                        target,
                        launched_minutes,
                        args=args,
                        run_logs_dir=minutes_logs_dir if isinstance(minutes_logs_dir, Path) else output_csv.parent,
                    )
                except Exception as exc:
                    row = {
                        "kind": "minutes",
                        "slug": slug,
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "status": "failed",
                        "detail": f"子プロセス起動失敗: {type(exc).__name__}: {exc}",
                        "seconds": 0.0,
                        "started_at": started_at,
                        "finished_at": now_text(),
                    }
                    writer.writerow(row)
                    handle.flush()
                    rows.append(row)
                    completed_minutes += 1
                    if isinstance(minutes_state, dict):
                        batch_status.update_item(
                            minutes_state,
                            slug,
                            status="failed",
                            message=str(row["detail"]),
                            finished_at=str(row["finished_at"]),
                            returncode=-1,
                        )
                        write_minutes_state(len(active_workers))
                        batch_status.invalidate_runtime_caches()
                    print(
                        f"[DONE minutes {completed_minutes}/{len(minutes_targets)}] {slug} "
                        f"{row['status']} {row['detail']}",
                        flush=True,
                    )
                    made_progress = True
                    continue

                active_workers.append(worker)
                if isinstance(minutes_state, dict):
                    batch_status.update_item(
                        minutes_state,
                        slug,
                        status="running",
                        message="再構築中",
                        started_at=str(worker["started_at"]),
                        pid=int(worker["process"].pid),
                        progress_current=0,
                        progress_total=None,
                        progress_unit="meeting",
                    )
                    write_minutes_state(len(active_workers))
                    batch_status.invalidate_runtime_caches()
                print(
                    f"[START minutes {launched_minutes}/{len(minutes_targets)}] {slug} "
                    f"minutes.sqlite を再構築します pid={worker['process'].pid}",
                    flush=True,
                )
                made_progress = True

            for worker in active_workers:
                progress = extract_worker_progress_from_state(Path(worker["state_path"]), default_unit="meeting")
                if progress is None or not isinstance(minutes_state, dict):
                    continue
                progress_current = int(progress["progress_current"])
                progress_total = int(progress["progress_total"])
                batch_status.update_item(
                    minutes_state,
                    str(worker["target"]["slug"]),
                    message="FTS 再構築中" if progress_total > 0 and progress_current >= progress_total else "再構築中",
                    progress_current=progress_current,
                    progress_total=progress_total,
                    progress_unit=str(progress["progress_unit"]),
                )

            for worker, returncode in completed_workers:
                target = worker["target"]
                slug = str(target["slug"])
                result = load_minutes_worker_result(worker, returncode)
                status = str(result.get("status", "failed")).strip() or "failed"
                detail = str(result.get("message", "")).strip() or summarize_worker(
                    Path(worker["stdout_path"]),
                    Path(worker["stderr_path"]),
                )
                seconds = round(float(result.get("seconds", 0.0) or 0.0), 3)
                finished_at = now_text()
                row_returncode = int(result.get("returncode", returncode) or 0)
                total_files = int(result.get("total_files", 0) or 0)

                row = {
                    "kind": "minutes",
                    "slug": slug,
                    "code": str(target["code"]),
                    "name": str(target["name"]),
                    "status": status,
                    "detail": detail,
                    "seconds": seconds,
                    "started_at": str(worker["started_at"]),
                    "finished_at": finished_at,
                }
                writer.writerow(row)
                handle.flush()
                rows.append(row)
                completed_minutes += 1

                if isinstance(minutes_state, dict):
                    batch_status.update_item(
                        minutes_state,
                        slug,
                        status="failed" if status == "failed" else "ok",
                        message=detail,
                        finished_at=finished_at,
                        returncode=row_returncode,
                        progress_current=total_files if total_files > 0 else None,
                        progress_total=total_files if total_files > 0 else None,
                        progress_unit="meeting",
                    )
                    write_minutes_state(len(active_workers))
                    batch_status.invalidate_runtime_caches()

                print(
                    f"[DONE minutes {completed_minutes}/{len(minutes_targets)}] {slug} "
                    f"{row['status']} {row['detail']}",
                    flush=True,
                )

            if pending_minutes_targets or active_workers:
                write_minutes_state(len(active_workers))
                if not made_progress:
                    time.sleep(1.0)

        for index, target in enumerate(reiki_targets_list, start=1):
            slug = str(target["slug"])
            started_at = now_text()
            print(f"[START reiki {index}/{len(reiki_targets_list)}] {slug}", flush=True)
            if slug in active_reiki_slugs:
                row = {
                    "kind": "reiki",
                    "slug": slug,
                    "code": str(target["code"]),
                    "name": str(target["name"]),
                    "status": "skipped_active",
                    "detail": "例規バッチが実行中のためスキップしました",
                    "seconds": 0.0,
                }
            else:
                try:
                    row = run_reiki_rebuild(target)
                except Exception as exc:
                    row = {
                        "kind": "reiki",
                        "slug": slug,
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "status": "failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                        "seconds": 0.0,
                    }
            row["started_at"] = started_at
            row["finished_at"] = now_text()
            writer.writerow(row)
            handle.flush()
            rows.append(row)
            print(f"[DONE reiki {index}/{len(reiki_targets_list)}] {slug} {row['status']} {row['detail']}", flush=True)

    ok = sum(1 for row in rows if row["status"] == "ok")
    skipped = sum(1 for row in rows if str(row["status"]).startswith("skipped"))
    failed = sum(1 for row in rows if row["status"] == "failed")
    if isinstance(minutes_state, dict):
        batch_status.finish_batch(minutes_state)
        write_minutes_state(0)
        batch_status.invalidate_runtime_caches()
    print(
        f"[SUMMARY] total={len(rows)} ok={ok} skipped={skipped} failed={failed} csv={output_csv}",
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
