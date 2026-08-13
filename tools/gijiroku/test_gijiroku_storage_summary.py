import unittest

from tools.gijiroku import gijiroku_storage


class ClassifiedScrapeSummaryTest(unittest.TestCase):
    def test_all_pdfs_without_text_is_stated_plainly(self) -> None:
        # 初山別村は 29 件すべてが紙を画像で貼った PDF。会議録ではある。
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=29,
            downloaded_count=0,
            status_counts={"empty_pdf_text": 29},
        )
        self.assertEqual(
            summary["warning_lines"],
            ["取得元の PDF 29件はすべて文字情報を持たず、本文を取り出せません"],
        )

    def test_some_pdfs_without_text_is_reported_as_an_exclusion(self) -> None:
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=100,
            downloaded_count=90,
            status_counts={"empty_pdf_text": 10},
        )
        self.assertEqual(summary["warning_lines"], ["文字情報のない PDF を除外 10件"])

    def test_other_exclusions_keep_their_own_wording(self) -> None:
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=100,
            downloaded_count=90,
            status_counts={"empty_text": 10},
        )
        self.assertEqual(summary["warning_lines"], ["会議録本体ではない候補を除外 10件"])

    def test_both_kinds_are_listed_separately(self) -> None:
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=100,
            downloaded_count=80,
            status_counts={"empty_pdf_text": 12, "empty_text": 8},
        )
        self.assertEqual(
            summary["warning_lines"],
            ["文字情報のない PDF を除外 12件", "会議録本体ではない候補を除外 8件"],
        )


if __name__ == "__main__":
    unittest.main()
