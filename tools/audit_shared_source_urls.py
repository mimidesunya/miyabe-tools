#!/usr/bin/env python3
"""同じ取得元 URL が複数の自治体に登録されていないか調べる。

同名の自治体があると、片方の URL をもう片方にも登録してしまう。すると、
**別の自治体の文書が、その自治体のものとして検索に出る**。取れていない
なら空欄になるが、これは誤った内容が正しい名前で表示される。

実際に 2 組あった:

- 北海道泊村 01403 と 01696 に同じ URL。北海道に泊村は 1 つしかない
- 長野県川上村 20304 と 奈良県川上村 29452 に同じ URL。別の村なのに、
  奈良県川上村として長野県川上村の例規 621 件が公開されていた

collection-gap-survey.md の型 F（1 自治体 = 1 URL）とは逆の形で、
**1 URL = 複数自治体**になっている。
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import defaultdict
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "municipalities"
REGISTRIES = [
    (DATA_ROOT / "reiki_system_urls.tsv", "例規"),
    (DATA_ROOT / "assembly_minutes_system_urls.tsv", "会議録"),
]


def load_master() -> dict[str, tuple[str, str]]:
    master: dict[str, tuple[str, str]] = {}
    path = DATA_ROOT / "municipality_master.tsv"
    if not path.is_file():
        return master
    with io.open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            if code:
                master[code] = (
                    str(row.get("pref_name", "")).strip(),
                    str(row.get("name", "") or row.get("full_name", "")).strip(),
                )
    return master


def shared_urls(path: Path) -> list[tuple[str, list[str]]]:
    """同じ URL を持つ、除外されていない自治体の組を返す。"""
    if not path.is_file():
        return []
    by_url: dict[str, list[str]] = defaultdict(list)
    with io.open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            url = str(row.get("url", "")).strip()
            code = str(row.get("jis_code", "")).strip()
            status = str(row.get("crawl_status", "")).strip()
            if url and code and status != "excluded":
                by_url[url].append(code)
    return [(url, codes) for url, codes in by_url.items() if len(codes) > 1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="同じ取得元 URL が複数の自治体に登録されていないか調べます。"
    )
    parser.parse_args()

    master = load_master()
    total = 0
    for path, label in REGISTRIES:
        shared = shared_urls(path)
        total += len(shared)
        print(f"■ {label}: 同じ URL を使う自治体の組 {len(shared)}")
        for url, codes in shared:
            names = " / ".join(
                f"{code} {''.join(master.get(code, ('', '')))}" for code in codes
            )
            print(f"   {names}")
            print(f"      {url}")

    if total:
        print(
            "\n別の自治体の文書が、その自治体のものとして公開されている可能性があります。"
            "どちらが正しいか調べ、誤っている側の登録を外してください。",
            file=sys.stderr,
        )
        return 1
    print("\n重複した登録はありません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
