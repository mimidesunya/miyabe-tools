#!/usr/bin/env python3
from __future__ import annotations

# 既に保存済みの clean HTML を走査し、ordinances.sqlite に未登録の行だけを補う保守バッチ。

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import build_ordinance_index
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
        "--configured-only",
        action="store_true",
        help="config.json に登録済みの自治体だけを対象にする",
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

    targets = list(reiki_targets.iter_reiki_targets(configured_only=args.configured_only))
    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

    targets = [target for target in targets if target_has_scraped_data(target)]
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if args.list_targets:
        print(f"[INFO] 補完対象自治体数: {len(targets)}")
        for target in targets:
            print(
                f"{target['slug']}\t{target['code']}\t{target['system_type']}\t"
                f"{target['name']}\t{target['html_dir']}"
            )
        return 0

    if not targets:
        print("[INFO] 補完対象はありません。")
        return 0

    print(f"[INFO] 補完対象自治体数: {len(targets)}")
    failures = 0
    total_added = 0
    for index, target in enumerate(targets, start=1):
        manifest_json = Path(target["work_root"]) / "source_manifest.json.gz"
        print(f"[START {index}/{len(targets)}] {target['slug']} ordinances.sqlite を点検します", flush=True)
        try:
            stats = build_ordinance_index.backfill_missing_rows(
                slug=str(target["slug"]),
                clean_html_dir=Path(target["html_dir"]),
                classification_dir=Path(target["classification_dir"]),
                markdown_dir=Path(target["markdown_dir"]),
                manifest_json=manifest_json,
                output_db=Path(target["db_path"]),
                progress_callback=lambda progress, target=target, index=index, total=len(targets): log_target_progress(
                    target,
                    index,
                    total,
                    progress,
                ),
            )
        except Exception as exc:
            failures += 1
            print(f"[WARN] {target['slug']} の補完に失敗しました: {exc}", flush=True)
            continue

        total_added += int(stats["added"])
        print(
            f"[DONE {index}/{len(targets)}] {target['slug']} "
            f"added={stats['added']} existing={stats['existing']} skipped={stats['skipped']} total_html={stats['total_html']}",
            flush=True,
        )

    if failures:
        print(f"[WARN] {failures} 件の補完に失敗しました。 added={total_added}", flush=True)
        return 1

    print(f"[DONE] ordinances.sqlite の不足分を補完しました。 added={total_added}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
