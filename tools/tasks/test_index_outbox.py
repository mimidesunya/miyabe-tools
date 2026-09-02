"""索引投入の待ち行列。取得の成功で公開の成功を代用しない。

2026-08-31 に、例規の索引更新が未定義変数で全自治体・全実行落ちていた。
取得側は成功し続けていたので `last_checked_at` は進み、次に拾い直される
機会は 30 日後だった。決定的な失敗なので 30 日後も同じ失敗になる。
つまり放置しても永久に公開へ反映されない。

投げた時点ではなく、**索引が成功したと確認できたときだけ**待ち行列から消す。
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.tasks import index_outbox  # noqa: E402
from tools.tasks import status as batch_status  # noqa: E402


class IndexOutboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        batch_status.configure_status_root(self._tmp.name)

    def tearDown(self) -> None:
        batch_status.configure_status_root(None)
        self._tmp.cleanup()

    def test_dispatch_alone_does_not_clear_the_queue(self) -> None:
        index_outbox.record_pending("reiki", "14130-kawasaki-shi", task_id="t1")
        self.assertEqual(index_outbox.pending_count("reiki"), 1)

    def test_success_clears_it(self) -> None:
        index_outbox.record_pending("reiki", "14130-kawasaki-shi")
        index_outbox.mark_done("reiki", "14130-kawasaki-shi")
        self.assertEqual(index_outbox.pending_count("reiki"), 0)

    def test_not_due_immediately_but_due_after_the_wait(self) -> None:
        index_outbox.record_pending("minutes", "01000-hokkaido")
        self.assertEqual(index_outbox.due_slugs("minutes"), [])
        later = time.time() + index_outbox.DEFAULT_MIN_RETRY_SECONDS + 1
        self.assertEqual(index_outbox.due_slugs("minutes", now=later), ["01000-hokkaido"])

    def test_silent_loss_is_picked_up_too(self) -> None:
        # worker が強制終了されると失敗の記録すら残らない。「失敗した」ではなく
        # 「成功が確認できていない」で拾えること。
        index_outbox.record_pending("minutes", "01100-sapporo-shi", task_id="lost")
        later = time.time() + index_outbox.DEFAULT_MIN_RETRY_SECONDS + 1
        self.assertIn("01100-sapporo-shi", index_outbox.due_slugs("minutes", now=later))

    def test_wait_grows_with_attempts(self) -> None:
        first = index_outbox.retry_delay_seconds(0)
        second = index_outbox.retry_delay_seconds(1)
        third = index_outbox.retry_delay_seconds(2)
        self.assertLess(first, second)
        self.assertLess(second, third)
        self.assertLessEqual(
            index_outbox.retry_delay_seconds(100), index_outbox.DEFAULT_MAX_RETRY_SECONDS
        )

    def test_attempt_limit_slows_the_sweep_but_does_not_stop_it(self) -> None:
        # 失敗し続けるものを 6 時間おきに投げ直さない。ただし止めもしない。
        # 索引側の修正が配られたとき、人が投げ直さなくても拾えるように、
        # 3 日おきには試す。
        index_outbox.record_pending("reiki", "27210-hirakata-shi")
        for _ in range(index_outbox.DEFAULT_MAX_ATTEMPTS):
            index_outbox.mark_failed("reiki", "27210-hirakata-shi", "NameError")
        soon = time.time() + index_outbox.DEFAULT_MAX_RETRY_SECONDS + 1
        self.assertEqual(index_outbox.due_slugs("reiki", now=soon), [])
        self.assertIn("27210-hirakata-shi", index_outbox.stuck_entries("reiki"))
        later = time.time() + index_outbox.DEFAULT_STUCK_RETRY_SECONDS + 1
        self.assertEqual(index_outbox.due_slugs("reiki", now=later), ["27210-hirakata-shi"])

    def test_enqueueing_is_not_failing(self) -> None:
        # 索引キューが数日分あると、実行される前に何度も掃き取りの番が来る。
        # それを失敗と数えると、一度も走らないまま上限に達して止まる。
        # 本番で 61 自治体がそうなっていた。
        index_outbox.record_pending("minutes", "27140-sakai-shi", task_id="t1")
        for _ in range(index_outbox.DEFAULT_MAX_ATTEMPTS + 2):
            index_outbox.mark_enqueued("minutes", "27140-sakai-shi")
        self.assertNotIn("27140-sakai-shi", index_outbox.stuck_entries("minutes"))
        entry = index_outbox.all_entries("minutes")["27140-sakai-shi"]
        self.assertEqual(entry["attempts"], 0)
        self.assertEqual(entry["enqueues"], index_outbox.DEFAULT_MAX_ATTEMPTS + 3)

    def test_version_1_enqueue_counts_are_not_read_as_failures(self) -> None:
        # 版 1 は積んだ回数を attempts に入れていた。失敗の記録が無い attempts は
        # 積んだ回数として読み直し、上限に達したままにしない。
        path = index_outbox.outbox_path("minutes")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "minutes",
                    "entries": {
                        "27140-sakai-shi": {
                            "first_requested_at": 1.0,
                            "requested_at": 1.0,
                            "attempts": 8,
                            "last_attempt_at": 2.0,
                        },
                        "01662-akkeshi-town-cho": {
                            "first_requested_at": 1.0,
                            "requested_at": 1.0,
                            "attempts": 8,
                            "last_attempt_at": 2.0,
                            "last_error": "exit code 1",
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.assertEqual(sorted(index_outbox.stuck_entries("minutes")), ["01662-akkeshi-town-cho"])
        after_wait = 2.0 + index_outbox.DEFAULT_MAX_RETRY_SECONDS + 1
        self.assertIn("27140-sakai-shi", index_outbox.due_slugs("minutes", now=after_wait))
        self.assertNotIn("01662-akkeshi-town-cho", index_outbox.due_slugs("minutes", now=after_wait))

    def test_sweep_does_not_resend_the_same_slug_every_tick(self) -> None:
        index_outbox.record_pending("reiki", "40000-fukuoka-ken")
        later = time.time() + index_outbox.DEFAULT_MIN_RETRY_SECONDS + 1
        self.assertEqual(index_outbox.due_slugs("reiki", now=later), ["40000-fukuoka-ken"])
        index_outbox.mark_attempted("reiki", "40000-fukuoka-ken")
        self.assertEqual(index_outbox.due_slugs("reiki", now=later), [])

    def test_broken_file_does_not_stop_collection(self) -> None:
        index_outbox.outbox_path("reiki").parent.mkdir(parents=True, exist_ok=True)
        index_outbox.outbox_path("reiki").write_text("{ not json", encoding="utf-8")
        self.assertEqual(index_outbox.pending_count("reiki"), 0)
        index_outbox.record_pending("reiki", "01209-yubari-shi")
        self.assertEqual(index_outbox.pending_count("reiki"), 1)

    def test_minutes_and_reiki_are_separate(self) -> None:
        index_outbox.record_pending("minutes", "same-slug")
        index_outbox.mark_done("reiki", "same-slug")
        self.assertEqual(index_outbox.pending_count("minutes"), 1)


if __name__ == "__main__":
    unittest.main()
