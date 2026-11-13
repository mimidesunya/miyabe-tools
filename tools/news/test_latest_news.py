"""トップページの「各地のお知らせ」の一覧。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.document_crawl import JST  # noqa: E402
from tools.news import latest_news  # noqa: E402

NOW = datetime(2026, 11, 14, 16, 5, tzinfo=JST)
MASTER = {
    "01000": {"name": "北海道", "pref_name": "北海道"},
    "01100": {"name": "札幌市", "pref_name": "北海道"},
    "13000": {"name": "東京都", "pref_name": "東京都"},
    "13101": {"name": "千代田区", "pref_name": "東京都"},
    "13102": {"name": "中央区", "pref_name": "東京都"},
}


def record(code: str, item_id: str, title: str, date: str | None, seen: str, revision: int = 0) -> dict:
    row = {"id": item_id, "jis_code": code, "url": f"https://www.example.jp/{code}/{item_id}.html",
           "title": title, "first_seen_at": seen, "revision": revision}
    if date:
        row["date"] = date
    return row


class LatestNewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "data"
        self.work_root = root / "work"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, code: str, records: list[dict], year: int = 2026) -> None:
        path = self.data_root / f"{code}-x" / "items" / f"{year}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in records:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def build(self) -> dict:
        return latest_news.build_latest(data_root=self.data_root, work_root=self.work_root, master=MASTER, now=NOW)

    def load(self, scope: str) -> dict:
        return json.loads((self.work_root / "latest" / f"{scope}.json").read_text(encoding="utf-8"))

    def titles(self, scope: str) -> list[str]:
        return [item["title"] for item in self.load(scope)["items"]]

    def test_only_dated_items_from_the_last_seven_days(self) -> None:
        self.write("13101", [
            record("13101", "a", "古い", "2026-11-07", "2026-11-07 10:00:00"),
            record("13101", "b", "範囲内", "2026-11-08", "2026-11-08 10:00:00"),
            record("13101", "c", "日付なし", None, "2026-11-14 13:00:00"),
            record("13101", "d", "催しの日", "2026-11-20", "2026-11-14 13:00:00"),
            record("13101", "e", "きょう", "2026-11-14", "2026-11-14 13:00:00"),
        ])
        result = self.build()
        self.assertEqual(self.titles("13"), ["きょう", "範囲内"])
        self.assertEqual(result["latest_candidates"], 2)
        self.assertEqual(self.load("13")["since"], "2026-11-08")

    def test_newest_date_then_newest_run_first(self) -> None:
        self.write("13101", [record("13101", "a", "前の日", "2026-11-13", "2026-11-14 13:00:00")])
        self.write("13102", [
            record("13102", "b", "朝の回", "2026-11-14", "2026-11-14 10:00:00"),
            record("13102", "c", "昼の回", "2026-11-14", "2026-11-14 13:00:00"),
        ])
        self.build()
        self.assertEqual(self.titles("13"), ["昼の回", "朝の回", "前の日"])

    def test_caps_per_municipality(self) -> None:
        self.write("13101", [record("13101", f"k{n}", f"千代田{n}", "2026-11-14", "2026-11-14 13:00:00") for n in range(5)])
        self.write("13102", [record("13102", "z", "中央", "2026-11-14", "2026-11-14 13:00:00")])
        self.build()
        nationwide = self.load("all")["items"]
        self.assertEqual(sorted(item["jis_code"] for item in nationwide), ["13101", "13102"])
        tokyo = [item["jis_code"] for item in self.load("13")["items"]]
        self.assertEqual(tokyo.count("13101"), latest_news.PREFECTURE_PER_MUNICIPALITY)

    def test_latest_revision_wins_and_same_title_is_shown_once(self) -> None:
        self.write("01100", [
            record("01100", "a", "人口（10月）", "2026-11-10", "2026-11-10 10:00:00"),
            record("01100", "b", "お知らせ", "2026-11-12", "2026-11-12 10:00:00"),
            record("01100", "c", "お知らせ", "2026-11-12", "2026-11-12 13:00:00"),
            record("01100", "a", "人口（11月）", "2026-11-14", "2026-11-14 10:00:00", revision=1),
        ])
        self.build()
        self.assertEqual(self.titles("01"), ["人口（11月）", "お知らせ"])

    def test_an_old_item_put_out_again_shows_with_its_new_date(self) -> None:
        # 前の年に見たページが、新しい日付で新着に出し直された（統計のページなど）。
        self.write("01100", [record("01100", "a", "統計", "2025-12-01", "2025-12-01 10:00:00")], year=2025)
        self.write("01100", [
            record("01100", "b", "前のお知らせ", "2026-11-01", "2026-11-01 10:00:00"),
            record("01100", "a", "統計", "2026-11-14", "2026-11-14 10:00:00", revision=1),
        ])
        self.build()
        self.assertEqual(self.titles("01"), ["統計"])
        self.assertEqual(self.load("01")["items"][0]["date"], "2026-11-14")

    def test_every_prefecture_file_is_written_with_names(self) -> None:
        self.write("01000", [record("01000", "a", "道のお知らせ", "2026-11-14", "2026-11-14 10:00:00")])
        result = self.build()
        files = sorted(path.stem for path in (self.work_root / "latest").glob("*.json"))
        self.assertEqual(len(files), 48)
        self.assertEqual(result["latest_prefectures"], 1)
        item = self.load("all")["items"][0]
        self.assertEqual((item["name"], item["pref_name"], item["date"]), ("北海道", "北海道", "2026-11-14"))
        self.assertEqual(self.load("13")["items"], [])
        self.assertEqual(self.load("13")["pref_name"], "東京都")
        self.assertEqual(self.load("all")["generated_at"], "2026-11-14 16:05:00")


if __name__ == "__main__":
    unittest.main()
