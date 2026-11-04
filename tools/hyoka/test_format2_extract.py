#!/usr/bin/env python3
"""形 2.0 の抽出の部品を確かめる（AI は呼ばない）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "format2"))

import extract  # noqa: E402
import score  # noqa: E402

PAGE = {
    "page": 3, "garbled": False, "vertical": ["縦の字"],
    "tables": [{"cells": [
        {"r": 0, "c": 0, "rs": 1, "cs": 2, "text": "事業費（千円）"},
        {"r": 1, "c": 0, "rs": 1, "cs": 1, "text": "54,020"},
        {"r": 1, "c": 1, "rs": 1, "cs": 1, "text": ""},
    ]}],
    "lines": [{"y": 5, "x": 10, "text": "令和6年度"}],
}


class AddressTest(unittest.TestCase):
    def test_every_cell_gets_an_address(self) -> None:
        text, cells = extract.address_pages([PAGE])
        self.assertEqual(cells["p3t1r1c0"], "54,020")
        self.assertEqual(cells["p3l0"], "令和6年度")
        self.assertEqual(cells["p3v0"], "縦の字")
        self.assertIn("p3t1r0c0 (1x2): 事業費（千円）", text)
        self.assertNotIn("p3t1r1c1", cells)  # 空の升目は渡さない

    def test_canvas_page_keeps_rows_and_columns_visible(self) -> None:
        page = {"page": 1, "canvas": [
            {"l": 50.0, "t": 10.3, "s": 1, "x": "右"}, {"l": 5.0, "t": 10.0, "s": 1, "x": "左"}, {"l": 5.0, "t": 20.0, "s": 1, "x": "下"},
        ]}
        text, cells = extract.address_pages([page])
        self.assertEqual(cells["p1l0"], "左")
        self.assertIn("[10] p1l0=左@5 | p1l1=右@50", text)
        self.assertIn("[20] p1l2=下@5", text)

    def test_unreadable_page_says_so(self) -> None:
        text, cells = extract.address_pages([{"page": 2, "image_only": True}])
        self.assertIn("読めない", text)
        self.assertEqual(cells, {})


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        _, self.cells = extract.address_pages([PAGE])

    def test_good_value_is_kept(self) -> None:
        record = {"amounts": [{"raw": "54,020", "ref": "p3t1r1c0"}], "project": {}}
        result = extract.resolve(record, self.cells)
        self.assertEqual(len(result["record"]["amounts"]), 1)
        self.assertEqual(result["problems"], [])

    def test_value_not_in_the_cell_is_dropped(self) -> None:
        record = {"amounts": [{"raw": "54,200", "ref": "p3t1r1c0"}], "project": {}}
        result = extract.resolve(record, self.cells)
        self.assertEqual(result["record"]["amounts"], [])
        self.assertIn("値が升目に無い", result["problems"][0])

    def test_unknown_address_is_dropped(self) -> None:
        record = {"amounts": [{"raw": "54,020", "ref": "p9t9r9c9"}], "project": {}}
        self.assertIn("番地が無い", extract.resolve(record, self.cells)["problems"][0])

    def test_text_is_copied_from_the_cell_not_from_the_answer(self) -> None:
        record = {"project": {"name": ["p3l0"]}, "evaluations": [{"reason_refs": ["p3v0"]}]}
        result = extract.resolve(record, self.cells)["record"]
        self.assertEqual(result["project"]["name"], "令和6年度")
        self.assertEqual(result["evaluations"][0]["reason"], "縦の字")


class ScoreTest(unittest.TestCase):
    TRUTH = {
        "project": {"name": "広報広聴事業", "department": "広報課"},
        "amount_tables": [{
            "unit": "千円", "columns": [{"key": "R5", "fiscal_year": 2023, "stage": "settlement"}],
            "rows": [{"label": "総事業費", "scope": "total", "values": ["1,200"]}, {"label": "事業費", "scope": "direct", "values": ["1,000"]}],
        }],
        "indicators": [{"name": "相談件数", "kind": "activity", "columns": [2023], "target": ["10"], "actual": ["8"]}],
        "evaluations": [{"stage_raw": "課長評価", "actor": "課長", "status": "done", "direction": "maintain"}],
    }

    def answer(self) -> dict:
        return {
            "project": {"name": "広報広聴事業", "department": "総務部広報課"},
            "amounts": [
                {"fiscal_year": 2023, "raw": "1,200", "scope": "total", "unit": "千円", "stage": "settlement", "label": "総事業費"},
                {"fiscal_year": 2023, "raw": "1,000", "scope": "direct", "unit": "千円", "stage": "settlement", "label": "事業費"},
            ],
            "indicators": [{"name": "相談件数", "kind": "activity", "values": [{"fiscal_year": 2023, "target_raw": "10", "actual_raw": "8"}]}],
            "evaluations": [{"stage_raw": "課長評価", "actor": "課長", "status": "done", "direction": "maintain"}],
        }

    def test_full_match(self) -> None:
        result = score.score_sheet(self.TRUTH, self.answer())
        self.assertEqual(result["amounts"], (2, 2))
        self.assertEqual(result["indicators"], (2, 2))
        self.assertEqual(result["evaluations"], (1, 1))
        self.assertEqual(result["miss"] + result["wrong"], [])

    def test_wrong_scope_is_reported(self) -> None:
        answer = self.answer()
        answer["amounts"][0]["scope"] = "direct"
        self.assertTrue(any("scope" in w for w in score.score_sheet(self.TRUTH, answer)["wrong"]))

    def test_wrong_unit_is_reported(self) -> None:
        answer = self.answer()
        answer["amounts"][1]["unit"] = "円"
        self.assertTrue(any("unit" in w for w in score.score_sheet(self.TRUTH, answer)["wrong"]))

    def test_wrong_direction_is_reported(self) -> None:
        answer = self.answer()
        answer["evaluations"][0]["direction"] = "reduce"
        self.assertTrue(any("方向性" in w for w in score.score_sheet(self.TRUTH, answer)["wrong"]))

    def test_arithmetic_finds_a_shifted_value(self) -> None:
        answer = self.answer()
        answer["amounts"] = [
            {"fiscal_year": 2023, "stage": "settlement", "scope": "direct", "raw": "1,000"},
            {"fiscal_year": 2023, "stage": "settlement", "scope": "personnel", "raw": "200"},
            {"fiscal_year": 2023, "stage": "settlement", "scope": "total", "raw": "1,300"},
        ]
        self.assertEqual(len(score.arithmetic(answer)), 1)


class SchemaTest(unittest.TestCase):
    def test_every_object_lists_all_fields_as_required(self) -> None:
        def walk(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertIs(node["additionalProperties"], False)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(extract.SCHEMA)


if __name__ == "__main__":
    unittest.main()
