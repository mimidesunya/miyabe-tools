"""例規の対応系統は、スクレイパの対応表と同じものを見る。

Celery 側の一覧は手書きで、`d1-law` `taikei` `g-reiki` の 3 つのまま
放置されていた。2026-09-19 に bk2reiki / wp-reiki / reiki-pdf を足したとき、
この一覧に載らない系統は「未登録の対象」として数えられず、残作業ありの
短い周期が効かない。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deploy.scraper_runtime.celery import runtime  # noqa: E402
from tools.reiki import scrape_all_reiki  # noqa: E402


class ReikiSupportedSystemsTest(unittest.TestCase):
    def test_it_matches_the_scraper_table(self) -> None:
        self.assertEqual(
            runtime.reiki_supported_systems(),
            {str(name) for name in scrape_all_reiki.SUPPORTED_SYSTEMS},
        )

    def test_the_new_systems_are_included(self) -> None:
        systems = runtime.reiki_supported_systems()
        for name in ("bk2reiki", "wp-reiki", "reiki-pdf", "legal-square"):
            self.assertIn(name, systems)


if __name__ == "__main__":
    unittest.main()
