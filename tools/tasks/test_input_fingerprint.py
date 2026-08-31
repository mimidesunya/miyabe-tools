from __future__ import annotations

import unittest
from unittest import mock

from tools.tasks import input_fingerprint as fingerprints


class InputFingerprintTest(unittest.TestCase):
    def test_minutes_aliases_share_the_canonical_fingerprint(self) -> None:
        voices = {"source_url": "https://example.test/path/", "system_type": "voices"}
        canonical = {"source_url": "https://example.test/path/", "system_type": "gijiroku.com"}
        self.assertEqual(
            fingerprints.input_fingerprint("minutes", voices),
            fingerprints.input_fingerprint("minutes", canonical),
        )

    def test_minutes_url_change_invalidates_the_generation(self) -> None:
        old = {"source_url": "https://old.example/", "system_type": "dbsr"}
        new = {"source_url": "https://new.example/", "system_type": "dbsr"}
        self.assertNotEqual(
            fingerprints.input_fingerprint("minutes", old),
            fingerprints.input_fingerprint("minutes", new),
        )

    def test_scraper_generation_is_part_of_the_hash(self) -> None:
        target = {"source_url": "https://example.test/", "system_type": "d1-law"}
        self.assertNotEqual(
            fingerprints.input_fingerprint("reiki", target, scraper_generation=1),
            fingerprints.input_fingerprint("reiki", target, scraper_generation=2),
        )

    def test_reiki_type_is_case_and_whitespace_normalized(self) -> None:
        left = {"source_url": "https://example.test/", "system_type": " G-Reiki "}
        right = {"source_url": "https://example.test/", "system_type": "g-reiki"}
        self.assertEqual(
            fingerprints.input_fingerprint("reiki", left),
            fingerprints.input_fingerprint("reiki", right),
        )

    def test_reiki_required_entry_url_is_part_of_the_hash(self) -> None:
        first = {
            "source_url": "https://example.test/",
            "entry_url": "https://example.test/reiki/",
            "system_type": "taikei",
        }
        second = {**first, "entry_url": "https://example.test/changed/"}
        self.assertNotEqual(
            fingerprints.input_fingerprint("reiki", first),
            fingerprints.input_fingerprint("reiki", second),
        )

    def test_legacy_item_accepts_same_input_but_rejects_registry_change(self) -> None:
        old_item = {
            "source_url": "https://example.test/",
            "system_type": "dbsr",
            "status": "snapshot",
            "returncode": 0,
        }
        self.assertTrue(fingerprints.fingerprint_matches_published("minutes", dict(old_item), old_item))
        changed = {**old_item, "source_url": "https://changed.example/"}
        self.assertFalse(fingerprints.fingerprint_matches_published("minutes", changed, old_item))

    def test_queued_index_state_is_not_treated_as_unpublished(self) -> None:
        # `queued` は投げた直後という意味で、失敗ではない。ここを未公開と読むと、
        # 取得の終わった自治体が毎周期やり直しになる。索引が落ちたときに
        # 投げ直すのは index_outbox の役目で、取得のやり直しではない。
        target = {"source_url": "https://example.test/", "system_type": "d1-law"}
        item = {
            **target,
            "index_status": "queued",
            "input_fingerprint": fingerprints.input_fingerprint("reiki", target),
        }
        self.assertTrue(fingerprints.fingerprint_matches_published("reiki", target, item))

    def test_failed_index_state_is_not_published(self) -> None:
        target = {"source_url": "https://example.test/", "system_type": "d1-law"}
        item = {
            **target,
            "index_status": "failed",
            "input_fingerprint": fingerprints.input_fingerprint("reiki", target),
        }
        self.assertFalse(fingerprints.fingerprint_matches_published("reiki", target, item))

    def test_legacy_generation_stops_matching_after_generation_bump(self) -> None:
        target = {"source_url": "https://example.test/", "system_type": "d1-law"}
        with mock.patch.dict("os.environ", {"MIYABE_REIKI_SCRAPER_GENERATION": "2"}):
            self.assertFalse(fingerprints.fingerprint_matches_published("reiki", target, dict(target)))


if __name__ == "__main__":
    unittest.main()
