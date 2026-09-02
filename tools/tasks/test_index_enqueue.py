"""索引の積み重ね防止の印。

印は「積んだか実行中」を意味する。始まった時点で消すと、13〜28 分の
実行中に掃き取りが同じ自治体を積む。終わったら（成功でも失敗でも）消す。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deploy.scraper_runtime.celery import index_enqueue  # noqa: E402


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = (str(value), ex)
        return True

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def exists(self, key):
        return 1 if key in self.store else 0


class FakeChannel:
    def __init__(self, client):
        self.client = client


class FakeConnection:
    def __init__(self, client):
        self.default_channel = FakeChannel(client)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeApp:
    def __init__(self) -> None:
        self.redis = FakeRedis()
        self.sent: list[tuple[str, dict, str]] = []

    def connection_or_acquire(self):
        return FakeConnection(self.redis)

    def send_task(self, name, kwargs=None, queue=None):
        self.sent.append((name, dict(kwargs or {}), str(queue)))

        class Result:
            id = f"id-{len(self.sent)}"

        return Result()


class IndexEnqueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FakeApp()
        self.task = "deploy.scraper_runtime.celery.tasks.run_gijiroku_index_update"

    def test_second_enqueue_of_the_same_slug_is_dropped(self) -> None:
        self.assertTrue(index_enqueue.send_index_update(self.app, self.task, "q", "01100-sapporo-shi"))
        self.assertEqual(index_enqueue.send_index_update(self.app, self.task, "q", "01100-sapporo-shi"), "")
        self.assertEqual(len(self.app.sent), 1)

    def test_marker_survives_start_and_clears_at_end(self) -> None:
        index_enqueue.send_index_update(self.app, self.task, "q", "01100-sapporo-shi")
        index_enqueue.started(self.app, self.task, "01100-sapporo-shi")
        # 実行中は、掃き取りが同じ自治体を積めない。
        self.assertEqual(index_enqueue.send_index_update(self.app, self.task, "q", "01100-sapporo-shi"), "")
        key = index_enqueue.marker_key(self.task, "01100-sapporo-shi")
        self.assertEqual(self.app.redis.store[key][1], index_enqueue.RUNNING_TTL_SECONDS)
        index_enqueue.release(self.app, self.task, "01100-sapporo-shi")
        self.assertTrue(index_enqueue.send_index_update(self.app, self.task, "q", "01100-sapporo-shi"))

    def test_queued_marker_outlives_a_long_queue(self) -> None:
        # 待ち行列が 130 時間分あるとき、24 時間で印が消えると重複がまた積まれる。
        index_enqueue.claim(self.app, self.task, "27140-sakai-shi")
        key = index_enqueue.marker_key(self.task, "27140-sakai-shi")
        self.assertGreaterEqual(self.app.redis.store[key][1], 7 * 24 * 60 * 60)

    def test_failed_send_does_not_leave_a_marker(self) -> None:
        def broken(*args, **kwargs):
            raise RuntimeError("broker down")

        self.app.send_task = broken  # type: ignore[assignment]
        with self.assertRaises(RuntimeError):
            index_enqueue.send_index_update(self.app, self.task, "q", "01100-sapporo-shi")
        self.assertFalse(index_enqueue.is_held(self.app, self.task, "01100-sapporo-shi"))

    def test_unreachable_redis_still_enqueues(self) -> None:
        class Down:
            def connection_or_acquire(self):
                raise ConnectionError("no redis")

            def send_task(self, name, kwargs=None, queue=None):
                class Result:
                    id = "sent"

                return Result()

        self.assertEqual(index_enqueue.send_index_update(Down(), self.task, "q", "x"), "sent")


if __name__ == "__main__":
    unittest.main()
