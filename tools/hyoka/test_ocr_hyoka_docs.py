"""事務事業評価の OCR の掃き取り（ocr_hyoka_docs.py）を確かめる。OCR の道具は呼ばず、差し替えた関数で試す。"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ocr_hyoka_docs as o  # noqa: E402


def make_municipality(root: Path) -> Path:
    work_dir = root / "99999-example-shi"
    (work_dir / "source").mkdir(parents=True)
    (work_dir / "source" / "a.pdf").write_bytes(b"%PDF-1.4 scanned a")
    (work_dir / "source" / "b.pdf").write_bytes(b"%PDF-1.4 scanned b")
    (work_dir / "source" / "c.pdf").write_bytes(b"%PDF-1.4 with text")
    manifest = {"documents": {
        "a": {"file": "source/a.pdf", "file_type": "pdf", "sha256": "x" * 64, "pages": 3, "text_status": "needs_ocr",
              "text_file": "", "title": "事務事業評価シート（総務課）", "source_url": "https://example.jp/a.pdf"},
        "b": {"file": "source/b.pdf", "file_type": "pdf", "sha256": "y" * 64, "pages": 2, "text_status": "needs_ocr",
              "text_file": ""},
        "c": {"file": "source/c.pdf", "file_type": "pdf", "sha256": "z" * 64, "pages": 1, "text_status": "ok",
              "text_file": "text/c.txt.gz"},
    }}
    (work_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return work_dir


def fake_ocr(path: Path):
    if path.name == "a.pdf":
        return "事業名 防災訓練事業\n今後の方向性 継続", ""
    return "", "OCR の結果が空だった"


class OcrSweepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work_dir = make_municipality(Path(self.tmp.name))

    def manifest(self) -> dict:
        return json.loads((self.work_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_scanned_pdf_gets_text_and_is_marked_ok(self) -> None:
        result = o.ocr_one_municipality(self.work_dir, ocr=fake_ocr)
        self.assertEqual((result.candidates, result.done, result.failed), (2, 1, 1))
        entry = self.manifest()["documents"]["a"]
        self.assertEqual((entry["text_status"], entry["text_source"], entry["text_file"]), ("ok", o.OCR_SOURCE, "text/a.txt.gz"))
        body = gzip.decompress((self.work_dir / "text" / "a.txt.gz").read_bytes()).decode("utf-8")
        self.assertIn("防災訓練事業", body)
        self.assertIn("出典: https://example.jp/a.pdf", body)  # 頭に出典の行
        # 原典は変えない。読めなかった物は OCR 待ちのまま。
        self.assertEqual((self.work_dir / "source" / "a.pdf").read_bytes(), b"%PDF-1.4 scanned a")
        self.assertEqual(self.manifest()["documents"]["b"]["text_status"], "needs_ocr")

    def test_failed_pdf_is_not_retried_forever(self) -> None:
        for _ in range(3):
            result = o.ocr_one_municipality(self.work_dir, ocr=fake_ocr)
        # 1 回目で a は済み、b は 2 回で打ち切り。3 回目は b を飛ばす。
        self.assertEqual((result.candidates, result.done, result.failed, result.skipped_attempts), (1, 0, 0, 1))

    def test_dry_run_writes_nothing(self) -> None:
        result = o.ocr_one_municipality(self.work_dir, dry_run=True, ocr=fake_ocr)
        self.assertEqual((result.candidates, result.pages), (2, 5))
        self.assertFalse((self.work_dir / "text").exists())
        self.assertEqual(self.manifest()["documents"]["a"]["text_status"], "needs_ocr")

    def test_limit_docs(self) -> None:
        result = o.ocr_one_municipality(self.work_dir, limit_docs=1, ocr=fake_ocr)
        self.assertEqual((result.candidates, result.done + result.failed), (2, 1))


if __name__ == "__main__":
    unittest.main()
