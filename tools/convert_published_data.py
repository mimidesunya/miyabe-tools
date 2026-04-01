#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def gzip_path(path: Path) -> Path:
    return path if path.suffix.lower() == ".gz" else path.with_name(path.name + ".gz")


def compress_json_file(path: Path, *, dry_run: bool = False) -> tuple[bool, int, int]:
    dest = gzip_path(path)
    source_bytes = path.read_bytes()
    source_size = len(source_bytes)
    compressed_size = 0

    if dry_run:
        compressed_size = len(gzip.compress(source_bytes, compresslevel=6))
        return True, source_size, compressed_size

    with gzip.open(dest, "wb", compresslevel=6) as handle:
        handle.write(source_bytes)
    compressed_size = dest.stat().st_size
    path.unlink()
    return True, source_size, compressed_size


def convert_reiki_data(data_root: Path, *, dry_run: bool = False) -> dict[str, int]:
    summary = {
        "converted_files": 0,
        "source_bytes": 0,
        "compressed_bytes": 0,
    }

    reiki_root = data_root / "reiki"
    if not reiki_root.exists():
        return summary

    for json_file in sorted(reiki_root.glob("*/json/**/*.json")):
        converted, source_size, compressed_size = compress_json_file(json_file, dry_run=dry_run)
        if converted:
            summary["converted_files"] += 1
            summary["source_bytes"] += source_size
            summary["compressed_bytes"] += compressed_size

    return summary


def inspect_gijiroku_data(data_root: Path) -> dict[str, int]:
    gijiroku_root = data_root / "gijiroku"
    sqlite_files = 0
    other_files = 0

    if gijiroku_root.exists():
        for path in gijiroku_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".sqlite":
                sqlite_files += 1
            else:
                other_files += 1

    return {
        "sqlite_files": sqlite_files,
        "other_files": other_files,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert published data directories to the current compressed layout."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=project_root() / "data",
        help="Path to the published data root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be converted without modifying files.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_root = args.data_root.resolve()

    reiki_summary = convert_reiki_data(data_root, dry_run=args.dry_run)
    gijiroku_summary = inspect_gijiroku_data(data_root)

    print(f"data root: {data_root}")
    if args.dry_run:
        print("mode: dry-run")

    print(
        "reiki json converted: "
        f"{reiki_summary['converted_files']} files, "
        f"{reiki_summary['source_bytes']} -> {reiki_summary['compressed_bytes']} bytes"
    )
    print(
        "gijiroku files: "
        f"{gijiroku_summary['sqlite_files']} sqlite, "
        f"{gijiroku_summary['other_files']} other"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
