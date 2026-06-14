#!/usr/bin/env python3
"""h-chosonkai（北海道町村会 例規集データベース）の自治体別 reiki.html URL を解決する。

houmu.h-chosonkai.gr.jp/~reikidb は 106 町村を 1 つの DB に同居させており、
reiki_system_urls.tsv では全自治体が共通の入口 URL を持っている。実体は
?choson_no=N でフレーム /~reikidb/data/{N}/{id}/reiki.html を読む D1-Law 静的版。

このスクリプトは入口ページの choson_no→自治体名（漢字）対応を読み、
master の自治体名で jis_code に突き合わせ、各町村のフレーム reiki.html URL を
解決して reiki_system_urls.tsv の該当行（system_type=h-chosonkai）を書き換える。
解決後は d1_law.py がそのまま取得できる。
"""

from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parents[2]
TSV = ROOT / "data" / "municipalities" / "reiki_system_urls.tsv"
MASTER = ROOT / "data" / "municipalities" / "municipality_master.tsv"
ENTRY = "http://houmu.h-chosonkai.gr.jp/~reikidb/"
CHOSON_LINK_RE = re.compile(r'(?is)<a[^>]*href="/~reikidb/\?choson_no=(\d+)"[^>]*>(.*?)</a>')
FRAME_RE = re.compile(r'(?i)<(?:frame|iframe)[^>]*src="?([^ ">]*reiki(?:_menu)?\.html?)"?')
UA = "Mozilla/5.0 (compatible; miyabe-tools/1.0)"


def load_rows(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fetch(session: requests.Session, url: str) -> str | None:
    try:
        r = session.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        print(f"[WARN] fetch failed {url}: {exc}")
        return None
    if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def main() -> int:
    session = requests.Session()
    entry_html = fetch(session, ENTRY)
    if entry_html is None:
        print("[ERROR] could not load entry page")
        return 1

    # choson_no -> 漢字名（リンク文字列は「かな 漢字」形式）
    name_to_no: dict[str, int] = {}
    for no, label in CHOSON_LINK_RE.findall(entry_html):
        text = re.sub(r"<[^>]+>", " ", label)
        text = re.sub(r"\s+", " ", text).strip()
        kanji = text.split(" ")[-1] if text else ""
        if kanji:
            name_to_no.setdefault(kanji, int(no))
    print(f"entry choson links: {len(name_to_no)} kanji names")

    master = {row["jis_code"]: row for row in load_rows(MASTER)}
    rows = load_rows(TSV)

    resolved = 0
    unmatched: list[str] = []
    for row in rows:
        if str(row.get("system_type", "")).strip() != "h-chosonkai":
            continue
        code = str(row.get("jis_code", "")).strip()
        name = str(master.get(code, {}).get("name", "")).strip()
        no = name_to_no.get(name)
        if no is None:
            unmatched.append(f"{code} {name}")
            continue
        page = fetch(session, urljoin(ENTRY, f"?choson_no={no}"))
        time.sleep(0.3)
        if page is None:
            unmatched.append(f"{code} {name} (page)")
            continue
        m = FRAME_RE.search(page)
        if not m:
            unmatched.append(f"{code} {name} (no frame)")
            continue
        reiki_url = urljoin(ENTRY, m.group(1))
        row["url"] = reiki_url
        resolved += 1
        print(f"  {code} {name} -> choson_no={no} {reiki_url}")

    with open(TSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"resolved={resolved} unmatched={len(unmatched)}")
    for item in unmatched:
        print(f"  [unmatched] {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
