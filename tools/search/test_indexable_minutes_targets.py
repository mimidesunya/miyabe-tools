"""会議録の索引に、会議録が無いと確かめた対象を載せない。

2026-09-19 に、公開なしで除外した大鹿村（議会だより 22 件）と坂祝町
（議案・予算書 34 件）が会議録として載っていた。除外前に落とした PDF が
残っていて、索引は除外した対象も含めて作っていたため。
robots で除外した対象は本物の会議録なので、これまでどおり載せる。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gijiroku"))

import build_opensearch_index as indexer  # noqa: E402
import gijiroku_targets  # noqa: E402


def target(slug: str, status: str, reason: str = "") -> dict:
    return {"slug": slug, "crawl_status": status, "exclusion_reason": reason}


class IndexableMinutesTargetsTest(unittest.TestCase):
    def test_only_targets_known_to_have_no_minutes_are_left_out(self):
        targets = [
            target("a-enabled", "enabled"),
            target("b-robots", "excluded", "robots_disallowed"),
            target("c-not-published", "excluded", "not_published"),
            target("d-video", "excluded", "video_only"),
            target("e-review", "review_required", "registry_changed"),
        ]
        with mock.patch.object(gijiroku_targets, "iter_gijiroku_targets", return_value=targets):
            slugs = [t["slug"] for t in indexer.indexable_minutes_targets()]

        self.assertEqual(slugs, ["a-enabled", "b-robots", "e-review"])


if __name__ == "__main__":
    unittest.main()
