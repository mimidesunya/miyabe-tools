import unittest
from datetime import date

from tools.search import scraped_source_records


class ExtractHeldOnTest(unittest.TestCase):
    def test_explicit_iso_metadata_is_preserved(self) -> None:
        held_on, year, month, day = scraped_source_records.extract_held_on(
            "会議録\nHeld-On: 2026-02-24\nSource URL: https://example.test/",
            "会議録",
            None,
        )

        self.assertEqual((held_on, year, month, day), ("2026-02-24", 2026, 2, 24))

class FutureMinutesDateTest(unittest.TestCase):
    def test_future_date_is_rejected(self) -> None:
        self.assertIsNone(
            scraped_source_records.accept_minutes_date(
                "令和8年8月20日", 2027, 8, 19, "sample", today=date(2026, 8, 6)
            )
        )

    def test_today_is_accepted(self) -> None:
        self.assertEqual(
            scraped_source_records.accept_minutes_date(
                "令和8年8月6日", 2026, 8, 6, "sample", today=date(2026, 8, 6)
            ),
            ("2026-08-06", 2026, 8, 6),
        )

    def test_impossible_date_is_rejected(self) -> None:
        self.assertIsNone(
            scraped_source_records.accept_minutes_date(
                "2026年2月30日", 2026, 2, 30, "sample", today=date(2026, 8, 6)
            )
        )

class ClassifyDocTypeTest(unittest.TestCase):
    def test_title_ending_with_toc_is_toc(self) -> None:
        self.assertEqual(scraped_source_records.classify_doc_type("3月定例会－目次", "短い本文"), "toc")

    def test_short_document_with_toc_marker_is_toc(self) -> None:
        text = "会議録目次
第1 会議録署名議員の指名
" + "項目
" * 100
        self.assertEqual(scraped_source_records.classify_doc_type("3月定例会", text), "toc")

    def test_long_body_with_leading_toc_is_minutes(self) -> None:
        # 本文の冒頭に目次を載せる会議録がある。全体を目次扱いしない。
        text = "会議録目次
第1 会議録署名議員の指名
" + "◯議長　ただいまから本日の会議を開きます。
" * 3000
        self.assertGreater(len(text), scraped_source_records.TOC_TEXT_MAX_LENGTH)
        self.assertEqual(scraped_source_records.classify_doc_type("3月定例会", text), "minutes")


if __name__ == "__main__":
    unittest.main()
