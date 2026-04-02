#!/usr/bin/env python3
from __future__ import annotations

# 既に保存済みの clean HTML を走査し、ordinances.sqlite に未登録の行だけを補う保守バッチ。

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import batch_status
import reiki_priority
import reiki_targets


def log_target_progress(target: dict, index: int, total_targets: int, progress: dict[str, int | str]) -> None:
    # 大きい自治体で長く無音にならないよう、自治体内の進み具合も定期表示する。
    stage = str(progress.get("stage", "indexing"))
    if stage == "prepare_db":
        print(
            f"[PROGRESS {index}/{total_targets}] {target['slug']} DB を確認中 total={progress['total_html']}",
            flush=True,
        )
        return
    print(
        f"[PROGRESS {index}/{total_targets}] {target['slug']} "
        f"{progress['processed']}/{progress['total_html']} "
        f"added={progress['added']} existing={progress['existing']} skipped={progress['skipped']}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ordinances.sqlite の欠落行を、ダウンロード済み HTML から補完します。"
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
    return reiki_targets.project_root() / "work" / "reiki" / "_reiki_reflect_batch"


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


def target_has_scraped_data(target: dict) -> bool:
    html_dir = Path(target["html_dir"])
    manifest_path = Path(target["work_root"]) / "source_manifest.json.gz"
    return html_dir.is_dir() or manifest_path.is_file()


def main() -> int:
    args = build_parser().parse_args()

    targets = list(reiki_targets.iter_reiki_targets())
    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

    targets = [target for target in targets if target_has_scraped_data(target)]
    targets = reiki_priority.sort_targets_by_priority(targets)
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if args.list_targets:
        print(f"[INFO] 補完対象自治体数: {len(targets)}")
        for target in targets:
            priority = reiki_priority.target_priority_info(target)
            print(
                f"{target['slug']}\t{target['code']}\t{priority['priority_label']}\t"
                f"{target['system_type']}\t{target['name']}\t{target['html_dir']}"
            )
        return 0

    if not targets:
        print("[INFO] 補完対象はありません。")
        return 0

    import build_ordinance_index

    run_id = now_ts()
    reflect_root = batch_dir()
    summary_path = summary_output_path(run_id)
    run_logs_dir = logs_dir(run_id)
    reflect_root.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 補完対象自治体数: {len(targets)}")
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)

    status_state = batch_status.build_state("reiki_reflect", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, "")
    batch_status.write_state("reiki_reflect", status_state)

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
                "total_html",
            ],
        )
        writer.writeheader()

        for index, target in enumerate(targets, start=1):
            slug = str(target["slug"])
            manifest_json = Path(target["work_root"]) / "source_manifest.json.gz"
            started_at = batch_status.now_text()
            priority = reiki_priority.target_priority_info(target)

            print(f"[START {index}/{len(targets)}] {slug} ordinances.sqlite を点検します", flush=True)
            batch_status.update_item(
                status_state,
                slug,
                status="running",
                message="反映中",
                started_at=started_at,
            )
            batch_status.write_state("reiki_reflect", status_state)

            try:
                def on_progress(progress: dict[str, int | str]) -> None:
                    log_target_progress(target, index, len(targets), progress)
                    total_html = int(progress.get("total_html", 0) or 0)
                    processed = int(progress.get("processed", 0) or 0)
                    batch_status.update_item(
                        status_state,
                        slug,
                        message="反映中",
                        progress_current=processed,
                        progress_total=total_html,
                        progress_unit="ordinance",
                    )
                    batch_status.write_state("reiki_reflect", status_state)

                stats = build_ordinance_index.backfill_missing_rows(
                    slug=slug,
                    clean_html_dir=Path(target["html_dir"]),
                    classification_dir=Path(target["classification_dir"]),
                    markdown_dir=Path(target["markdown_dir"]),
                    manifest_json=manifest_json,
                    output_db=Path(target["db_path"]),
                    progress_callback=on_progress,
                )
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
                        "total_html": 0,
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
                batch_status.write_state("reiki_reflect", status_state)
                print(f"[WARN] {slug} の補完に失敗しました: {exc}", flush=True)
                continue

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
                    "total_html": int(stats["total_html"]),
                }
            )
            handle.flush()
            batch_status.update_item(
                status_state,
                slug,
                status="ok",
                message=(
                    f"added={stats['added']} existing={stats['existing']} "
                    f"skipped={stats['skipped']} total_html={stats['total_html']}"
                ),
                finished_at=finished_at,
                returncode=0,
                progress_current=int(stats["total_html"]),
                progress_total=int(stats["total_html"]),
                progress_unit="ordinance",
            )
            batch_status.write_state("reiki_reflect", status_state)
            batch_status.invalidate_runtime_caches()
            print(
                f"[DONE {index}/{len(targets)}] {slug} "
                f"added={stats['added']} existing={stats['existing']} skipped={stats['skipped']} total_html={stats['total_html']}",
                flush=True,
            )

    batch_status.finish_batch(status_state)
    batch_status.write_state("reiki_reflect", status_state)
    batch_status.invalidate_runtime_caches()

    if failures:
        print(f"[WARN] {failures} 件の補完に失敗しました。 added={total_added}", flush=True)
        return 1

    print(f"[DONE] ordinances.sqlite の不足分を補完しました。 added={total_added}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
