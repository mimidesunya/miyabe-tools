#!/usr/bin/env python3
from __future__ import annotations

# 既存の data/work を走査し、スクレイピング進捗用の background_tasks JSON を後追い生成する。

import argparse
import gzip
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

TOOLS_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = TOOLS_DIR.parent
DATA_ROOT = WORKSPACE_ROOT / "data"
WORK_ROOT = WORKSPACE_ROOT / "work"
sys.path.append(str(TOOLS_DIR))
sys.path.append(str(TOOLS_DIR / "gijiroku"))
sys.path.append(str(TOOLS_DIR / "reiki"))

import batch_status
import gijiroku_storage
import gijiroku_targets
import reiki_io
import reiki_targets


# テストや remote 実行時にも使えるよう、参照 root は引数で差し替えられる。
def configure_roots(
    *,
    workspace_root: Path | None = None,
    data_root: Path | None = None,
    work_root: Path | None = None,
) -> None:
    global WORKSPACE_ROOT
    global DATA_ROOT
    global WORK_ROOT

    WORKSPACE_ROOT = (workspace_root or WORKSPACE_ROOT).resolve()
    DATA_ROOT = (data_root or DATA_ROOT).resolve()
    WORK_ROOT = (work_root or WORK_ROOT).resolve()

    for module in (gijiroku_targets, reiki_targets):
        module.WORKSPACE_ROOT = WORKSPACE_ROOT
        module.DATA_ROOT = DATA_ROOT
        module.WORK_ROOT = WORK_ROOT
    batch_status.configure_status_root(DATA_ROOT / "background_tasks")


JSON_TASKS = {"gijiroku", "reiki"}
HTML_SUFFIXES = {".html", ".htm"}
MINUTES_SUFFIXES = {".txt", ".html", ".htm"}


def now_run_id() -> str:
    return time.strftime("snapshot_%Y%m%d_%H%M%S")


def now_text() -> str:
    return batch_status.now_text()


def format_timestamp(timestamp: float | None) -> str:
    return batch_status.format_timestamp_text(timestamp)


def file_or_gzip_path(path: Path) -> Path | None:
    candidates = [path]
    if path.suffix.lower() == ".gz":
        candidates.append(path.with_suffix(""))
    else:
        candidates.append(path.with_name(path.name + ".gz"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_json_array_count(path: Path) -> int:
    candidate = file_or_gzip_path(path)
    if candidate is None:
        return 0
    try:
        raw = candidate.read_bytes()
        if candidate.suffix.lower() == ".gz":
            raw = gzip.decompress(raw)
        loaded = json.loads(raw.decode("utf-8"))
    except Exception:
        return 0
    return len(loaded) if isinstance(loaded, list) else 0


def latest_mtime(paths: list[Path]) -> float | None:
    mtimes: list[float] = []
    for path in paths:
        if path.exists():
            try:
                mtimes.append(path.stat().st_mtime)
            except Exception:
                continue
    return max(mtimes) if mtimes else None


def count_gijiroku_downloads(downloads_dir: Path) -> int:
    if not downloads_dir.exists():
        return 0
    seen: set[str] = set()
    for file_path in downloads_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if gijiroku_storage.logical_suffix(file_path) not in MINUTES_SUFFIXES:
            continue
        seen.add(gijiroku_storage.source_key(file_path, downloads_dir))
    return len(seen)


def count_reiki_html_files(root: Path) -> int:
    if not root.exists():
        return 0
    seen: set[str] = set()
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        logical = reiki_io.logical_path(file_path)
        if logical.suffix.lower() not in HTML_SUFFIXES:
            continue
        seen.add(logical.relative_to(root).as_posix())
    return len(seen)


def build_snapshot_state(task_name: str, items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    latest_updated = max(
        [str(item.get("updated_at", "")).strip() for item in items.values() if isinstance(item, dict)] or [now_text()]
    )
    return {
        "task": task_name,
        "run_id": now_run_id(),
        "running": False,
        "started_at": latest_updated,
        "finished_at": latest_updated,
        "updated_at": latest_updated,
        "total_count": len(items),
        "completed_count": len(items),
        "active_count": 0,
        "pending_count": 0,
        "summary_csv": "",
        "logs_dir": "",
        "items": items,
    }


# 会議録は index JSON / ダウンロード済み本文から snapshot を復元する。
def gijiroku_snapshot_items(*, fast: bool = False) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for target in gijiroku_targets.iter_gijiroku_targets():
        downloads_dir = Path(target["downloads_dir"])
        index_json_path = Path(target["index_json_path"])

        downloaded_count = 0 if fast else count_gijiroku_downloads(downloads_dir)
        total_count = max(load_json_array_count(index_json_path), downloaded_count)
        current_count = downloaded_count
        if total_count <= 0:
            continue

        updated_at = format_timestamp(latest_mtime([downloads_dir, file_or_gzip_path(index_json_path) or index_json_path]))
        source_url = str(target.get("source_url", "")).strip()
        host = (urlsplit(source_url).hostname or "").strip().lower()
        items[str(target["slug"])] = {
            "slug": str(target["slug"]),
            "code": str(target.get("code", "")).strip(),
            "name": str(target.get("name", "")).strip(),
            "full_name": str(target.get("full_name", "")).strip(),
            "system_type": str(target.get("system_type", "")).strip(),
            "host": host,
            "source_url": source_url,
            "status": "snapshot",
            "message": "既存データから復元",
            "started_at": "",
            "finished_at": "",
            "updated_at": updated_at,
            "returncode": 0,
            "pid": None,
            "progress_current": current_count,
            "progress_total": total_count,
            "progress_unit": "meeting",
        }
    return items


# 例規集は manifest / source / clean HTML から snapshot を復元する。
def reiki_snapshot_items(*, fast: bool = False) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for target in reiki_targets.iter_reiki_targets():
        work_root = Path(target["work_root"])
        manifest_path = work_root / "source_manifest.json"
        source_dir = Path(target["source_dir"])
        html_dir = Path(target["html_dir"])

        manifest_count = load_json_array_count(manifest_path)
        source_count = 0 if fast else count_reiki_html_files(source_dir)
        clean_html_count = 0 if fast else count_reiki_html_files(html_dir)
        current_count = max(source_count, clean_html_count)
        total_count = manifest_count if manifest_count > 0 else current_count
        total_count = max(total_count, current_count)
        if total_count <= 0:
            continue

        updated_at = format_timestamp(
            latest_mtime([work_root, file_or_gzip_path(manifest_path) or manifest_path, source_dir, html_dir])
        )
        source_url = str(target.get("source_url", "")).strip()
        host = (urlsplit(source_url).hostname or "").strip().lower()
        items[str(target["slug"])] = {
            "slug": str(target["slug"]),
            "code": str(target.get("code", "")).strip(),
            "name": str(target.get("name", "")).strip(),
            "full_name": str(target.get("full_name", "")).strip(),
            "system_type": str(target.get("system_type", "")).strip(),
            "host": host,
            "source_url": source_url,
            "status": "snapshot",
            "message": "既存データから復元",
            "started_at": "",
            "finished_at": "",
            "updated_at": updated_at,
            "returncode": 0,
            "pid": None,
            "progress_current": current_count,
            "progress_total": total_count,
            "progress_unit": "ordinance",
        }
    return items


def write_snapshot(task_name: str, *, fast: bool = False) -> tuple[Path, Path, int]:
    if task_name == "gijiroku":
        items = gijiroku_snapshot_items(fast=fast)
    elif task_name == "reiki":
        items = reiki_snapshot_items(fast=fast)
    else:
        raise ValueError(f"Unsupported task: {task_name}")

    state = build_snapshot_state(task_name, items)
    main_path = batch_status.write_state(task_name, state)
    snapshot_path = batch_status.write_state(f"{task_name}_snapshot", state)
    return main_path, snapshot_path, len(items)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="既存の work/data から background_tasks JSON を復元します。")
    parser.add_argument(
        "--tasks",
        default="gijiroku,reiki",
        help="生成する task 名のカンマ区切り一覧。既定は gijiroku,reiki",
    )
    parser.add_argument(
        "--workspace-root",
        help="municipality マスタや config を読むワークスペース root。既定はこの script の親ディレクトリです。",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="ダウンロード済み本文やHTMLの再帰走査を省き、manifest から高速に復元します。",
    )
    parser.add_argument(
        "--data-root",
        help="gijiroku/reiki の実データ root。既定は <workspace-root>/data です。",
    )
    parser.add_argument(
        "--work-root",
        help="gijiroku/reiki の work root。既定は <workspace-root>/work です。",
    )
    return parser


def parse_tasks(value: str) -> list[str]:
    tasks = [item.strip() for item in str(value).split(",") if item.strip()]
    if not tasks:
        return sorted(JSON_TASKS)
    unsupported = [task for task in tasks if task not in JSON_TASKS]
    if unsupported:
        raise ValueError(f"Unsupported task(s): {', '.join(unsupported)}")
    seen: set[str] = set()
    ordered: list[str] = []
    for task in tasks:
        if task in seen:
            continue
        seen.add(task)
        ordered.append(task)
    return ordered


def main() -> int:
    args = build_parser().parse_args()
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else WORKSPACE_ROOT
    data_root = Path(args.data_root).resolve() if args.data_root else (workspace_root / "data")
    work_root = Path(args.work_root).resolve() if args.work_root else (workspace_root / "work")
    configure_roots(
        workspace_root=workspace_root,
        data_root=data_root,
        work_root=work_root,
    )
    try:
        tasks = parse_tasks(args.tasks)
    except ValueError as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 2

    for task_name in tasks:
        main_path, snapshot_path, count = write_snapshot(task_name, fast=args.fast)
        print(f"[DONE] {task_name}: {count} items -> {main_path}", flush=True)
        print(f"[DONE] {task_name}_snapshot: {count} items -> {snapshot_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
