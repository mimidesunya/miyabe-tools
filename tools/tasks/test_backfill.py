from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
