import unittest

from tools.search import scraped_source_records


class ExtractHeldOnTest(unittest.TestCase):
    def test_explicit_iso_metadata_is_preserved(self) -> None:
        held_on, year, month, day = scraped_source_records.extract_held_on(
            "会議録\nHeld-On: 2026-02-24\nSource URL: https://example.test/",
            "会議録",
            None,
        )

        self.assertEqual((held_on, year, month, day), ("2026-02-24", 2026, 2, 24))


if __name__ == "__main__":
    unittest.main()
