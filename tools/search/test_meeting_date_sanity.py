"""会議録の開催日として取り得ない日付を、検索に載せない。

まだ開かれていない会議の会議録は無い。未来の日付は、和暦の読み違い
（年度を年と取る）か、本文中の別の日付（期限や施行日）を拾った結果である。
実データで 20 件あり、横須賀市の 2025 年 12 月開催が 2026 年 12 月、
和光市は会議日 2023-05-19 なのに本文中の調査期限 2027-04-29 になっていた。
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_opensearch_index as builder  # noqa: E402


def days_from_now(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


class MeetingDateSanityTest(unittest.TestCase):
    def test_past_date_is_kept(self):
        self.assertEqual(builder.plausible_meeting_date("2025-12-22"), "2025-12-22")

    def test_today_is_kept(self):
        self.assertEqual(builder.plausible_meeting_date(days_from_now(0)), days_from_now(0))

    def test_future_date_is_dropped(self):
        self.assertIsNone(builder.plausible_meeting_date(days_from_now(30)))

    def test_far_future_date_is_dropped(self):
        self.assertIsNone(builder.plausible_meeting_date("2099-01-01"))

    def test_malformed_is_dropped(self):
        for value in ("", None, "2025-13-01", "令和7年12月22日"):
            self.assertIsNone(builder.plausible_meeting_date(value))

    def test_one_day_of_slack_is_allowed(self):
        # 時差と取得元の表記ゆれの余地。
        self.assertEqual(
            builder.plausible_meeting_date(days_from_now(1)), days_from_now(1)
        )


if __name__ == "__main__":
    unittest.main()
