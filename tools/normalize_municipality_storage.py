#!/usr/bin/env python3
from __future__ import annotations

# 自治体コードを軸に、保存ディレクトリ名と background_tasks の slug を現行形へ揃える。

import argparse
import filecmp
import json
import os
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = TOOLS_DIR.parent
DATA_ROOT = WORKSPACE_ROOT / "data"
WORK_ROOT = WORKSPACE_ROOT / "work"
BACKGROUND_TASK_DIR = WORKSPACE_ROOT / "data" / "background_tasks"
BACKGROUND_TASK_FILES = (
    ("gijiroku", "gijiroku.json"),
    ("gijiroku", "gijiroku_snapshot.json"),
    ("reiki", "reiki.json"),
    ("reiki", "reiki_snapshot.json"),
)

sys.path.append(str(TOOLS_DIR))
sys.path.append(str(TOOLS_DIR / "gijiroku"))
sys.path.append(str(TOOLS_DIR / "reiki"))

import gijiroku_targets
import reiki_targets


def configure_roots(
    *,
    workspace_root: Path | None = None,
    data_root: Path | None = None,
    work_root: Path | None = None,
    background_task_dir: Path | None = None,
) -> None:
    global WORKSPACE_ROOT
    global DATA_ROOT
    global WORK_ROOT
    global BACKGROUND_TASK_DIR

    WORKSPACE_ROOT = (workspace_root or WORKSPACE_ROOT).resolve()
    DATA_ROOT = (data_root or DATA_ROOT).resolve()
    WORK_ROOT = (work_root or WORK_ROOT).resolve()
    BACKGROUND_TASK_DIR = (background_task_dir or BACKGROUND_TASK_DIR).resolve()

    for module in (gijiroku_targets, reiki_targets):
        module.WORKSPACE_ROOT = WORKSPACE_ROOT
        module.DATA_ROOT = DATA_ROOT
        module.WORK_ROOT = WORK_ROOT


def directory_code(name: str) -> str:
    stem = str(name).strip()
    if "-" not in stem:
        return ""
    prefix = stem.split("-", 1)[0].strip()
    return prefix if prefix.isdigit() else ""


# gijiroku/reiki ごとに「この code なら data/work のどこへ置くべきか」を作る。
def expected_directory_map() -> dict[str, dict[str, dict[str, Path]]]:
    expected: dict[str, dict[str, dict[str, Path]]] = {
        "gijiroku": {},
        "reiki": {},
    }

    for target in gijiroku_targets.iter_gijiroku_targets():
        code = str(target.get("code", "")).strip()
        if code == "":
            continue
        expected["gijiroku"][code] = {
            "data": Path(target["data_dir"]),
            "work": Path(target["work_dir"]),
        }

    for target in reiki_targets.iter_reiki_targets():
        code = str(target.get("code", "")).strip()
        if code == "":
            continue
        expected["reiki"][code] = {
            "data": Path(target["html_dir"]).parent,
            "work": Path(target["work_root"]),
        }

    return expected


def planned_directory_moves(
    collection: str,
    expected_by_code: dict[str, dict[str, Path]],
) -> list[tuple[Path, Path]]:
    collection_roots = {
        "data": DATA_ROOT / collection,
        "work": WORK_ROOT / collection,
    }
    moves: list[tuple[Path, Path]] = []

    for kind, root in collection_roots.items():
        if not root.exists():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            code = directory_code(child.name)
            if code == "" or code not in expected_by_code:
                continue

            target = expected_by_code[code][kind]
            if child == target:
                continue
            moves.append((child, target))

    return moves


def merge_directory_tree(source: Path, target: Path, dry_run: bool) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Cannot merge non-directory source: {source}")
    if target.exists() and not target.is_dir():
        raise RuntimeError(f"Cannot merge {source} into non-directory target: {target}")

    for root, dirnames, filenames in os.walk(source):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        target_root = target / relative_root
        conflict_root = target.parent / "__migration_conflicts__" / source.name / relative_root
        if not dry_run:
            target_root.mkdir(parents=True, exist_ok=True)

        for dirname in dirnames:
            destination_dir = target_root / dirname
            if destination_dir.exists() and not destination_dir.is_dir():
                raise RuntimeError(
                    f"Cannot merge {root_path / dirname} into non-directory target {destination_dir}"
                )
            if not dry_run:
                destination_dir.mkdir(parents=True, exist_ok=True)

        for filename in filenames:
            source_file = root_path / filename
            destination_file = target_root / filename
            if destination_file.exists():
                if not destination_file.is_file():
                    raise RuntimeError(
                        f"Cannot merge file {source_file} into non-file target {destination_file}"
                    )
                if not filecmp.cmp(source_file, destination_file, shallow=False):
                    archive_file = conflict_root / filename
                    print(
                        f"[CONFLICT] {source_file} -> {archive_file} (target kept: {destination_file})",
                        flush=True,
                    )
                    if not dry_run:
                        archive_file.parent.mkdir(parents=True, exist_ok=True)
                        source_file.replace(archive_file)
                    continue
                if not dry_run:
                    source_file.unlink()
                continue

            if not dry_run:
                destination_file.parent.mkdir(parents=True, exist_ok=True)
                source_file.replace(destination_file)

    if dry_run:
        return

    for directory in sorted(source.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    try:
        source.rmdir()
    except OSError as exc:
        raise RuntimeError(f"Merge completed but source directory is not empty: {source}") from exc


def apply_directory_moves(moves: list[tuple[Path, Path]], dry_run: bool) -> int:
    changed = 0
    for source, target in moves:
        if target.exists():
            print(f"[MERGE] {source} -> {target}", flush=True)
            merge_directory_tree(source, target, dry_run=dry_run)
            changed += 1
            continue

        print(f"[MOVE] {source} -> {target}", flush=True)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        changed += 1
    return changed


def slug_map_by_task() -> dict[str, dict[str, str]]:
    return {
        "gijiroku": {
            str(target.get("code", "")).strip(): str(target.get("slug", "")).strip()
            for target in gijiroku_targets.iter_gijiroku_targets()
            if str(target.get("code", "")).strip() != "" and str(target.get("slug", "")).strip() != ""
        },
        "reiki": {
            str(target.get("code", "")).strip(): str(target.get("slug", "")).strip()
            for target in reiki_targets.iter_reiki_targets()
            if str(target.get("code", "")).strip() != "" and str(target.get("slug", "")).strip() != ""
        },
    }


# task JSON は slug を複数箇所に持つので、code を頼りに全項目を一括で揃える。
def normalize_task_status_file(path: Path, slug_by_code: dict[str, str], dry_run: bool) -> bool:
    if not path.exists():
        return False

    loaded = json.loads(path.read_text(encoding="utf-8"))
    items = loaded.get("items")
    if not isinstance(items, dict):
        return False

    normalized_items: dict[str, Any] = {}
    changed = False

    for raw_key, raw_item in items.items():
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        code = str(item.get("code", "")).strip()
        current_slug = str(item.get("slug", raw_key)).strip()
        target_slug = slug_by_code.get(code, current_slug)
        if target_slug == "":
            continue
        if current_slug != target_slug or str(raw_key).strip() != target_slug:
            changed = True
        item["slug"] = target_slug
        existing = normalized_items.get(target_slug)
        if existing is not None and existing != item:
            raise RuntimeError(
                f"Task item collision in {path}: {target_slug!r} would be written twice."
            )
        normalized_items[target_slug] = item

    if not changed:
        return False

    loaded["items"] = normalized_items
    print(f"[TASK] {path} -> normalized {len(normalized_items)} items", flush=True)
    if not dry_run:
        path.write_text(
            json.dumps(loaded, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return True


def normalize_task_status_files(dry_run: bool) -> int:
    slug_maps = slug_map_by_task()
    changed = 0
    for task_name, filename in BACKGROUND_TASK_FILES:
        path = BACKGROUND_TASK_DIR / filename
        if normalize_task_status_file(path, slug_maps[task_name], dry_run=dry_run):
            changed += 1
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="自治体データの保存先を現行 slug に正規化し、background_tasks の slug も揃えます。"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="変更予定だけを表示して、実際のリネームや JSON 更新はしません。",
    )
    parser.add_argument(
        "--workspace-root",
        help="municipality マスタや config を読むワークスペース root。既定はこの script の親ディレクトリです。",
    )
    parser.add_argument(
        "--data-root",
        help="gijiroku/reiki の実データ root。既定は <workspace-root>/data です。",
    )
    parser.add_argument(
        "--work-root",
        help="gijiroku/reiki の work root。既定は <workspace-root>/work です。",
    )
    parser.add_argument(
        "--background-task-dir",
        help="background_tasks JSON の保存先。既定は <workspace-root>/data/background_tasks です。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else WORKSPACE_ROOT
    data_root = Path(args.data_root).resolve() if args.data_root else (workspace_root / "data")
    work_root = Path(args.work_root).resolve() if args.work_root else (workspace_root / "work")
    background_task_dir = (
        Path(args.background_task_dir).resolve()
        if args.background_task_dir
        else (workspace_root / "data" / "background_tasks")
    )
    configure_roots(
        workspace_root=workspace_root,
        data_root=data_root,
        work_root=work_root,
        background_task_dir=background_task_dir,
    )
    expected = expected_directory_map()

    move_count = 0
    for collection, expected_by_code in expected.items():
        moves = planned_directory_moves(collection, expected_by_code)
        move_count += apply_directory_moves(moves, dry_run=args.dry_run)

    task_count = normalize_task_status_files(dry_run=args.dry_run)
    print(
        f"[DONE] directory_moves={move_count} task_files={task_count}"
        + (" (dry-run)" if args.dry_run else ""),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
