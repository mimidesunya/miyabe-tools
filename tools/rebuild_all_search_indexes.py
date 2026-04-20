#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tools"))
sys.path.append(str(ROOT / "tools" / "gijiroku"))
sys.path.append(str(ROOT / "tools" / "reiki"))

import build_locks
import build_minutes_index
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


def print_minutes_progress(slug: str, progress: dict[str, int | str]) -> None:
    stage = str(progress.get("stage", ""))
    processed = int(progress.get("processed", 0))
    total_files = int(progress.get("total_files", 0))
    added = int(progress.get("added", 0))
    skipped = int(progress.get("skipped", 0))
    print(
        f"    [progress] {slug} stage={stage} processed={processed}/{total_files} "
        f"added={added} skipped={skipped}",
        flush=True,
    )


def run_minutes_rebuild(target: dict) -> dict[str, object]:
    slug = str(target["slug"])
    lock_path = build_locks.acquire_build_lock(
        slug,
        owner="rebuild_all_search_indexes",
        wait_seconds=0.0,
    )
    if lock_path is None:
        return {
            "kind": "minutes",
            "slug": slug,
            "code": str(target["code"]),
            "name": str(target["name"]),
            "status": "skipped_locked",
            "detail": "別プロセスが minutes.sqlite を更新中です",
            "seconds": 0.0,
        }

    started = time.monotonic()
    try:
        indexed, skipped = build_minutes_index.build_index(
            Path(target["downloads_dir"]),
            Path(target["index_json_path"]),
            Path(target["db_path"]),
            progress_callback=lambda progress: print_minutes_progress(slug, progress),
        )
        return {
            "kind": "minutes",
            "slug": slug,
            "code": str(target["code"]),
            "name": str(target["name"]),
            "status": "ok",
            "detail": f"indexed={indexed} skipped={skipped}",
            "seconds": round(time.monotonic() - started, 3),
        }
    finally:
        build_locks.release_build_lock(lock_path)


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

    rows: list[dict[str, object]] = []
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["kind", "slug", "code", "name", "status", "detail", "seconds", "started_at", "finished_at"],
        )
        writer.writeheader()

        for index, target in enumerate(minutes_targets, start=1):
            slug = str(target["slug"])
            started_at = now_text()
            print(f"[START minutes {index}/{len(minutes_targets)}] {slug}", flush=True)
            if slug in active_minutes_slugs or looks_like_active_minutes_scrape(target):
                row = {
                    "kind": "minutes",
                    "slug": slug,
                    "code": str(target["code"]),
                    "name": str(target["name"]),
                    "status": "skipped_active",
                    "detail": "会議録バッチが実行中のためスキップしました",
                    "seconds": 0.0,
                }
            else:
                try:
                    row = run_minutes_rebuild(target)
                except Exception as exc:
                    row = {
                        "kind": "minutes",
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
            print(f"[DONE minutes {index}/{len(minutes_targets)}] {slug} {row['status']} {row['detail']}", flush=True)

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
    print(
        f"[SUMMARY] total={len(rows)} ok={ok} skipped={skipped} failed={failed} csv={output_csv}",
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
