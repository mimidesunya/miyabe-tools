"""会議録でない PDF を会議録として公開しない判定の回帰。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tools.gijiroku import gijiroku_storage, minutes_kind
from tools.gijiroku.scrapers import gikai_pdf, kami_city_pdf


IIZUKA_ANKEN = (
    "案件1\n"
    "窓口時間短縮について（総務委員会資料）\n"
    "試行開始時期令和8年10月1日\n"
    "提案理由 市民サービスの向上を図るため\n"
)

NAGAYO_BILL = "議案第６１号 財産の取得について\n提案理由\n別記様式\n"

ETAJIMA_COVER = "第3回定例会会議録\n表紙\n令和6年9月\n"

NAGAI_MINUTES = (
    "令和９年９月１８日木曜日\n"
    "決算特別委員会記録（第２号）\n"
    "出席議員（15名）\n"
    "会議録署名議員\n"
    "開議 午前10時00分\n"
)

TAGAWA_MINUTES = (
    "令和９年９月１２日（木）\n"
    "令和６年第３回田川市議会定例会会議録\n"
    "令和６年９月１２日　午前１０時０１分開議\n"
    "出席議員\n"
    "会議録署名議員\n"
)

YOICHI_MINUTES = (
    "令和８年余市町議会第１回定例会会議録（第５号）\n"
    "出席議員\n"
    "開議 午前10時\n"
    "会議録署名議員の指名\n"
)


class NonMinutesReasonTest(unittest.TestCase):
    def test_anken_title_is_not_minutes(self) -> None:
        self.assertEqual(
            minutes_kind.non_minutes_reason("案件1", IIZUKA_ANKEN),
            "non_minutes_label",
        )

    def test_digit_only_bill_is_not_minutes(self) -> None:
        self.assertEqual(
            minutes_kind.non_minutes_reason("61", NAGAYO_BILL),
            "non_minutes_body",
        )

    def test_digit_only_without_body_is_kept_for_later(self) -> None:
        # リンクが数字だけでも、本文が会議録なら題名を本文から作る。
        self.assertIsNone(minutes_kind.non_minutes_reason("61", ""))

    def test_newsletter_is_not_minutes(self) -> None:
        self.assertEqual(
            minutes_kind.non_minutes_reason("議会広報185号", "占冠村議会広報"),
            "non_minutes_label",
        )

    def test_cover_pdf_is_not_minutes(self) -> None:
        self.assertEqual(
            minutes_kind.non_minutes_reason("第3回定例会会議録（表紙 PDF）", ETAJIMA_COVER),
            "cover_only",
        )

    def test_real_minutes_are_kept(self) -> None:
        self.assertIsNone(minutes_kind.non_minutes_reason("開議", NAGAI_MINUTES))

    def test_publicity_committee_minutes_are_kept(self) -> None:
        title = "広報広聴委員会会議録"
        body = "広報広聴委員会会議録\n出席議員\n開議 午前10時\n会議録署名議員\n"
        self.assertIsNone(minutes_kind.non_minutes_reason(title, body))


class DisplayTitleTest(unittest.TestCase):
    def test_kaigi_link_uses_body_meeting_name(self) -> None:
        title = minutes_kind.minutes_display_title("開議-6cfc1df7", NAGAI_MINUTES)
        self.assertIn("決算特別委員会記録", title)

    def test_day_only_link_uses_body_meeting_name(self) -> None:
        title = minutes_kind.minutes_display_title("18日", YOICHI_MINUTES)
        self.assertIn("余市町議会第１回定例会会議録", title)

    def test_meaningful_link_is_kept(self) -> None:
        title = minutes_kind.minutes_display_title(
            "令和6年第1回定例会会議録", YOICHI_MINUTES
        )
        self.assertEqual(title, "令和6年第1回定例会会議録")


class HeldOnWeekdayTest(unittest.TestCase):
    def test_tagawa_ocr_nine_is_corrected_by_weekday_and_year_label(self) -> None:
        held_on = minutes_kind.extract_plausible_held_on(
            TAGAWA_MINUTES,
            title="第3回定例会（第4日 9月12日）",
            year_label="令和6年",
            filename="https://example.test/GetText3.exe?fileName=R060912A",
            today=date(2026, 8, 31),
        )
        self.assertEqual(held_on, "2024-09-12")

    def test_nagai_ocr_nine_is_corrected_by_weekday_and_year_label(self) -> None:
        held_on = minutes_kind.extract_plausible_held_on(
            NAGAI_MINUTES,
            title="開議-6cfc1df7",
            year_label="令和7年",
            filename="https://example.test/nagaigikai_kessan_R7_09_18_kaigi.pdf",
            today=date(2026, 8, 31),
        )
        self.assertEqual(held_on, "2025-09-18")

    def test_weekday_mismatch_without_year_label_is_dropped(self) -> None:
        held_on = minutes_kind.extract_plausible_held_on(
            "令和９年９月１２日（木）\n開議\n",
            today=date(2026, 8, 31),
        )
        self.assertIsNone(held_on)

    def test_year_label_gap_without_weekday_is_dropped(self) -> None:
        held_on = minutes_kind.extract_plausible_held_on(
            "令和９年１０月１日\n試行開始時期\n",
            year_label="令和6年",
            today=date(2026, 8, 31),
        )
        self.assertIsNone(held_on)


class CleanPdfLabelTest(unittest.TestCase):
    def test_fullwidth_pdf_file_note_is_stripped(self) -> None:
        self.assertEqual(
            kami_city_pdf.clean_pdf_label("香美市議会会議録［PDFファイル／248KB］"),
            "香美市議会会議録",
        )

    def test_halfwidth_slash_note_is_stripped(self) -> None:
        self.assertEqual(
            kami_city_pdf.clean_pdf_label("香美市議会会議録[PDFファイル/248KB]"),
            "香美市議会会議録",
        )


class GikaiPdfSkipsNonMinutesLinksTest(unittest.TestCase):
    def test_anken_cover_and_newsletter_are_not_collected(self) -> None:
        page = """
        <html><head><title>令和6年 会議録</title></head><body>
        <a href="anken1.pdf">案件1</a>
        <a href="bill61.pdf">61</a>
        <a href="cover.pdf">第3回定例会会議録（表紙 PDF）</a>
        <a href="koho.pdf">議会広報185号</a>
        <a href="minutes.pdf">令和6年第1回定例会会議録</a>
        </body></html>
        """
        walk: dict = {}
        with mock.patch.object(gikai_pdf, "request_text", lambda s, u, t, *a, **k: page):
            items = gikai_pdf.crawl_pdf_items(
                object(),
                "https://example.lg.jp/gikai/list18.html",
                timeout_ms=1000,
                max_pages=5,
                max_depth=1,
                walk=walk,
            )
        urls = sorted(item.url for item in items)
        self.assertEqual(
            urls,
            [
                "https://example.lg.jp/gikai/bill61.pdf",
                "https://example.lg.jp/gikai/minutes.pdf",
            ],
        )
        self.assertGreaterEqual(int(walk.get("dropped_non_minutes") or 0), 3)


class ExplainedShrinkGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "meetings_index.json"

    def test_explained_non_minutes_drop_replaces_the_plan(self) -> None:
        gijiroku_storage.save_meetings_index(self.path, [{"a": i} for i in range(100)])
        self.assertTrue(
            gijiroku_storage.meetings_index_would_shrink(
                self.path, [{"a": i} for i in range(30)]
            )
        )
        self.assertFalse(
            gijiroku_storage.meetings_index_would_shrink(
                self.path,
                [{"a": i} for i in range(30)],
                explained_drop_count=70,
            )
        )
        gijiroku_storage.save_meetings_index(
            self.path,
            [{"a": i} for i in range(30)],
            explained_drop_count=70,
        )
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 30)

    def test_unexplained_drop_is_still_refused(self) -> None:
        gijiroku_storage.save_meetings_index(self.path, [{"a": i} for i in range(100)])
        gijiroku_storage.save_meetings_index(
            self.path,
            [{"a": i} for i in range(30)],
            explained_drop_count=10,
        )
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 100)

    def test_body_drops_are_recorded_on_the_walk(self) -> None:
        work_dir = Path(tempfile.mkdtemp())
        gijiroku_storage.record_catalog_walk(
            work_dir,
            discovered=30,
            extra={
                "dropped_non_minutes": 70,
                "dropped_non_minutes_reasons": {"non_minutes_label": 70},
            },
        )
        gijiroku_storage.merge_dropped_non_minutes(
            work_dir, {"non_minutes_body": 5}
        )
        payload = gijiroku_storage.load_source_coverage(work_dir)
        self.assertEqual(payload.get("dropped_non_minutes"), 75)
        self.assertEqual(payload.get("dropped_non_minutes_reasons")["non_minutes_body"], 5)

    def test_incomplete_walk_does_not_explain_drops(self) -> None:
        self.assertEqual(
            gijiroku_storage.explained_non_minutes_drops(
                dropped_count=70, missed_pages=1
            ),
            0,
        )
        self.assertEqual(
            gijiroku_storage.explained_non_minutes_drops(
                dropped_count=70, limit_reached=True
            ),
            0,
        )
        self.assertEqual(
            gijiroku_storage.explained_non_minutes_drops(dropped_count=70),
            70,
        )


class HeldOnHeaderTest(unittest.TestCase):
    def test_composed_text_puts_held_on_for_indexer(self) -> None:
        item = kami_city_pdf.PdfMeetingItem(
            title="決算特別委員会記録（第２号）",
            url="https://example.test/nagaigikai_kessan_R7_09_18_kaigi.pdf",
            year_label="令和7年",
            source_year=2025,
            source_fino=None,
            page_url="https://example.test/list",
            page_title="会議録",
        )
        text = kami_city_pdf.composed_minutes_text(
            item, NAGAI_MINUTES, held_on="2025-09-18"
        )
        self.assertIn("Held-On: 2025-09-18", text)


if __name__ == "__main__":
    unittest.main()
