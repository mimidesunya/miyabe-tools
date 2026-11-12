from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools.tasks import backfill


class PreservePublishedAcknowledgementsTest(unittest.TestCase):
    def test_only_existing_ack_fields_are_merged_into_rebuilt_items(self) -> None:
        rebuilt = {
            "old": {"source_url": "https://new.example/", "progress_current": 12},
            "new": {"source_url": "https://brand-new.example/", "progress_current": 1},
        }
        previous = {
            "items": {
                "old": {
                    "source_url": "https://old.example/",
                    "progress_current": 9,
                    "input_fingerprint": "published-hash",
                    "scrape_generation": "generation-1",
                    "published_at": "2026-08-01 00:00:00",
                    "index_status": "ok",
                }
            }
        }
        with mock.patch.object(backfill.batch_status, "read_state", return_value=previous):
            backfill.preserve_published_acknowledgements("reiki", rebuilt)

        self.assertEqual("https://new.example/", rebuilt["old"]["source_url"])
        self.assertEqual(12, rebuilt["old"]["progress_current"])
        self.assertEqual("published-hash", rebuilt["old"]["input_fingerprint"])
        self.assertNotIn("input_fingerprint", rebuilt["new"])


REPO_ROOT = Path(__file__).resolve().parents[2]

# 本番の容器と同じく、時計を UTC にした子プロセスで読む。状況の記録の時刻
# （status.now_text）は日本時間で、タイムゾーンの印は無い。
CHILD_CHECK = """
import json, sys, time
from datetime import datetime, timedelta, timezone
from tools.tasks import backfill
tokyo = timezone(timedelta(hours=9))
now = datetime.now(tokyo)
two_hours_ago = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
just_now = now.strftime("%Y-%m-%d %H:%M:%S")
print(json.dumps({
    "stale_is_running": backfill.status_is_active_running(
        {"running": True, "heartbeat_at": two_hours_ago}, stale_seconds=900),
    "fresh_is_running": backfill.status_is_active_running(
        {"running": True, "heartbeat_at": just_now}, stale_seconds=900),
    "parsed": backfill.parse_status_timestamp("2026-11-11 14:32:00"),
}))
"""


class StatusTimestampTest(unittest.TestCase):
    """異常終了した実行の「実行中」の記録を、止まったと見分ける。

    2026-11-11 の停電で例規の周回の親が落ち、「実行中」の記録が残った。
    次の周回は始めに backfill.py で記録を片付けるが、backfill.py は日本時間の
    時刻を容器の時計（UTC）で読み、最後の生存記録（14:32）を 23:32 と読んだ。
    「まだ動いている」と断り続け、周回は 9 時間始まらなかった。
    """

    def test_status_time_is_tokyo_time(self) -> None:
        expected = datetime(2026, 11, 11, 5, 32, tzinfo=timezone.utc).timestamp()
        self.assertEqual(backfill.parse_status_timestamp("2026-11-11 14:32:00"), expected)
        # 印のある時刻はそのまま読む。
        self.assertEqual(backfill.parse_status_timestamp("2026-11-11T05:32:00Z"), expected)
        self.assertEqual(backfill.parse_status_timestamp("2026-11-11T14:32:00+09:00"), expected)
        self.assertIsNone(backfill.parse_status_timestamp(""))
        self.assertIsNone(backfill.parse_status_timestamp("soon"))

    def test_agrees_with_the_celery_side(self) -> None:
        # celery 側が「止まった」と判断して片付けを頼んだのに、backfill.py が
        # 「動いている」と断ると、片付けが永久に（記録が古くなるまで）進まない。
        from deploy.scraper_runtime.celery import runtime

        for text in ("2026-11-11 14:32:00", "2026-11-11T05:32:00Z", "2026-11-11T14:32:00+09:00"):
            self.assertEqual(backfill.parse_status_timestamp(text), runtime.parse_status_timestamp(text), text)

    def test_a_crashed_run_is_not_running_on_a_utc_clock(self) -> None:
        env = dict(os.environ, TZ="UTC0", PYTHONPATH=str(REPO_ROOT), PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", CHILD_CHECK],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertFalse(payload["stale_is_running"])
        self.assertTrue(payload["fresh_is_running"])
        self.assertEqual(
            payload["parsed"], datetime(2026, 11, 11, 5, 32, tzinfo=timezone.utc).timestamp()
        )


if __name__ == "__main__":
    unittest.main()
