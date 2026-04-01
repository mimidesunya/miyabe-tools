#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
from municipality_slugs import configured_slug_token_by_code, preferred_name_romaji


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="municipality_master.tsv に name_romaji 列を付与します。"
    )
    parser.add_argument(
        "--master-tsv",
        type=Path,
        default=root / "work" / "municipalities" / "municipality_master.tsv",
        help="入力・更新対象の municipality_master.tsv",
    )
    parser.add_argument(
        "--homepage-csv",
        type=Path,
        default=root / "work" / "municipalities" / "municipality_homepages.csv",
        help="自治体ホームページ一覧 CSV",
    )
    parser.add_argument(
        "--out-file",
        type=Path,
        default=None,
        help="出力先（未指定時は master-tsv を上書き）",
    )
    return parser.parse_args()


def load_homepage_index(path: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not isinstance(row, dict):
                continue
            code = str(row.get("jis_code", "")).strip()
            url = str(row.get("url", "")).strip()
            if code and url:
                index[code] = url
    return index


def main() -> int:
    args = parse_args()
    master_tsv = args.master_tsv.resolve()
    homepage_csv = args.homepage_csv.resolve()
    out_file = (args.out_file or args.master_tsv).resolve()

    configured_tokens = configured_slug_token_by_code(project_root())
    homepage_index = load_homepage_index(homepage_csv)

    with master_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader if isinstance(row, dict)]

    if "name_romaji" not in fieldnames:
        insert_at = fieldnames.index("full_name") + 1 if "full_name" in fieldnames else len(fieldnames)
        fieldnames.insert(insert_at, "name_romaji")

    source_counter: Counter[str] = Counter()
    unresolved: list[tuple[str, str]] = []

    for row in rows:
        code = str(row.get("jis_code", "")).strip()
        name = str(row.get("name", "")).strip()
        entity_type = str(row.get("entity_type", "")).strip()
        homepage_url = homepage_index.get(code, "")
        configured_token = configured_tokens.get(code, "")
        existing_romaji = str(row.get("name_romaji", "")).strip()

        romaji = preferred_name_romaji(
            code=code,
            name=name,
            entity_type=entity_type,
            homepage_url=homepage_url,
            configured_slug_token=configured_token,
            name_romaji=existing_romaji,
        )
        if romaji == "":
            unresolved.append((code, name))
            continue

        if code in configured_tokens:
            source_counter["config"] += 1
        elif homepage_url:
            source_counter["homepage"] += 1
        else:
            source_counter["override"] += 1
        row["name_romaji"] = romaji

    if unresolved:
        unresolved_text = ", ".join(f"{code}:{name}" for code, name in unresolved[:20])
        raise SystemExit(f"Could not derive name_romaji for {len(unresolved)} rows: {unresolved_text}")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_file}")
    print(
        "Derived name_romaji from "
        + ", ".join(f"{source}={count}" for source, count in sorted(source_counter.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
