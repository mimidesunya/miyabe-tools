"""投入するかの判定は、取得キューに積まない。

取得 worker は concurrency 1 で、1 自治体を何日も掴むことがある。判定を
取得キューへ 1 分ごとに送ると、その間ずっと溜まる。2026-09-20 に例規キューは
25,360 件（ほぼ期限切れの判定）になっていて、手で投入した取得もその中に並んだ。
判定は共有の状態を読んで run_*_cycle を送るだけなので、maintenance（索引
worker）で動かす。取得そのものは今までどおり取得キューで動く。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # celery はスクレイパのコンテナにしか入っていない。
    from deploy.scraper_runtime.celery import app as celery_app
except ModuleNotFoundError:  # pragma: no cover - 手元では読み飛ばす
    celery_app = None

PREFIX = "deploy.scraper_runtime.celery.tasks."


@unittest.skipIf(celery_app is None, "celery が無い環境では確かめない")
class DispatchQueueRoutingTest(unittest.TestCase):
    @property
    def routes(self) -> dict:
        return celery_app.app.conf.task_routes

    @property
    def beat(self) -> dict:
        return celery_app.app.conf.beat_schedule

    def test_the_gate_runs_on_maintenance(self) -> None:
        for name in ("dispatch_gijiroku_cycle", "dispatch_reiki_cycle"):
            self.assertEqual(self.routes[PREFIX + name]["queue"], "maintenance", name)

    def test_the_gate_is_scheduled_on_maintenance(self) -> None:
        for name in ("dispatch-gijiroku-cycle", "dispatch-reiki-cycle"):
            self.assertEqual(self.beat[name]["options"]["queue"], "maintenance", name)

    def test_the_scraping_itself_still_runs_on_its_own_queue(self) -> None:
        self.assertEqual(self.routes[PREFIX + "run_gijiroku_cycle"]["queue"], celery_app.GIJIROKU_QUEUE)
        self.assertEqual(self.routes[PREFIX + "run_reiki_cycle"]["queue"], celery_app.REIKI_QUEUE)


if __name__ == "__main__":
    unittest.main()
