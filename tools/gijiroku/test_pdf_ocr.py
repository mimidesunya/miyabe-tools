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
