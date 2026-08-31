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

    def test_a_real_bill_is_still_dropped(self) -> None:
        bill = "61\n\n議案第６１号\n　　財産の取得について\n提案理由\n"
        self.assertIsNotNone(minutes_kind.non_minutes_reason("61", bill))


if __name__ == "__main__":
    unittest.main()
