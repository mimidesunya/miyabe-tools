#!/usr/bin/env python3
from __future__ import annotations

# 既にダウンロード済みの会議録を走査し、
# minutes.sqlite に未登録の行だけを補う保守用バッチ。

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import batch_status
import build_locks
import gijiroku_priority
import gijiroku_targets


CORRUPT_DB_MARKERS = (
    "vtable constructor failed: minutes_fts",
    "database disk image is malformed",
    "database malformed",
    "invalid page number",
)


def log_target_progress(target: dict, index: int, total_targets: int, progress: dict[str, int | str]) -> None:
    # 大きい自治体で長く無音にならないよう、自治体内の進み具合も定期表示する。
    stage = str(progress.get("stage", "indexing"))
    if stage == "prepare_db":
        print(
            f"[PROGRESS {index}/{total_targets}] {target['slug']} DB を確認中 total={progress['total_files']}",
            flush=True,
        )
        return
    print(
        f"[PROGRESS {index}/{total_targets}] {target['slug']} "
        f"{progress['processed']}/{progress['total_files']} "
        f"added={progress['added']} existing={progress['existing']} skipped={progress['skipped']}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="minutes.sqlite の欠落行を、ダウンロード済み会議録から補完します。"
    )
    parser.add_argument(
        "--filter",
        default="",
        help="slug / code / name / full_name に部分一致する自治体だけを対象にする",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        default=0,
        help="補完する自治体数の上限（0 は無制限）",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="補完対象一覧だけ表示して終了する",
    )
    return parser


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def batch_dir() -> Path:
    return gijiroku_targets.project_root() / "work" / "gijiroku" / "_minutes_reflect_batch"


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


def target_has_scraped_data(target: dict) -> bool:
    downloads_dir = Path(target["downloads_dir"])
    index_json_path = Path(target["index_json_path"])
    return downloads_dir.is_dir() or index_json_path.is_file()


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


def main() -> int:
    args = build_parser().parse_args()

    targets = list(gijiroku_targets.iter_gijiroku_targets())
    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

    targets = [target for target in targets if target_has_scraped_data(target)]
    targets = gijiroku_priority.sort_targets_by_priority(targets)
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if args.list_targets:
        print(f"[INFO] 補完対象自治体数: {len(targets)}")
        for target in targets:
            priority = gijiroku_priority.target_priority_info(target)
            print(
                f"{target['slug']}\t{target['code']}\t{priority['priority_label']}\t"
                f"{target['system_type']}\t{target['name']}\t{target['downloads_dir']}"
            )
        return 0

    if not targets:
        print("[INFO] 補完対象はありません。")
        return 0

    import build_minutes_index

    run_id = now_ts()
    reflect_root = batch_dir()
    summary_path = summary_output_path(run_id)
    run_logs_dir = logs_dir(run_id)
    reflect_root.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 点検対象自治体数: {len(targets)}")
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)

    status_state = batch_status.build_state("gijiroku_reflect", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, "")
    batch_status.write_state("gijiroku_reflect", status_state)

    failures = 0
    total_added = 0
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slug",
                "code",
                "name",
                "priority",
                "status",
                "returncode",
                "started_at",
                "finished_at",
                "added",
                "existing",
                "skipped",
                "total_files",
            ],
        )
        writer.writeheader()

        for index, target in enumerate(targets, start=1):
            slug = str(target["slug"])
            started_at = batch_status.now_text()
            priority = gijiroku_priority.target_priority_info(target)

            print(f"[START {index}/{len(targets)}] {slug} minutes.sqlite を点検します", flush=True)
            batch_status.update_item(
                status_state,
                slug,
                status="running",
                message="反映中",
                started_at=started_at,
            )
            batch_status.write_state("gijiroku_reflect", status_state)

            lock_path = build_locks.acquire_build_lock(
                slug,
                owner="build_missing_minutes_indexes",
                wait_seconds=0.0,
            )
            if lock_path is None:
                finished_at = batch_status.now_text()
                writer.writerow(
                    {
                        "slug": slug,
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "priority": str(priority["priority_label"]),
                        "status": "busy",
                        "returncode": 0,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "added": 0,
                        "existing": 0,
                        "skipped": 0,
                        "total_files": 0,
                    }
                )
                handle.flush()
                batch_status.update_item(
                    status_state,
                    slug,
                    status="ok",
                    message="別プロセスが minutes.sqlite を更新中のためスキップ",
                    finished_at=finished_at,
                    returncode=0,
                )
                batch_status.write_state("gijiroku_reflect", status_state)
                print(f"[SKIP {index}/{len(targets)}] {slug} は別プロセスが更新中です", flush=True)
                continue

            try:
                def on_progress(progress: dict[str, int | str]) -> None:
                    log_target_progress(target, index, len(targets), progress)
                    total_files = int(progress.get("total_files", 0) or 0)
                    processed = int(progress.get("processed", 0) or 0)
                    batch_status.update_item(
                        status_state,
                        slug,
                        message="反映中",
                        progress_current=processed,
                        progress_total=total_files,
                        progress_unit="meeting",
                    )
                    batch_status.write_state("gijiroku_reflect", status_state)

                try:
                    stats = build_minutes_index.backfill_missing_rows(
                        Path(target["downloads_dir"]),
                        Path(target["index_json_path"]),
                        Path(target["db_path"]),
                        progress_callback=on_progress,
                    )
                except Exception as exc:
                    if not looks_like_corrupt_minutes_db(exc):
                        raise

                    db_path = Path(target["db_path"])
                    backup_path = backup_corrupt_minutes_db(db_path)
                    print(
                        f"[WARN] {slug} minutes.sqlite が破損していたため {backup_path.name} へ退避し、再構築します",
                        flush=True,
                    )
                    batch_status.update_item(
                        status_state,
                        slug,
                        message="DB破損のため再構築中",
                        progress_current=0,
                        progress_total=None,
                        progress_unit="meeting",
                    )
                    batch_status.write_state("gijiroku_reflect", status_state)
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
            except Exception as exc:
                failures += 1
                finished_at = batch_status.now_text()
                writer.writerow(
                    {
                        "slug": slug,
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "priority": str(priority["priority_label"]),
                        "status": "failed",
                        "returncode": 1,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "added": 0,
                        "existing": 0,
                        "skipped": 0,
                        "total_files": 0,
                    }
                )
                handle.flush()
                batch_status.update_item(
                    status_state,
                    slug,
                    status="failed",
                    message=str(exc),
                    finished_at=finished_at,
                    returncode=1,
                )
                batch_status.write_state("gijiroku_reflect", status_state)
                print(f"[WARN] {slug} の補完に失敗しました: {exc}", flush=True)
            else:
                total_added += int(stats["added"])
                finished_at = batch_status.now_text()
                writer.writerow(
                    {
                        "slug": slug,
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "priority": str(priority["priority_label"]),
                        "status": "ok",
                        "returncode": 0,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "added": int(stats["added"]),
                        "existing": int(stats["existing"]),
                        "skipped": int(stats["skipped"]),
                        "total_files": int(stats["total_files"]),
                    }
                )
                handle.flush()
                batch_status.update_item(
                    status_state,
                    slug,
                    status="ok",
                    message=(
                        f"added={stats['added']} existing={stats['existing']} "
                        f"skipped={stats['skipped']} total_files={stats['total_files']}"
                    ),
                    finished_at=finished_at,
                    returncode=0,
                    progress_current=int(stats["total_files"]),
                    progress_total=int(stats["total_files"]),
                    progress_unit="meeting",
                )
                batch_status.write_state("gijiroku_reflect", status_state)
                batch_status.invalidate_runtime_caches()
                print(
                    f"[DONE {index}/{len(targets)}] {slug} "
                    f"added={stats['added']} existing={stats['existing']} skipped={stats['skipped']} total_files={stats['total_files']}",
                    flush=True,
                )
            finally:
                build_locks.release_build_lock(lock_path)

    batch_status.finish_batch(status_state)
    batch_status.write_state("gijiroku_reflect", status_state)
    batch_status.invalidate_runtime_caches()

    if failures:
        print(f"[WARN] {failures} 件の補完に失敗しました。 added={total_added}", flush=True)
        return 1

    print(f"[DONE] minutes.sqlite の不足分を補完しました。 added={total_added}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
