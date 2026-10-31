"""事務事業評価の画面用の控え（build_display_data.py）を確かめる。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_display_data as b  # noqa: E402

SLUG = "99999-example-shi"
DOC_A = "a" * 16
DOC_B = "b" * 16


def field(text, label, value=None):
    return {"status": "present", "field_kind": "dedicated", "value": text if value is None else value,
            "raw_text": text, "raw_label": label, "evidence": [{"quote": text}]}


def record(record_id, doc_id, page, name, **fields):
    return {
        "record_id": record_id, "record_status": "provisional", "layout": "report",
        "source_document_ids": [doc_id],
        "record_evidence": [{"doc_id": doc_id, "locator": {"type": "pdf", "page": page, "bbox": [0, 0, 1, 1]}}],
        "fields": {"project_name": field(name, "事業名"), **fields},
        "raw_fields": [],
    }


class TextTest(unittest.TestCase):
    def test_wrapped_cell_lines_are_joined(self) -> None:
        self.assertEqual(b.join_wrapped_lines("あるけるまち\n八王子の実現"), "あるけるまち八王子の実現")
        self.assertEqual(b.join_wrapped_lines("所\n管\n課"), "所管課")
        self.assertEqual(b.join_wrapped_lines("サ\nービス"), "サービス")

    def test_sentence_ends_and_list_heads_keep_their_breaks(self) -> None:
        self.assertEqual(b.join_wrapped_lines("終えた。\n次は"), "終えた。\n次は")
        self.assertEqual(b.join_wrapped_lines("○目的\n○対象"), "○目的\n○対象")
        self.assertEqual(b.join_wrapped_lines("1. 一つ目\n2. 二つ目"), "1. 一つ目\n2. 二つ目")
        self.assertEqual(b.join_wrapped_lines("前段\n\n後段"), "前段\n\n後段")

    def test_vertical_label_is_one_line(self) -> None:
        self.assertEqual(b.clean_label("所\n管\n課\nの\n評\n価"), "所管課の評価")


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.work, self.records, self.out = root / "hyoka", root / "extracted", root / "out"
        work_dir = self.work / SLUG
        work_dir.mkdir(parents=True)
        documents = {
            DOC_A: {"text_status": "ok", "file_type": "pdf", "pages": 10, "kind": "project_evaluation", "fiscal_year": 2024,
                    "source_url": "https://example.jp/a.pdf"},
            DOC_B: {"text_status": "ok", "text_source": "ocr:ndlocr-lite", "file_type": "pdf", "pages": 5,
                    "kind": "project_evaluation", "fiscal_year": 2010},
            "c" * 16: {"text_status": "needs_ocr", "file_type": "pdf", "pages": 3, "kind": "overview"},
            "d" * 16: {"text_status": "needs_ocr", "file_type": "pdf", "pages": 4, "kind": "overview"},
            "e" * 16: {"text_status": "unreadable", "file_type": "zip", "kind": "external_review", "status": "gone"},
        }
        (work_dir / "manifest.json").write_text(json.dumps({
            "slug": SLUG, "jis_code": "99999", "name": "例県 例市", "entry_url": "https://example.jp/hyoka/",
            "last_run": {"status": "partial", "finished_at": "2026-10-29 20:00:00"}, "documents": documents,
        }, ensure_ascii=False), encoding="utf-8")
        (work_dir / "ocr_attempts.json").write_text(json.dumps({
            "c" * 16: {"attempts": 2, "status": "failed", "reason": "OCR が失敗"},
            "d" * 16: {"attempts": 1, "status": "failed", "reason": "OCR が失敗"},
        }), encoding="utf-8")
        (self.records / SLUG).mkdir(parents=True)
        rows = [
            record("r2", DOC_A, 3, "防災訓練事業",
                   department=field("総務部\n防災課", "担当\n課"),
                   future_direction=field("継続", "今後の\n方向性", {"raw_term": "継続"}),
                   purpose={"status": "represented_elsewhere", "field_kind": "embedded", "value": None}),
            record("r1", DOC_A, 1, "広報事業", target_year=field("令和6年度", "年度", 2024)),
            record("r3", "zipchild-0001", 1, "ZIP の中の事業"),
        ]
        rows[0]["raw_fields"] = [{"label": "事業\n概要", "content": field("訓練を\n年 2 回行う。", "事業概要")}]
        (self.records / SLUG / "records.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        self.log = root / "rebuild.log"
        self.log.write_text(
            "[COUNT] doc_type=hyoka total=2\n"
            + "[HYOKA] " + json.dumps({"slug": SLUG, "yielded": 2, "indexed_documents": 7, "unexplained_drop": 0}) + "\n"
            + "[DONE] index=miyabe-hyoka-v20261031071938 count=7\n", encoding="utf-8")

    def test_status_counts_each_stage(self) -> None:
        status = b.build(self.work, self.records, self.out, [self.log])
        row = status["municipalities"][0]
        self.assertEqual((row["documents"], row["text_ok"], row["ocr_done"], row["ocr_waiting"], row["ocr_failed"], row["no_text"]),
                         (5, 2, 1, 1, 1, 1))
        self.assertEqual((row["fetch_status"], row["indexed_documents"], row["records"], row["pages"]), ("partial", 2, 3, 22))
        self.assertEqual((row["year_min"], row["year_max"], row["gone"]), (2010, 2024, 1))
        self.assertEqual(status["index"]["index"], "miyabe-hyoka-v20261031071938")
        self.assertEqual(status["totals"]["searchable_municipalities"], 1)
        saved = json.loads((self.out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["totals"]["records"], 3)  # ZIP の中の文書の記録も数える

    def test_records_are_compacted_per_document_in_page_order(self) -> None:
        b.build(self.work, self.records, self.out, [self.log])
        saved = json.loads((self.out / "records" / SLUG / f"{DOC_A}.json").read_text(encoding="utf-8"))
        self.assertEqual([r["id"] for r in saved["records"]], ["r1", "r2"])
        self.assertEqual(saved["source_url"], "https://example.jp/a.pdf")
        r2 = saved["records"][1]
        self.assertEqual(r2["page"], 3)
        # 標準の順、欄名は原典のまま（折り返しを詰める）、文中の記載（embedded）は出さない
        self.assertEqual([(f["key"], f["label"], f["text"]) for f in r2["fields"]],
                         [("project_name", "事業名", "防災訓練事業"), ("department", "担当課", "総務部防災課"),
                          ("future_direction", "今後の方向性", "継続")])
        self.assertEqual(r2["raw_fields"], [{"label": "事業概要", "text": "訓練を年 2 回行う。"}])
        self.assertEqual(saved["records"][0]["fields"][1], {"key": "target_year", "name": "対象年度", "label": "年度", "text": "令和6年度"})
        # 索引に詳細画面の無い ZIP の中の文書は控えを作らない
        self.assertEqual(sorted(p.name for p in (self.out / "records" / SLUG).iterdir()), [f"{DOC_A}.json"])

    def test_stale_record_files_are_removed(self) -> None:
        stale = self.out / "records" / SLUG / f"{'f' * 16}.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}", encoding="utf-8")
        b.build(self.work, self.records, self.out, [])
        self.assertFalse(stale.exists())

    def test_empty_inputs_do_not_overwrite_the_display_data(self) -> None:
        with self.assertRaises(ValueError):
            b.build(self.work, self.out / "missing", self.out, [])
        empty_work = self.out / "empty-work"
        empty_work.mkdir(parents=True)
        with self.assertRaises(ValueError):
            b.build(empty_work, self.records, self.out, [])
        self.assertFalse((self.out / "status.json").exists())


if __name__ == "__main__":
    unittest.main()
