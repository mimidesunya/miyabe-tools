#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import tempfile
import urllib.request

import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
from municipality_slugs import preferred_name_romaji

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )
}
MUNICIPALITY_SOURCE_URL = "https://raw.githubusercontent.com/code4fukui/localgovjp/master/localgovjp-utf8.csv"
PREFECTURE_SOURCE_URL = "https://raw.githubusercontent.com/code4fukui/localgovjp/master/prefjp-utf8.csv"
KANA_OVERRIDES_BY_CODE = {
    "01695": "しこたんむら",
    "01696": "とまりむら",
    "01697": "るよべつむら",
    "01698": "るべつむら",
    "01699": "しゃなむら",
    "01700": "しべとろむら",
}


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
        default=root / "data" / "municipalities" / "municipality_master.tsv",
        help="入力・更新対象の municipality_master.tsv",
    )
    parser.add_argument(
        "--homepage-csv",
        type=Path,
        default=root / "data" / "municipalities" / "municipality_homepages.csv",
        help="自治体ホームページ一覧 CSV",
    )
    parser.add_argument(
        "--municipality-source-url",
        default=MUNICIPALITY_SOURCE_URL,
        help="localgovjp 市区町村 CSV の URL",
    )
    parser.add_argument(
        "--prefecture-source-url",
        default=PREFECTURE_SOURCE_URL,
        help="localgovjp 都道府県 CSV の URL",
    )
    parser.add_argument(
        "--municipality-source-csv",
        type=Path,
        default=Path(tempfile.gettempdir()) / "localgovjp-utf8.csv",
        help="localgovjp 市区町村 CSV のローカルパス",
    )
    parser.add_argument(
        "--prefecture-source-csv",
        type=Path,
        default=Path(tempfile.gettempdir()) / "prefjp-utf8.csv",
        help="localgovjp 都道府県 CSV のローカルパス",
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


def ensure_source_csv(path: Path, url: str) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(request) as response, path.open("wb") as handle:
        handle.write(response.read())
    return path


def load_code4fukui_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader if isinstance(row, dict)]


def katakana_to_hiragana(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(ch)
    return "".join(chars)


def normalize_kana(text: str) -> str:
    value = katakana_to_hiragana(str(text).strip())
    value = "".join(ch for ch in value if ch not in {" ", "\t", "\r", "\n", "\u3000"})
    return value


def first_non_empty(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = normalize_kana(row.get(key, ""))
        if value:
            return value
    return ""


def load_name_kana_index(municipality_source_csv: Path, prefecture_source_csv: Path) -> dict[str, str]:
    kana_index: dict[str, str] = {}

    for row in load_code4fukui_rows(municipality_source_csv):
        lgcode = str(row.get("lgcode", "")).strip()
        if len(lgcode) < 5:
            continue
        code = lgcode[:5]
        kana = first_non_empty(row, "citykana", "kana", "namekana")
        if code and kana and code not in kana_index:
            kana_index[code] = kana

    for row in load_code4fukui_rows(prefecture_source_csv):
        pid = str(row.get("pid", "")).strip()
        if not pid.isdigit():
            continue
        code = f"{int(pid):02d}000"
        kana = first_non_empty(row, "prefkana", "citykana", "kana", "namekana")
        if code and kana and code not in kana_index:
            kana_index[code] = kana

    return kana_index


def main() -> int:
    args = parse_args()
    master_tsv = args.master_tsv.resolve()
    homepage_csv = args.homepage_csv.resolve()
    municipality_source_csv = ensure_source_csv(args.municipality_source_csv.resolve(), str(args.municipality_source_url))
    prefecture_source_csv = ensure_source_csv(args.prefecture_source_csv.resolve(), str(args.prefecture_source_url))
    out_file = (args.out_file or args.master_tsv).resolve()

    homepage_index = load_homepage_index(homepage_csv)
    name_kana_index = load_name_kana_index(municipality_source_csv, prefecture_source_csv)

    with master_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader if isinstance(row, dict)]

    if "name_kana" not in fieldnames:
        insert_at = fieldnames.index("name") + 1 if "name" in fieldnames else len(fieldnames)
        fieldnames.insert(insert_at, "name_kana")
    if "name_romaji" not in fieldnames:
        insert_at = fieldnames.index("full_name") + 1 if "full_name" in fieldnames else len(fieldnames)
        fieldnames.insert(insert_at, "name_romaji")

    source_counter: Counter[str] = Counter()
    kana_source_counter: Counter[str] = Counter()
    unresolved: list[tuple[str, str]] = []
    unresolved_kana: list[tuple[str, str]] = []

    for row in rows:
        code = str(row.get("jis_code", "")).strip()
        name = str(row.get("name", "")).strip()
        entity_type = str(row.get("entity_type", "")).strip()
        homepage_url = homepage_index.get(code, "")
        existing_kana = normalize_kana(str(row.get("name_kana", "")).strip())
        existing_romaji = str(row.get("name_romaji", "")).strip()

        kana = normalize_kana(name_kana_index.get(code, "")) or existing_kana or KANA_OVERRIDES_BY_CODE.get(code, "")
        if kana == "":
            unresolved_kana.append((code, name))
        else:
            if code in name_kana_index:
                kana_source_counter["localgovjp"] += 1
            elif existing_kana:
                kana_source_counter["existing"] += 1
            else:
                kana_source_counter["override"] += 1
            row["name_kana"] = kana

        romaji = preferred_name_romaji(
            code=code,
            name=name,
            entity_type=entity_type,
            homepage_url=homepage_url,
            name_romaji=existing_romaji,
        )
        if romaji == "":
            unresolved.append((code, name))
            continue

        if homepage_url:
            source_counter["homepage"] += 1
        else:
            source_counter["override"] += 1
        row["name_romaji"] = romaji

    if unresolved:
        unresolved_text = ", ".join(f"{code}:{name}" for code, name in unresolved[:20])
        raise SystemExit(f"Could not derive name_romaji for {len(unresolved)} rows: {unresolved_text}")
    if unresolved_kana:
        unresolved_text = ", ".join(f"{code}:{name}" for code, name in unresolved_kana[:20])
        raise SystemExit(f"Could not derive name_kana for {len(unresolved_kana)} rows: {unresolved_text}")

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
    print(
        "Derived name_kana from "
        + ", ".join(f"{source}={count}" for source, count in sorted(kana_source_counter.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
