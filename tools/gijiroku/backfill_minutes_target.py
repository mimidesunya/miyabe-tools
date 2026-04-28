#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import build_locks
import build_minutes_index
import gijiroku_storage
import gijiroku_targets


CORRUPT_DB_MARKERS = (
    "vtable constructor failed: minutes_fts",
    "database disk image is malformed",
    "database malformed",
    "invalid page number",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="1 自治体ぶんの minutes.sqlite 欠落行を補完します。"
    )
    parser.add_argument("--slug", required=True, help="対象自治体 slug")
    parser.add_argument("--state-path", type=Path, required=True, help="進捗 JSON の出力先")
    parser.add_argument("--result-json", type=Path, required=True, help="結果 JSON の出力先")
    return parser


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def looks_like_corrupt_minutes_db(exc: Exception) -> bool:
    text = str(exc).strip().lower()
    if text == "":
        return False
    return any(marker in text for marker in CORRUPT_DB_MARKERS)


def backup_corrupt_minutes_db(db_path: Path) -> Path:
    timestamp = now_ts()
    backup_path = db_path.with_name(f"{db_path.name}.corrupt-{timestamp}")
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(db_path) + suffix)
        if not source.exists():
            continue
        target = Path(str(backup_path) + suffix)
        source.replace(target)
    return backup_path


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def emit_progress(state_path: Path, progress: dict[str, int | str]) -> None:
    processed = int(progress.get("processed", 0) or 0)
    total_files = int(progress.get("total_files", 0) or 0)
    gijiroku_storage.update_progress_state(
        state_path,
        current=processed,
        total=total_files,
        unit="meeting",
    )
    print(
        f"[PROGRESS] unit=meeting current={processed} total={total_files}",
        flush=True,
    )


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug)
    state_path = args.state_path
    result_json = args.result_json
    db_path = Path(target["db_path"])

    try:
        state_path.unlink(missing_ok=True)
    except Exception:
        pass

    lock_path = build_locks.acquire_build_lock(
        str(target["slug"]),
        owner="backfill_minutes_target",
        wait_seconds=0.0,
    )
    if lock_path is None:
        message = "別プロセスが minutes.sqlite を更新中のためスキップ"
        write_json_atomic(
            result_json,
            {
                "status": "busy",
                "returncode": 0,
                "added": 0,
                "existing": 0,
                "skipped": 0,
                "total_files": 0,
                "message": message,
            },
        )
        print(f"[INFO] {message}", flush=True)
        return 0

    try:
        def on_progress(progress: dict[str, int | str]) -> None:
            emit_progress(state_path, progress)

        try:
            stats = build_minutes_index.backfill_missing_rows(
                Path(target["downloads_dir"]),
                Path(target["index_json_path"]),
                db_path,
                progress_callback=on_progress,
            )
        except Exception as exc:
            if not looks_like_corrupt_minutes_db(exc):
                raise

            backup_path = backup_corrupt_minutes_db(db_path)
            print(
                f"[INFO] minutes.sqlite が破損していたため {backup_path.name} へ退避し、再構築します",
                flush=True,
            )
            indexed, skipped_count = build_minutes_index.build_index(
                Path(target["downloads_dir"]),
                Path(target["index_json_path"]),
                db_path,
                progress_callback=on_progress,
            )
            stats = {
                "added": indexed,
                "existing": 0,
                "skipped": skipped_count,
                "total_files": indexed + skipped_count,
            }

        total_files = int(stats["total_files"])
        gijiroku_storage.update_progress_state(
            state_path,
            current=total_files,
            total=total_files,
            unit="meeting",
        )
        message = (
            f"added={stats['added']} existing={stats['existing']} "
            f"skipped={stats['skipped']} total_files={stats['total_files']}"
        )
        write_json_atomic(
            result_json,
            {
                "status": "ok",
                "returncode": 0,
                "added": int(stats["added"]),
                "existing": int(stats["existing"]),
                "skipped": int(stats["skipped"]),
                "total_files": total_files,
                "message": message,
            },
        )
        print(f"[DONE] {message}", flush=True)
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        write_json_atomic(
            result_json,
            {
                "status": "failed",
                "returncode": 1,
                "added": 0,
                "existing": 0,
                "skipped": 0,
                "total_files": 0,
                "message": message,
            },
        )
        print(f"[ERROR] {message}", flush=True)
        return 1
    finally:
        build_locks.release_build_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
