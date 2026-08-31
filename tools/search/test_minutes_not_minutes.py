"""議案・資料・広報・表紙を会議録として索引しない。ただし本物は落とさない。

取得側（`tools/gijiroku/minutes_kind.py`）だけを直しても、既にディスクにある
PDF は次の索引更新でまた会議録として出る。同じ判定を索引側でも通す。

**逆向きの危険の方が大きい。**判定が広すぎると本物の会議録が消える。
札幌市の常任委員会記録は「開議」「出席議員」を一度も書かず、「開　会」
「委員長」しか無い。この形の実データ 963 件を、最初の判定は議案として
落としていた。ここではその形を必ず残す。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scraped_source_records import classify_doc_type  # noqa: E402


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

IIZUKA_MATERIAL = """案件1

窓口時間短縮について
試行開始　令和8年10月1日
議案第79号 令和8年度補正予算書
提案理由
"""

NAGAYO_BILL = """61

議案第６１号
　　財産の取得について
　　　　　　　　　　　　　　　　　　　　提案理由
"""


class NotMinutesTest(unittest.TestCase):
    def test_committee_record_without_kaigi_markers_stays_minutes(self) -> None:
        # 「開　会」と「委員長」しか無い委員会記録。実データ 963 件がこの形。
        self.assertEqual(classify_doc_type("04月02日-記録", SAPPORO_COMMITTEE), "minutes")

    def test_committee_record_quoting_a_bill_stays_minutes(self) -> None:
        # 議案を審議しているので本文に「議案第10号」が出る。議案本文ではない。
        self.assertIn("議案第10号", SAPPORO_COMMITTEE)
        self.assertEqual(classify_doc_type("04月02日-記録", SAPPORO_COMMITTEE), "minutes")

    def test_anken_material_is_not_minutes(self) -> None:
        self.assertNotEqual(classify_doc_type("案件1", IIZUKA_MATERIAL), "minutes")

    def test_numeric_title_bill_is_not_minutes(self) -> None:
        self.assertNotEqual(classify_doc_type("61", NAGAYO_BILL), "minutes")

    def test_plain_minutes_still_minutes(self) -> None:
        text = "令和6年第2回定例会\n令和6年6月10日（月曜日）\n開議 午前10時\n出席議員 20名\n"
        self.assertEqual(classify_doc_type("06月10日-01号", text), "minutes")


class HeldOnSanityTest(unittest.TestCase):
    def test_ocr_year_is_corrected_by_the_weekday(self) -> None:
        from scraped_source_records import extract_held_on

        # OCR が「６」を「９」と読んだ。曜日「（木）」は 2024-09-12 と一致し、
        # 2027-09-12（日）とは一致しない。
        text = "田川市議会\n令和９年９月12日（木曜日）\n開議 午前10時\n出席議員 18名\n"
        held_on, _, _, _ = extract_held_on(
            text, "09月12日-01号", 2024, source_hint="R060912.pdf", year_label="令和6年"
        )
        self.assertEqual(held_on, "2024-09-12")

    def test_explicit_header_still_wins(self) -> None:
        from scraped_source_records import extract_held_on

        text = "Held-On: 2024-09-12\n令和９年９月12日（木曜日）\n開議\n"
        held_on, _, _, _ = extract_held_on(text, "x", 2024, source_hint="x", year_label="令和6年")
        self.assertEqual(held_on, "2024-09-12")


if __name__ == "__main__":
    unittest.main()
