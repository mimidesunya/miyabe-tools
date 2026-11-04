#!/usr/bin/env python3
"""形 2.0 の正解（tools/hyoka/format2/truth）を確かめる。

人の目で読んだ値の写し間違いを、シートの中の検算で見つける。
`python -m unittest tools/hyoka/test_format2_truth.py`
"""

from __future__ import annotations

import json
import re
import unittest
from decimal import Decimal
from pathlib import Path

TRUTH_DIR = Path(__file__).resolve().parent / "format2" / "truth"
UNITS = {"円": 1, "千円": 1000, "万円": 10000, "百万円": 1000000, "億円": 100000000}
STAGES = {"budget", "budget_initial", "budget_current", "settlement", "estimate", "plan", "unstated"}
SCOPES = {"direct", "personnel", "personnel_regular", "personnel_other", "total", "sub_project", "breakdown"}
SOURCES = {"national", "prefecture", "bond", "fund", "user_fee", "other_specific", "general"}
STATUSES = {"zero", "dash", "blank", "hatched", "placeholder", "not_on_form", "unreadable"}
DIRECTIONS = {"expand", "continue", "maintain", "improve", "reduce", "integrate", "suspend", "abolish", "complete", "other"}
EVALUATION_STATUSES = {"done", "blank", "partial"}
INDICATOR_KINDS = {"target", "activity", "outcome", "unknown"}


def number(raw) -> Decimal | None:
    """シートの文字を数にする。値でない状態は None。"""
    if not isinstance(raw, str):
        return None
    text = raw.replace(",", "").replace("，", "").replace("%", "").strip()
    negative = text.startswith("▲")
    text = text.lstrip("▲ ")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    value = Decimal(text)
    return -value if negative else value


def yen(raw: str, unit: str) -> str:
    """円にそろえた十進の文字列。"""
    value = number(raw)
    if value is None or unit not in UNITS:
        raise ValueError(f"円にできない: {raw} {unit}")
    return format((value * UNITS[unit]).normalize(), "f")


def load_all() -> dict[str, dict]:
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(TRUTH_DIR.glob("*.json"))}


def column_keys(columns: list) -> list[str]:
    return [c["key"] if isinstance(c, dict) else c for c in columns]


def row_value(table: dict, label: str, column: str) -> Decimal:
    keys = column_keys(table["columns"])
    row = next(r for r in table["rows"] if r["label"] == label)
    value = number(row["values"][keys.index(column)])
    if value is None:
        raise AssertionError(f"{table['id']} の {label} の {column} が数でない")
    return value


class YenTest(unittest.TestCase):
    def test_units_become_yen(self) -> None:
        self.assertEqual(yen("2,976", "千円"), "2976000")
        self.assertEqual(yen("1.5", "百万円"), "1500000")
        self.assertEqual(yen("3", "億円"), "300000000")
        self.assertEqual(yen("18,000", "円"), "18000")

    def test_triangle_is_negative(self) -> None:
        self.assertEqual(number("▲ 25,918"), Decimal("-25918"))

    def test_marks_are_not_numbers(self) -> None:
        self.assertIsNone(number("－"))
        self.assertIsNone(number({"status": "blank"}))

    def test_unknown_unit_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            yen("100", "不明")


class TruthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.truths = load_all()

    def test_truth_files_exist(self) -> None:
        self.assertGreaterEqual(len(self.truths), 10)

    def test_every_value_has_a_known_shape(self) -> None:
        for name, truth in self.truths.items():
            with self.subTest(name):
                self.assertEqual(truth["format"], "2.0-truth")
                self.assertIn("doc_id", truth["source"])
                for table in truth.get("amount_tables") or []:
                    self.assertIn(table["unit"], UNITS)
                    self.assertTrue(table["unit_source"])
                    for column in table["columns"]:
                        self.assertIn(column["stage"], STAGES)
                    for row in table["rows"]:
                        self.assertIn(row["scope"], SCOPES)
                        self.assertEqual(len(row["values"]), len(table["columns"]), row["label"])
                        self.check_values(row["values"])
                for table in truth.get("funding_tables") or []:
                    self.assertIn(table["unit"], UNITS)
                    for row in table["rows"]:
                        self.assertIn(row["source"], SOURCES)
                        self.assertEqual(len(row["values"]), len(table["columns"]), row["label"])
                        self.check_values(row["values"])
                for indicator in truth.get("indicators") or []:
                    self.assertIn(indicator["kind"], INDICATOR_KINDS)
                    for key in ("target", "actual", "rate"):
                        if key in indicator:
                            self.assertEqual(len(indicator[key]), len(indicator["columns"]), indicator["name"])
                            self.check_values(indicator[key])
                for evaluation in truth.get("evaluations") or []:
                    self.assertIn(evaluation["status"], EVALUATION_STATUSES)
                    if "direction" in evaluation:
                        self.assertIn(evaluation["direction"], DIRECTIONS)

    def check_values(self, values: list) -> None:
        for value in values:
            if isinstance(value, dict):
                self.assertIn(value["status"], STATUSES)
            else:
                self.assertIsNotNone(number(value), value)

    def test_amounts_become_yen(self) -> None:
        for truth in self.truths.values():
            for table in truth.get("amount_tables") or []:
                for row in table["rows"]:
                    for value in row["values"]:
                        if isinstance(value, str):
                            self.assertTrue(yen(value, table["unit"]).isdigit())

    def test_sheet_arithmetic_holds(self) -> None:
        for name, truth in self.truths.items():
            tables = {t["id"]: t for t in truth.get("amount_tables") or []}
            funding = {t["id"]: t for t in truth.get("funding_tables") or []}
            staff = truth.get("staff") or {}
            for check in truth.get("checks") or []:
                with self.subTest(name, check=check["type"]):
                    self.run_check(truth, check, tables, funding, staff)

    def run_check(self, truth: dict, check: dict, tables: dict, funding: dict, staff: dict) -> None:
        kind = check["type"]
        if kind == "difference":
            self.assertEqual(number(check["to"]) - number(check["from"]), number(check["raw"]))
            return
        if kind == "rate":
            indicator = truth["indicators"][check["indicator"]]
            for target, actual, rate in zip(indicator["target"], indicator["actual"], indicator["rate"]):
                if number(target) and number(actual) is not None:
                    self.assertLess(abs(number(actual) / number(target) * 100 - number(rate)), Decimal("0.6"))
            return
        table = tables[check["table"]]
        for column in check["columns"]:
            total = row_value(table, check["total"], column)
            if kind == "sum":
                self.assertEqual(sum(row_value(table, part, column) for part in check["parts"]), total, column)
            elif kind == "funding_sum":
                source = funding[check["funding"]]
                keys = column_keys(source["columns"])
                values = [number(row["values"][keys.index(column)]) for row in source["rows"]]
                self.assertEqual(sum(v for v in values if v is not None), total, column)
            elif kind == "product":
                keys = column_keys(staff["columns"])
                left, right = (
                    number(next(r for r in staff["rows"] if r["label"] == label)["values"][keys.index(column)])
                    for label in check["staff"]
                )
                self.assertEqual(left * right, total, column)
            else:
                self.fail(f"知らない検算: {kind}")

    def test_a_wrong_digit_is_caught(self) -> None:
        truth = self.truths["13121-adachi-ku_4008.json"]
        truth["amount_tables"][0]["rows"][1]["values"][3] = "7,107"  # 事業費 7,017 の写し間違い
        tables = {t["id"]: t for t in truth["amount_tables"]}
        check = next(c for c in truth["checks"] if c["type"] == "sum")
        with self.assertRaises(AssertionError):
            self.run_check(truth, check, tables, {}, {})

    def test_every_truth_with_amounts_has_a_check_or_says_why(self) -> None:
        for name, truth in self.truths.items():
            with self.subTest(name):
                if truth.get("amount_tables"):
                    self.assertTrue(truth.get("checks") or truth.get("notes"))

    def test_known_gaps_are_real_gaps(self) -> None:
        for truth in self.truths.values():
            for gap in truth.get("known_gaps") or []:
                self.assertNotEqual(number(gap["total"]), number(gap["parts_sum"]))


if __name__ == "__main__":
    unittest.main()
