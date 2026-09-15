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



class CompanionDocumentLabelTest(unittest.TestCase):
    """会議録と同じ一覧に並ぶ添え物は、本文として保存しない。"""

    def test_bill_name_list_is_not_minutes(self):
        self.assertEqual(minutes_kind.non_minutes_reason("議案件名", ""), "non_minutes_label")

    def test_vote_result_is_not_minutes(self):
        for label in ("審議結果", "議決結果", "採決結果", "３月定例会議決結果一覧"):
            self.assertEqual(minutes_kind.non_minutes_reason(label, ""), "non_minutes_label", label)

    def test_a_real_minutes_title_still_passes(self):
        for label in ("令和8年第1回定例会会議録", "会議録【初日】", "第2回臨時会会議録"):
            self.assertIsNone(minutes_kind.non_minutes_reason(label, ""), label)

    def test_schedules_results_and_newsletters_by_title(self):
        # 本番の汎用 PDF で会議録として公開されていた題名（直島町・赤井川村・村田町・
        # 中標津町・青木村・丹波山村・坂祝町）。後ろに容量や閉会日が付いている。
        for label in (
            "2022年6月 令和4年第2回定例会日程表",
            "2026年9月 令和8年第4回定例会審議内容（ＰＤＦ：102ＫＢ）",
            "第４回臨時会結果(令和２年１１月２７日閉会).pdf",
            "一般質問項目",
            "開催予定表（R8.9.3更新）",
            "議会概要（2026）【ＮＥＷ】",
            "・議会報９３号",
            "No.3 平成３０年 １月発行",
            "諸般の報告（議長報告）",
        ):
            self.assertEqual(minutes_kind.non_minutes_reason(label, ""), "non_minutes_label", label)

    def test_a_general_question_day_is_still_minutes(self):
        # gijiroku.com では 1 日の会議録の名前が「一般質問」になる。
        for label in ("一般質問", "09月10日-一般質問", "令和6年第2回定例会一般質問（第1日）"):
            self.assertIsNone(minutes_kind.non_minutes_reason(label, ""), label)


# 大鹿村: 保存名は「会議録」、中身は議会だより。
OOSHIKA_NEWSLETTER = (
    "不明\n会議録\n出典: https://www.vill.ooshika.nagano.jp/storage/2024/04/gikai_r02_09.pdf\n"
    "（1） 　大鹿村議会だより●第３4号\n令和２年９月\n大鹿村議会９月定例会\n"
    "令和２年９月大鹿村議会定例会が９月９日から18日までの1０日間の会期で開会されました。\n"
    "議案第１号 大鹿村税条例の一部を改正する条例の制定について\n"
)
# 丹波山村: 「議会」「だより」が別の行に割れている。本文に開会・閉会はある。
TABAYAMA_NEWSLETTER = (
    "平成29年\nNo.2 議会の報告\n出典: https://www.vill.tabayama.yamanashi.jp/gikai/files/tabagikai002_201710.pdf\n"
    "議会\nだより\nTopics\n9月定例会 ･･･ ２ ～7ページ\n2017.10\nNo.2\n"
    "村議会９月定例会は、 ９月13日に開会し、 15日に閉会いたしました。\n委員長報告\n"
)
# 赤井川村: 議事日程と議決事項だけ。「会議録署名議員の指名」で会議録の名乗りに当たる。
AKAIGAWA_RESULT = (
    "令和8年\n第１回臨時会\n出典: https://www.akaigawa.com/manage/wp-content/uploads/2026/03/x.pdf\n"
    "令和８年第１回赤井川村議会臨時会議事日程\n日時令和８年１月２９日午前10時00分\n"
    "場所赤井川村役場議場\n日程議事議決事項\n１会議録署名議員の指名２番連茂\n"
    "２会期の決定1月29日１日間\n３諸般の報告議長諸報告\n"
    "４議案第１号専決処分事項の承認を求めることについて\n原案承認\n"
)
# 南富良野町: 議会の会議録ではなく農業委員会の総会の議事録。
MINAMIFURANO_FARM = (
    "不明\n第4回総会議事録\n出典: https://www.town.minamifurano.hokkaido.jp/x.pdf\n"
    "1\n平成２８年\n第４回南富良野町農業委員会総会\n議事録\n"
    "開催日時平成２８年５月３１日１６時３０分\n議長\n只今の出席委員は、８名です。\n"
)
# 蕨市（gijiroku.com）: 1 日の会議録の議事日程の頁。発言は無いが会議録の一部。
WARABI_AGENDA_PAGE = (
    "06月05日-02号\n平成30年\nSource URL: http://warabi.gijiroku.com/x\n"
    "平成３０年第　２回定例会－06月05日-02号 なし(なし) P.15\n平成３０年第　２回定例会\n"
    "平成３０年第２回蕨市議会定例会\n議事日程（第５日）\n平成３０年６月５日\n午前１０時　開　議\n"
    "１　開　　議\n２　提出議案に対する質疑\n◇出席議員　　１８名\n◇欠席議員　なし\n"
    "午前１０時０分開議\n"
)


class NonMinutesBodyTest(unittest.TestCase):
    """題名では分からない添え物を、本文の冒頭で見分ける。"""

    def test_newsletter_named_minutes(self):
        self.assertEqual(
            minutes_kind.non_minutes_reason("会議録", OOSHIKA_NEWSLETTER), "newsletter_body"
        )

    def test_newsletter_split_across_lines(self):
        self.assertEqual(
            minutes_kind.non_minutes_reason("No.2 議会の報告", TABAYAMA_NEWSLETTER), "newsletter_body"
        )

    def test_newsletter_editing_committee_minutes_is_minutes(self):
        text = "議会だより編集委員会会議録\n出席委員 5名\n欠席委員 なし\n○委員長（山田君） 開会します。\n"
        self.assertIsNone(minutes_kind.non_minutes_reason("第3回編集委員会", text))

    def test_session_result_is_agenda_only(self):
        self.assertEqual(
            minutes_kind.non_minutes_reason("第１回臨時会", AKAIGAWA_RESULT), "agenda_only"
        )

    def test_farm_committee_is_not_assembly_minutes(self):
        self.assertEqual(
            minutes_kind.non_minutes_reason("第4回総会議事録", MINAMIFURANO_FARM), "non_assembly_minutes"
        )

    def test_agenda_page_of_real_minutes_is_kept(self):
        self.assertIsNone(minutes_kind.non_minutes_reason("06月05日-02号", WARABI_AGENDA_PAGE))

    def test_short_minutes_without_speaker_parens_is_kept(self):
        # 厚岸町: 休憩だけの日の会議録。発言者を「議長」とかっこ無しで書く。
        text = (
            "不明\n議長日程第１、会議録署名議員の指名を行います。\n出典: https://www.akkeshi-town.jp/x.pdf\n"
            "- 359 -\n議長ただいまより、平成16年厚岸町議会第１回定例会を続会いたします。\n"
            "開会時刻１０時００分\n議長直ちに本日の会議を開きます。\n"
            "本日の議事日程は、お手元に配付の日程表のとおりであります。\n"
            "議長日程第１、会議録署名議員の指名を行います。\n"
        )
        self.assertIsNone(
            minutes_kind.non_minutes_reason("議長日程第１、会議録署名議員の指名を行います。", text)
        )

    def test_agenda_and_body_title_is_minutes(self):
        # 勝浦市: 「議事日程・本文」は議事日程から始まる会議録そのもの。
        body = (
            "令和6年第1回勝浦市議会定例会会議録\n議事日程\n出席議員 17名\n欠席議員 なし\n"
            "○議長（佐藤君） これより会議を開きます。\n"
        )
        self.assertIsNone(minutes_kind.non_minutes_reason("議事日程・本文", body))
        self.assertIsNone(minutes_kind.non_minutes_reason("議事日程・本文", ""))
        # 名簿が続く形は従来どおり添え物。
        self.assertEqual(minutes_kind.non_minutes_reason("議事日程・名簿", ""), "non_minutes_label")

    def test_short_minutes_with_speakers_is_kept(self):
        text = (
            "令和8年第1回臨時会会議録\n議事日程\n"
            "○議長（山田太郎君） ただいまから会議を開きます。\n"
            "○町長（佐藤次郎君） 提案理由を申し上げます。\n"
        )
        self.assertIsNone(minutes_kind.non_minutes_reason("第1回臨時会", text))

    def test_budget_bill_split_across_lines(self):
        text = (
            "令和７年度坂祝町国民健康保険特別会計補正予算（第１号）\n"
            "令和７年度坂祝町の国民健康保険特別会計補正予算（第１号）は、次に定めるところ\n"
            "による。\n（歳入歳出予算の補正）\n"
        )
        self.assertEqual(
            minutes_kind.non_minutes_reason("3 補正予算国保(1号)", text), "non_minutes_body"
        )


if __name__ == "__main__":
    unittest.main()
