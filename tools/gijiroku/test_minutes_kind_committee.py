"""委員会記録を議案と読み違えない。

会議録でないものを落とす判定は、広すぎる方が危ない。取れていないより
「本物が消える」方が悪い。実データで測ったところ、札幌市の常任委員会記録
**963 件**が議案として落とされていた。

その委員会記録は「開議」「出席議員」「会議録署名」を一度も書かない。
書いてあるのは「開　会」「委員長」だけで、しかも語の中に全角空白が入る。
さらに審議しているので本文に「議案第○号」が出る。三つが重なって、
本会議の語彙だけを見る判定はこれを議案と読む。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gijiroku import minutes_kind  # noqa: E402


SAPPORO_COMMITTEE = """令和　２年（常任）厚生委員会－04月02日-記録

令和　２年（常任）厚生委員会
　　　　　　　　　　　札幌市議会厚生委員会記録
　　　　　　　　　　　令和２年４月２日（木曜日）
　　　　　　────────────────────────
　　　　　　開　会　午後１時９分
　　　　――――――――――――――
○阿部ひであき　委員長　　ただいまから、厚生委員会を開会いたします。
　議案第10号について、理事者から説明を受けます。
"""


class CommitteeRecordTest(unittest.TestCase):
    def test_committee_record_is_minutes(self) -> None:
        self.assertIsNone(minutes_kind.non_minutes_reason("04月02日-記録", SAPPORO_COMMITTEE))

    def test_spaced_markers_are_found(self) -> None:
        # 「開　会」は字下げのために語の中へ全角空白が入る。素のまま探すと当たらない。
        self.assertGreater(minutes_kind.minutes_marker_count(SAPPORO_COMMITTEE), 0)

    def test_body_naming_itself_a_record_is_enough(self) -> None:
        # 本文が「札幌市議会厚生委員会記録」と名乗っている。リンク文言が
        # 「04月02日-記録」でも、これは会議録である。
        title = minutes_kind.extract_meeting_title_from_text(SAPPORO_COMMITTEE)
        self.assertIsNotNone(title)
        self.assertTrue(minutes_kind.looks_like_minutes_title(title or ""))

    def test_material_titles_with_branch_numbers_are_dropped(self) -> None:
        # 飯塚市を取り直したら「案件1」は消えたが「案件1_補足資料」「案件2-2」
        # 「報告事項1-5」「…一覧表」が会議録として残っていた。番号のあとに
        # 枝番や添え名が付いても、会議録ではなく資料である。
        body = "窓口時間短縮について\n議案第79号\n提案理由\n"
        for title in (
            "案件1_補足資料",
            "案件2-2",
            "報告2-1",
            "報告事項1-5",
            "選挙管理委員及び選挙管理委員補充員選出一覧表",
        ):
            with self.subTest(title=title):
                self.assertIsNotNone(minutes_kind.non_minutes_reason(title, body))

    def test_meeting_paperwork_titles_are_dropped(self) -> None:
        # 飯塚市を取り直したあとも会議録の席に残っていたもの。会議に付いた
        # 書類であって、会議の記録ではない。
        body = "会期は8日間とする" + chr(10) + "議案第79号" + chr(10)
        for title in (
            "会期日程",
            "請願文書表",
            "陳情書",
            "議案一覧表",
            "付託表",
            "議員名簿（令和8年2月20日現在）（PDFファイル／104KB）",
        ):
            with self.subTest(title=title):
                self.assertIsNotNone(minutes_kind.non_minutes_reason(title, body))

    def test_a_record_that_merely_mentions_paperwork_is_kept(self) -> None:
        self.assertIsNone(
            minutes_kind.non_minutes_reason(
                "会期日程について協議した議会運営委員会記録", SAPPORO_COMMITTEE
            )
        )

    def test_a_date_only_title_is_not_dropped(self) -> None:
        # 「令和8年6月26日」は会議録の題名としてありうる。落とさない。
        self.assertIsNone(
            minutes_kind.non_minutes_reason("令和8年6月26日", SAPPORO_COMMITTEE)
        )

    def test_link_text_that_is_not_a_meeting_name_is_weak(self) -> None:
        # 保存名がリンク文言のままの取得元。会議の名前になっていない。
        # 本番で釧路町の「会議録」224 件、和木町の「初日」「最終日」、
        # 舟橋村の「招集告示」があった。
        body = (
            "釧路町議会定例会会議録" + chr(10) + "令和6年6月10日（月曜日）"
            + chr(10) + "開議" + chr(10) + "出席議員" + chr(10)
        )
        for title in ("会議録", "初日", "最終日", "招集告示", "第2日", "第3日目", "12月9日"):
            with self.subTest(title=title):
                self.assertEqual(
                    minutes_kind.minutes_display_title(title, body),
                    "釧路町議会定例会会議録",
                )

    def test_a_real_meeting_name_is_left_alone(self) -> None:
        body = "厚生委員会記録" + chr(10) + "開　会" + chr(10)
        self.assertEqual(
            minutes_kind.minutes_display_title("03月11日-01号", body), "03月11日-01号"
        )

    def test_letter_spaced_title_is_recognised(self) -> None:
        # PDF の題名は「招 集 告 示」のように字間へ空白を入れて組むことがある。
        # そのままでは弱い題名にも落とす題名にも当たらない（舟橋村）。
        body = "釧路町議会定例会会議録" + chr(10) + "開議" + chr(10)
        self.assertEqual(
            minutes_kind.minutes_display_title("招 集 告 示", body),
            "釧路町議会定例会会議録",
        )
        self.assertEqual(minutes_kind.squeeze_letter_spacing("招 集 告 示"), "招集告示")
        # 語の区切りは残す。「総務 委員会 記録」は詰めない。
        self.assertEqual(
            minutes_kind.squeeze_letter_spacing("総務 委員会 記録"), "総務 委員会 記録"
        )

    def test_ocr_repeated_title_is_folded(self) -> None:
        # PDF の OCR が段組を横に読むと同じ塊が続けて並ぶ。釧路町では
        # 「釧路町議会臨時会会議録」が 4 回繋がった題名が 7 件公開されていた。
        name = "釧路町議会臨時会会議録"
        self.assertEqual(minutes_kind.collapse_repeated_run(name * 4), name)
        self.assertEqual(minutes_kind.collapse_repeated_run(name), name)
        # 別々の語が並んでいるだけなら畳まない。
        self.assertEqual(
            minutes_kind.collapse_repeated_run("総務委員会会議録および建設委員会会議録"),
            "総務委員会会議録および建設委員会会議録",
        )

    def test_a_body_that_only_points_at_a_pdf_is_not_minutes(self) -> None:
        # 発言は PDF の中にあり、本文には案内文しか入っていない。
        # 会議録として検索に載るが、探している発言は出てこない。
        # 本番で町田市 529 件・岩見沢市 212 件・港区 131 件。
        notice = (
            "町田市議会会議録第22号　左下のＰＤＦファイルをごらんください。"
            "スマートフォンサイトでアクセスされた方は、ＰＣ版サイトをごらんください。"
        )
        self.assertEqual(
            minutes_kind.non_minutes_reason("12月10日-01号", notice), "pdf_notice_only"
        )
        self.assertEqual(
            minutes_kind.non_minutes_reason("09月29日-付録", "巻末資料"), "pdf_notice_only"
        )

    def test_a_short_body_with_a_real_opening_is_kept(self) -> None:
        # 短くても開議・出席議員があるなら会議の記録である。
        body = "町田市議会会議録" + chr(10) + "開議 午前10時" + chr(10) + "出席議員 30名"
        self.assertIsNone(minutes_kind.non_minutes_reason("12月10日-01号", body))

    def test_a_long_body_that_mentions_a_pdf_is_kept(self) -> None:
        body = "町田市議会会議録" + chr(10) + "開議" + chr(10) + "議員の発言。" * 80
        self.assertIsNone(minutes_kind.non_minutes_reason("12月10日-01号", body))

    def test_a_title_wrapped_in_parentheses_is_still_a_material(self) -> None:
        # 題名が丸ごと括弧に入っていることがある。`（資料）`（出席表）は
        # 本番で 18,733 件あり、うち 13,566 件は開催日も無い。
        body = "令和6年12月定例会（第4回）" + chr(10) + "出席表" + chr(10) + "議席番号 氏名"
        for title in ("（資料）", "(資料)", "【資料】", "資料"):
            with self.subTest(title=title):
                self.assertEqual(
                    minutes_kind.non_minutes_reason(title, body), "non_minutes_label"
                )

    def test_a_parenthesised_prefix_is_not_a_material(self) -> None:
        # 「（仮称）市民会館条例の審議」のような本物の題名は落とさない。
        for title in ("（仮称）市民会館条例の審議", "（令和6年第4回定例会）", "（議案）"):
            with self.subTest(title=title):
                self.assertIsNone(
                    minutes_kind.non_minutes_reason(title, SAPPORO_COMMITTEE)
                )

    def test_letter_spaced_material_titles_are_dropped(self) -> None:
        # 字間に空白を入れて組む取得元がある。弱い題名の判定では詰めていたのに、
        # 落とす題名の判定では詰めていなかった。
        for title in ("議 案 一 覧 表", "会 期 日 程", "請 願 文 書 表", "（ 資 料 ）"):
            with self.subTest(title=title):
                self.assertEqual(
                    minutes_kind.non_minutes_reason(title, "資料"), "non_minutes_label"
                )

    def test_a_real_bill_is_still_dropped(self) -> None:
        bill = "61\n\n議案第６１号\n　　財産の取得について\n提案理由\n"
        self.assertIsNotNone(minutes_kind.non_minutes_reason("61", bill))


if __name__ == "__main__":
    unittest.main()
