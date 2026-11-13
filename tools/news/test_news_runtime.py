"""新着情報の取得を、本番のスクレイパの組み立てにつないだところ。

取得は専用の worker（scraper-news）が news キューで受ける。会議録・例規の
取得 worker は 1 自治体を何日も掴むことがあり、索引 worker の待ち行列は
1,000 自治体単位になることがあるので、その後ろに並べない。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deploy.scraping_stack import build_scraping_compose  # noqa: E402

try:  # celery はスクレイパのコンテナにしか入っていない。
    from deploy.scraper_runtime.celery import app as celery_app
except ModuleNotFoundError:  # pragma: no cover - 手元では読み飛ばす
    celery_app = None

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


@unittest.skipIf(yaml is None, "PyYAML が無い環境では確かめない")
class NewsWorkerComposeTest(unittest.TestCase):
    def test_news_worker_mounts_the_shared_news_directories(self) -> None:
        text = build_scraping_compose(
            image_name="img", shared_data_dir="/mnt/big/miyabe-tools", uid="1000", gid="1000",
            gijiroku_loop_seconds=21600, reiki_loop_seconds=21600, fail_sleep_seconds=60,
        )
        service = yaml.safe_load(text)["services"]["scraper-news"]
        self.assertIn("/mnt/big/miyabe-tools/news:/workspace/data/news", service["volumes"])
        self.assertIn("/mnt/big/miyabe-tools/work/news:/workspace/work/news", service["volumes"])
        queues = service["command"][service["command"].index("-Q") + 1]
        self.assertEqual(queues, "news")


@unittest.skipIf(celery_app is None, "celery が無い環境では確かめない")
class NewsScheduleTest(unittest.TestCase):
    def test_fetch_is_scheduled_on_the_news_queue(self) -> None:
        entry = celery_app.app.conf.beat_schedule["fetch-news-feeds"]
        self.assertEqual(entry["task"], "deploy.scraper_runtime.celery.tasks.run_news_fetch")
        self.assertEqual(entry["options"]["queue"], celery_app.NEWS_QUEUE)
        self.assertEqual(
            celery_app.app.conf.task_routes["deploy.scraper_runtime.celery.tasks.run_news_fetch"]["queue"],
            celery_app.NEWS_QUEUE,
        )


if __name__ == "__main__":
    unittest.main()
