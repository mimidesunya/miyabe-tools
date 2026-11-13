#!/usr/bin/env python3
"""トップページに出す「各地のお知らせ」の一覧を書く。

取得の 1 回の終わりに呼ぶ（`fetch_news_feeds.run`）。ためた項目から直近 7 日の
お知らせを拾い、全国と都道府県ごとの一覧を書く。web はこの小さなファイルを読むだけで、
項目の記録には触らない。

    work/news/latest/all.json        全国。1 自治体 1 件まで
    work/news/latest/<01〜47>.json   都道府県。1 自治体 3 件まで

並べ方:

- 日付の新しい順。同じ日付なら、見つけた時刻（時の単位）の新しい順。
- 同じ回に見つけたものは、項目の id（URL の SHA-1）の順にする。取得は自治体コードの
  順に回るので、見つけた時刻の順にすると毎回同じ地方が上に来る。id の順なら地方が混ざり、
  次の取得までは並びが変わらない。
- 日付の無い項目と、見つけた日より後の日付の項目（催しの日を日付にするフィード）は出さない。
- 同じ自治体の同じ題名・同じ日付は 1 つにする（新着と分野のフィードで URL が違うことがある）。

    python tools/news/latest_news.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.document_crawl import JST, load_master, write_json  # noqa: E402

DEFAULT_DATA_ROOT = WORKSPACE_ROOT / "data" / "news"
DEFAULT_WORK_ROOT = WORKSPACE_ROOT / "work" / "news"

WINDOW_DAYS = 7
LIST_SIZE = 20
NATIONWIDE_PER_MUNICIPALITY = 1
PREFECTURE_PER_MUNICIPALITY = 3
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
PREFECTURE_CODES = [f"{number:02d}" for number in range(1, 48)]


def recent_records(data_root: Path, since: datetime) -> list[dict]:
    """`since` より後に書いた行を、自治体と id ごとに最後の 1 行にして返す。

    項目の記録は追記だけなので、ファイルの後ろから読み、`since` より前の行に
    当たったらやめる。1 週間書いていないファイルは開かない。
    """
    since_text = since.strftime(TIME_FORMAT)
    since_stamp = since.timestamp()
    records: list[dict] = []
    if not data_root.is_dir():
        return records
    for directory in sorted(data_root.iterdir()):
        items = directory / "items"
        if not items.is_dir():
            continue
        latest: dict[str, dict] = {}
        paths = []
        for path in items.glob("*.jsonl"):
            try:
                if int(path.stem) >= since.year and path.stat().st_mtime >= since_stamp:
                    paths.append(path)
            except (ValueError, OSError):
                continue
        for path in sorted(paths, reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if str(record.get("first_seen_at") or "") < since_text:
                    break
                latest.setdefault(str(record.get("id")), record)
        records.extend(latest.values())
    return records


def eligible(record: dict, today: str, since_date: str) -> bool:
    date = str(record.get("date") or "")
    seen_date = str(record.get("first_seen_at") or "")[:10]
    url = str(record.get("url") or "")
    if not date or not record.get("title") or not url.startswith(("https://", "http://")):
        return False
    return since_date <= date <= min(today, seen_date)


def ordered(records: list[dict]) -> list[dict]:
    """並べ、同じ自治体の同じ題名・日付を 1 つにする。"""
    records = sorted(
        records,
        key=lambda record: (record["date"], str(record["first_seen_at"])[:13], str(record.get("id"))),
        reverse=True,
    )
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for record in records:
        key = (str(record.get("jis_code")), str(record["title"]), record["date"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def pick(records: list[dict], per_municipality: int, size: int = LIST_SIZE) -> list[dict]:
    counts: Counter[str] = Counter()
    chosen = []
    for record in records:
        code = str(record.get("jis_code"))
        if counts[code] >= per_municipality:
            continue
        counts[code] += 1
        chosen.append(record)
        if len(chosen) >= size:
            break
    return chosen


def entry(record: dict, master: dict[str, dict[str, str]]) -> dict[str, str]:
    code = str(record.get("jis_code"))
    row = master.get(code) or {}
    return {
        "date": record["date"],
        "title": str(record["title"]),
        "url": str(record["url"]),
        "jis_code": code,
        "name": str(row.get("name") or code),
        "pref_name": str(row.get("pref_name") or ""),
    }


def build_latest(*, data_root: Path = DEFAULT_DATA_ROOT, work_root: Path = DEFAULT_WORK_ROOT,
                 master: dict[str, dict[str, str]] | None = None, now: datetime | None = None) -> dict[str, int]:
    """一覧を書く。全国の件数と、お知らせのあった都道府県の数を返す。"""
    now = (now or datetime.now(JST)).astimezone(JST)
    master = master if master is not None else load_master()
    today = now.date()
    since_date = today - timedelta(days=WINDOW_DAYS - 1)
    since = datetime.combine(since_date, datetime.min.time(), tzinfo=JST)
    records = ordered([
        record for record in recent_records(data_root, since)
        if eligible(record, today.isoformat(), since_date.isoformat())
    ])
    generated_at = now.strftime(TIME_FORMAT)
    out_dir = work_root / "latest"

    def write(scope: str, chosen: list[dict], pref_name: str = "") -> None:
        payload = {
            "scope": scope,
            "pref_name": pref_name,
            "generated_at": generated_at,
            "since": since_date.isoformat(),
            "items": [entry(record, master) for record in chosen],
        }
        write_json(out_dir / f"{scope}.json", payload)

    nationwide = pick(records, NATIONWIDE_PER_MUNICIPALITY)
    write("all", nationwide)
    by_prefecture: dict[str, list[dict]] = {code: [] for code in PREFECTURE_CODES}
    for record in records:
        by_prefecture.setdefault(str(record.get("jis_code"))[:2], []).append(record)
    prefectures_with_items = 0
    for code in PREFECTURE_CODES:
        chosen = pick(by_prefecture[code], PREFECTURE_PER_MUNICIPALITY)
        prefectures_with_items += bool(chosen)
        pref_name = str((master.get(f"{code}000") or {}).get("pref_name") or "")
        write(code, chosen, pref_name)
    return {"latest_candidates": len(records), "latest_prefectures": prefectures_with_items}


def main() -> int:
    parser = argparse.ArgumentParser(description="トップページの「各地のお知らせ」の一覧を書く")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    args = parser.parse_args()
    result = build_latest(data_root=args.data_root, work_root=args.work_root)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
