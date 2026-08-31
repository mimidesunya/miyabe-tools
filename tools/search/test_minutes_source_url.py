"""会期名の年で照合して、取得済み会議録の原典 URL と開催日を落とさない。

本文冒頭の年は「会期名の年」であって開催年ではない。令和3年2月の会議に
「令和2年第2回定例会」と書かれていることがある。そこだけを照合キーにすると
一覧行に当たらず、`source_url` も `held_on` も空になる。取得は成功している
ので件数では検知できない。本番の北海道・札幌市で 917 件がこの形だった。

保存先のディレクトリ名は一覧行の年ラベルから作るが、改行や空白が `_` に
置き換わる（`平成31年・令和元年` → `平成31年・_令和元年`）。ここも合わせる。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scraped_source_records import (  # noqa: E402
    build_minutes_record,
    canonical_year_label,
    parse_minutes_source_meta,
)


URL = "https://www.gikai.example.jp/cgi-bin/index.cgi?YEAR=2021&FINO=8619"


class MinutesSourceUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.downloads = self.root / "downloads"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_index(self, rows: list[dict]) -> dict:
        path = self.root / "meetings_index.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return parse_minutes_source_meta(path)

    def _record(self, year_dir: str, meeting_dir: str, name: str, body: str):
        target = self.downloads / year_dir / meeting_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return build_minutes_record(target, self.downloads, self._meta, "2026-08-31T00:00:00Z")

    def test_session_year_differs_from_held_year(self) -> None:
        # 保存先は令和3年。本文冒頭は「令和2年第2回定例会」。
        self._meta = self._write_index(
            [
                {
                    "title": "02月03日-01号",
                    "year_label": "令和3年",
                    "url": URL,
                    "meeting_name": "少子・高齢社会対策特別委員会会議録",
                    "source_year": 2021,
                }
            ]
        )
        record = self._record(
            "令和3年",
            "少子・高齢社会",
            "02月03日-01号.txt",
            "令和2年第2回定例会\n少子・高齢社会対策特別委員会会議録\n令和3年（2021年）2月3日（水曜日）\n開会\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, URL)
        self.assertEqual(record.held_on, "2021-02-03")

    def test_year_dir_sanitised_from_index_label(self) -> None:
        # 一覧行は `平成31年・\n令和元年`、保存先は `平成31年・_令和元年`。
        self._meta = self._write_index(
            [
                {
                    "title": "05月21日-01号",
                    "year_label": "平成31年・\n令和元年",
                    "url": URL,
                    "meeting_name": "少子・高齢社会対策特別委員会会議録",
                    "source_year": 2019,
                }
            ]
        )
        record = self._record(
            "平成31年・_令和元年",
            "少子・高齢社会",
            "05月21日-01号.txt",
            "第31回 令和元年第1回少子・高齢社会対策特別委員会会議録\n令和元年（2019年）5月21日（火曜日）\n開会\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, URL)

    def test_canonical_year_label_drops_separators(self) -> None:
        self.assertEqual(canonical_year_label("平成31年・_令和元年"), "平成31年・令和元年")
        self.assertEqual(canonical_year_label("平成31年・\n令和元年"), "平成31年・令和元年")


if __name__ == "__main__":
    unittest.main()
