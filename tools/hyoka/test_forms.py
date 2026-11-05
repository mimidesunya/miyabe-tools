"""様式ごとの読み取りの道具（tools/hyoka/forms）を確かめる。"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "forms"))

import cells  # noqa: E402
import check  # noqa: E402

LAYOUT = {
    "page": 2,
    "tables": [{"cells": [
        {"r": 0, "c": 0, "rs": 1, "cs": 1, "text": "事務事業名"},
        {"r": 0, "c": 1, "rs": 1, "cs": 2, "text": "道路維持事業"},
        {"r": 1, "c": 0, "rs": 2, "cs": 1, "text": "事業費 （単位：千円）"},
        {"r": 1, "c": 1, "rs": 1, "cs": 1, "text": "1,234"},
        {"r": 1, "c": 2, "rs": 1, "cs": 1, "text": "▲56"},
        {"r": 2, "c": 1, "rs": 1, "cs": 2, "text": "52300. 33.", "lines": ["52300.", "33."]},
    ]}],
    "lines": [{"y": 3, "x": 40, "text": "令和6年度 事務事業評価シート"}],
}


def document() -> cells.Document:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "14130-kawasaki-shi" / "layouts" / "abc.json.gz"
        path.parent.mkdir(parents=True)
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps({"doc_id": "abc", "pages": [2]}) + "\n" + json.dumps(LAYOUT) + "\n")
        return cells.load_document(path)


class CellsTest(unittest.TestCase):
    def test_neighbours_follow_spans(self) -> None:
        page = document().pages[0]
        head = page.find("事業費")
        self.assertEqual(page.right(head).text, "1,234")
        self.assertEqual(page.right(head, 2).text, "▲56")
        self.assertEqual(page.below(page.find("事務事業名")).text, "事業費 （単位：千円）")
        self.assertEqual(page.right(page.find("事務事業名")).ref, "p2t1r0c1")
        self.assertEqual([cell.text for cell in page.row(page.find("1,234"))], ["事業費 （単位：千円）", "1,234", "▲56"])

    def test_lines_inside_a_cell(self) -> None:
        cell = document().pages[0].find("52300")
        self.assertEqual(cell.lines, ("52300.", "33."))
        self.assertEqual([value["raw"] for value in cell.line_values()], ["52300.", "33."])

    def test_numbers_and_status(self) -> None:
        self.assertEqual(cells.number("1,234"), Decimal(1234))
        self.assertEqual(cells.number("▲56"), Decimal(-56))
        self.assertEqual(cells.number("１２．５"), Decimal("12.5"))
        self.assertIsNone(cells.number("－"))
        self.assertEqual(cells.status("－"), "dash")
        self.assertEqual(cells.status("0"), "zero")
        self.assertEqual(cells.status(""), "blank")

    def test_marks_and_years(self) -> None:
        self.assertEqual(cells.marked("☑市が直接実施 □一部委託 ■その他"), ["市が直接実施", "その他"])
        self.assertEqual(cells.marked("□一部委託 □その他"), [])
        self.assertEqual(cells.western_year("R6年度"), 2024)
        self.assertEqual(cells.western_year("平成28年度"), 2016)
        self.assertEqual(cells.western_year("令和元年度"), 2019)
        self.assertEqual(cells.western_year("6年度", era="R"), 2024)



KAWASAKI = Path(__file__).resolve().parent / "forms" / "readers" / "14130-kawasaki-shi.py"


class KawasakiForm2Test(unittest.TestCase):
    """川崎市の読み取りの形 2.0 の部品（評価のそろえ方、凡例、分け）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.reader = check.load_reader(KAWASAKI)

    def test_three_levels_split_the_scale(self) -> None:
        levels = self.reader.three_levels
        names = ("above", "on_target", "below")
        self.assertEqual([levels(rank, 5, names) for rank in range(1, 6)], ["above", "above", "on_target", "below", "below"])
        self.assertEqual([levels(rank, 3, names) for rank in range(1, 4)], ["above", "on_target", "below"])
        self.assertEqual([levels(rank, 4, names) for rank in range(1, 5)], ["above", "on_target", "on_target", "below"])
        self.assertEqual(levels(0, 5, names), "")
        self.assertEqual(levels(1, 1, names), "")

    def test_legend_options_by_question(self) -> None:
        options = self.reader.legend_options("１．目標を大きく上回って達成 ２．目標を上回って達成 ３．ほぼ目標どおり", "1-9")
        self.assertEqual(options, [{"1": "目標を大きく上回って達成", "2": "目標を上回って達成", "3": "ほぼ目標どおり"}])
        two = self.reader.legend_options("a.上がっている b.徐々に c.上がっていない a.薄れていない b.薄れている", "a-c")
        self.assertEqual(len(two), 2)
        self.assertEqual(self.reader.level_of(two[0]["c"]), "low")
        self.assertEqual(self.reader.level_of(two[1]["a"]), "high")
        self.assertEqual(self.reader.level_of("当てはまらない"), "")

    def test_variants_by_ward_or_repeated_base(self) -> None:
        def record(name: str) -> dict:
            return {"project": {"name": [{"raw": name, "ref": "p1t1r0c1"}]}}
        records = [record("地域見守り事業（川崎区）"), record("道路維持事業（1期）"), record("道路維持事業（2期）"),
                   record("広報事業（市政だより）"), record("公園整備事業")]
        self.reader.mark_variants(records)
        self.assertEqual(records[0]["variant"], {"base_name": "地域見守り事業", "label": "川崎区", "kind": "区"})
        self.assertEqual(records[1]["variant"]["kind"], "期")
        self.assertEqual(records[2]["variant"]["base_name"], "道路維持事業")
        self.assertNotIn("variant", records[3])
        self.assertNotIn("variant", records[4])

class HtmlLayoutTest(unittest.TestCase):
    def test_spans_and_outside_text(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "ai"))
        import html_layout

        raw = """<html><head><title>令和6年度 事務事業「道路維持事業」</title><script>x</script></head><body>
        <h1>評価結果の公表</h1><p>事業の概要<br>（単位：千円）</p>
        <table><tr><th rowspan="2">事業費</th><th colspan="2">R6年度</th></tr>
        <tr><td>予算額</td><td>決算額</td></tr>
        <tr><td>直接</td><td>1,234</td><td>1,100<br>（見込）</td></tr></table>
        </body></html>""".encode("cp932", "ignore").replace(b"<head>", b'<head><meta charset="Shift_JIS">')
        layout = html_layout.html_layout(raw)
        self.assertEqual(layout["title"], "令和6年度 事務事業「道路維持事業」")
        cells_by_text = {cell["text"]: cell for cell in layout["tables"][0]["cells"]}
        self.assertEqual((cells_by_text["事業費"]["rs"], cells_by_text["R6年度"]["c"], cells_by_text["R6年度"]["cs"]), (2, 1, 2))
        self.assertEqual((cells_by_text["予算額"]["r"], cells_by_text["予算額"]["c"]), (1, 1))
        self.assertEqual(cells_by_text["1,100 （見込）"]["lines"], ["1,100", "（見込）"])
        self.assertEqual([line["text"] for line in layout["lines"]], ["評価結果の公表", "事業の概要", "（単位：千円）"])

    def test_nested_table_is_its_own_table(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "ai"))
        import html_layout

        raw = "<table><tr><td>外<table><tr><td>内</td></tr></table></td></tr></table>".encode("utf-8")
        layout = html_layout.html_layout(raw)
        self.assertEqual(sorted(cell["text"] for table in layout["tables"] for cell in table["cells"]), ["内", "外"])


class CheckTest(unittest.TestCase):
    def test_refs_must_hold_the_raw_text(self) -> None:
        refs = document().refs()
        record = {"amounts": [{"raw": "1,234", "ref": "p2t1r1c1"}, {"raw": "9,999", "ref": "p2t1r1c1"}, {"raw": "1", "ref": "p9t1r0c0"}]}
        problems = check.ref_problems(record, refs)
        self.assertEqual(len(problems), 2)

    def test_funding_must_add_up_to_direct_cost(self) -> None:
        record = {"amounts": [{"fiscal_year": 2024, "stage": "budget", "scope": "direct", "raw": "100"}],
                  "funding": [{"fiscal_year": 2024, "stage": "budget", "raw": "60"}, {"fiscal_year": 2024, "stage": "budget", "raw": "30"}]}
        self.assertEqual(len(check.funding_problems(record)), 1)
        record["funding"].append({"fiscal_year": 2024, "stage": "budget", "raw": "10"})
        self.assertEqual(check.funding_problems(record), [])


if __name__ == "__main__":
    unittest.main()
