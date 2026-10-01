"""会議録の対応系統は、スクレイパの対応表と同じものを見る。

Celery 側の一覧は手書きで、2026-10-01 時点で 独自（547 自治体）・amivoice・
msearch・iwate-kengikai が抜けていた。載らない系統は「未登録の対象」として
数えられず、残作業ありの短い周期が効かない。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deploy.scraper_runtime.celery import runtime  # noqa: E402
from tools.gijiroku import scrape_all_minutes  # noqa: E402


class GijirokuSupportedSystemsTest(unittest.TestCase):
    def test_it_matches_the_scraper_table(self) -> None:
        self.assertEqual(
            runtime.gijiroku_supported_systems(),
            {str(name) for name in scrape_all_minutes.SUPPORTED_INPUT_SYSTEMS},
        )

    def test_the_systems_missing_from_the_old_list_are_included(self) -> None:
        systems = runtime.gijiroku_supported_systems()
        for name in ("独自", "amivoice", "msearch", "iwate-kengikai", "voices"):
            self.assertIn(name, systems)


if __name__ == "__main__":
    unittest.main()
