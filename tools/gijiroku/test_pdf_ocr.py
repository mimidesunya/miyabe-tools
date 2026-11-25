#!/usr/bin/env python3
"""OCR の有効化と、再試行の止め方を確かめる。

紙をスキャンしただけの PDF は本文を取り出せず、除外されたまま毎周回同じ
結果になる。OCR は 1 件に数十秒かかるので、通常の巡回では動かさない。
取れない PDF を毎周回 OCR し直すと、それだけで CPU を使い切る。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent))

import pdf_ocr


class EnabledTest(unittest.TestCase):
    def test_off_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(pdf_ocr.is_enabled())

    def test_turned_on_by_the_environment(self) -> None:
        for value in ("1", "true", "yes", "ON"):
            with mock.patch.dict(os.environ, {"MIYABE_MINUTES_OCR": value}):
                self.assertTrue(pdf_ocr.is_enabled(), value)
        for value in ("0", "false", "", "no"):
            with mock.patch.dict(os.environ, {"MIYABE_MINUTES_OCR": value}):
                self.assertFalse(pdf_ocr.is_enabled(), value)


class ToolLookupTest(unittest.TestCase):
    def test_missing_tool_reports_a_reason(self) -> None:
        with mock.patch.object(pdf_ocr, "tool_directory", return_value=None):
            body, reason = pdf_ocr.ocr_pdf_text(Path("dummy.pdf"))
        self.assertEqual(body, "")
        self.assertIn("NDLOCR-Lite", reason)

    def test_missing_pdf_reports_a_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(pdf_ocr, "tool_directory", return_value=Path(directory)):
                body, reason = pdf_ocr.ocr_pdf_text(Path(directory) / "none.pdf")
        self.assertEqual(body, "")
        self.assertIn("保存されていない", reason)


# NDLOCR-Lite の代わりに、決まった .txt を出すだけの ocr.py を置いて呼ぶ。
FAKE_OCR = '''
import argparse, pathlib
parser = argparse.ArgumentParser()
parser.add_argument("--sourcepdf")
parser.add_argument("--output")
parser.add_argument("--device")
args = parser.parse_args()
out = pathlib.Path(args.output)
for name, text in %r:
    (out / name).write_text(text, encoding="utf-8")
'''


class OutputFilesTest(unittest.TestCase):
    def run_fake(self, files: list[tuple[str, str]]) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "ndlocr-lite"
            (tool / "src").mkdir(parents=True)
            (tool / "src" / "ocr.py").write_text(FAKE_OCR % (files,), encoding="utf-8")
            pdf = Path(directory) / "会議録.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch.object(pdf_ocr, "tool_directory", return_value=tool), \
                    mock.patch.object(pdf_ocr, "python_executable", return_value=sys.executable), \
                    mock.patch.object(pdf_ocr, "pdf_page_count", return_value=3):
                return pdf_ocr.ocr_pdf_text(pdf)

    def test_pages_written_one_file_each_are_joined_in_order(self) -> None:
        # 1.3.0 から（2.0.0 も）の形。頁ごとの .txt を、名前の順（=頁の順）でつなぐ。
        body, reason = self.run_fake([
            ("input_00002.txt", "二頁目\n"),
            ("input_00010.txt", "十頁目\n"),
            ("input_00001.txt", "一頁目\n"),
        ])
        self.assertEqual(reason, "")
        self.assertEqual(body, "一頁目\n二頁目\n十頁目")

    def test_a_single_file_is_read_as_before(self) -> None:
        # 1.2 までの形（PDF 全体で input.txt の 1 つ）
        body, reason = self.run_fake([("input.txt", "全部の頁\n続き\n")])
        self.assertEqual(reason, "")
        self.assertEqual(body, "全部の頁\n続き")

    def test_no_text_file_is_reported(self) -> None:
        body, reason = self.run_fake([("input.json", "{}")])
        self.assertEqual(body, "")
        self.assertIn("テキストを出さなかった", reason)


class AttemptsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.work = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_first_time_is_always_tried(self) -> None:
        self.assertTrue(pdf_ocr.should_try(self.work, "a.pdf", "digest1"))

    def test_success_is_never_retried(self) -> None:
        pdf_ocr.record_attempt(self.work, "a.pdf", "digest1", status="ok")
        self.assertFalse(pdf_ocr.should_try(self.work, "a.pdf", "digest1"))

    def test_failures_stop_at_the_limit(self) -> None:
        for _ in range(pdf_ocr.MAX_ATTEMPTS):
            self.assertTrue(pdf_ocr.should_try(self.work, "a.pdf", "digest1"))
            pdf_ocr.record_attempt(self.work, "a.pdf", "digest1", status="failed", reason="読めない")
        self.assertFalse(pdf_ocr.should_try(self.work, "a.pdf", "digest1"))

    def test_a_replaced_pdf_is_tried_again(self) -> None:
        # 取得元が差し替えたら、前回の結果は当てにならない。
        for _ in range(pdf_ocr.MAX_ATTEMPTS):
            pdf_ocr.record_attempt(self.work, "a.pdf", "digest1", status="failed")
        self.assertFalse(pdf_ocr.should_try(self.work, "a.pdf", "digest1"))
        self.assertTrue(pdf_ocr.should_try(self.work, "a.pdf", "digest2"))

    def test_broken_attempt_file_is_ignored(self) -> None:
        pdf_ocr.attempts_path(self.work).write_text("{ broken", encoding="utf-8")
        self.assertTrue(pdf_ocr.should_try(self.work, "a.pdf", "digest1"))

    def test_digest_of_a_missing_file_is_empty(self) -> None:
        self.assertEqual(pdf_ocr.file_digest(self.work / "none.pdf"), "")


if __name__ == "__main__":
    unittest.main()
