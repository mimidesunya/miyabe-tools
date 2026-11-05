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

    def overlay(self) -> dict:
        """調査から集め直した保存先にだけある自治体と、その様式ごとの読み取りの記録。"""
        root = self.work.parent
        slug = "14130-kawasaki-shi"
        work_dir = root / "hyoka2" / slug
        (root / "hyoka2-ai" / slug / "layouts").mkdir(parents=True)
        work_dir.mkdir(parents=True)
        (work_dir / "manifest.json").write_text(json.dumps({
            "slug": slug, "jis_code": "14130", "name": "神奈川県 川崎市", "last_run": {"status": "ok"},
            "documents": {DOC_A: {"text_status": "ok", "file_type": "pdf", "pages": 4, "fiscal_year": 2025, "source_url": "https://k.jp/a.pdf"}},
        }, ensure_ascii=False), encoding="utf-8")
        (root / "hyoka2-ai" / slug / "layouts" / f"{DOC_A}.json.gz").write_bytes(b"x")
        amounts = [{"label": "事業費A", "fiscal_year": 2024, "raw": "100", "stage": "settlement", "scope": "direct", "unit": "千円"},
                   {"label": "人件費B", "fiscal_year": 2024, "raw": "50", "stage": "settlement", "scope": "personnel", "unit": "千円"},
                   {"label": "総コスト(A+B)", "fiscal_year": 2024, "raw": "150", "stage": "settlement", "scope": "total", "unit": "千円"},
                   {"label": "事業費A", "fiscal_year": 2025, "raw": "120", "stage": "budget", "scope": "direct", "unit": "千円"}]
        form = {"doc_id": DOC_A, "pages": [3, 4], "evaluation_year": 2025, "target_year": 0,
                "project": {"name": [{"raw": "人権関連事業", "ref": "p3t1r2c9"}], "department": [{"raw": "市民文化局\n人権課", "ref": "p3t1r4c9"}],
                            "legal_basis": [{"raw": "人権教育法", "ref": "p3t1r9c5"}], "code": []},
                "amounts": amounts, "funding": [], "staff": [],
                "indicators": [{"name": "受講者数", "kind": "activity", "kind_raw": "活動指標", "unit": "人",
                                "values": [{"fiscal_year": 2024, "target_raw": "560", "actual_raw": "612", "rate_raw": ""}]}],
                "evaluations": [{"stage_raw": "今後の方向性", "status": "done", "rating_raw": "3", "direction_raw": "継続", "aspects": []}]}
        (root / "forms" / slug).mkdir(parents=True)
        (root / "forms" / slug / "records.jsonl").write_text(json.dumps(form, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"slugs": [slug], "root": root / "hyoka2", "records_root": root / "forms", "layouts_root": root / "hyoka2-ai"}

    def test_overlay_publishes_form_records_for_the_chosen_municipality(self) -> None:
        status = b.build(self.work, self.records, self.out, [self.log], overlay=self.overlay())
        rows = {row["slug"]: row for row in status["municipalities"]}
        self.assertEqual(sorted(rows), ["14130-kawasaki-shi", SLUG])
        self.assertEqual((rows["14130-kawasaki-shi"]["records"], rows["14130-kawasaki-shi"]["records_source"], rows["14130-kawasaki-shi"]["sheets"]), (1, "form", 1))
        self.assertEqual(rows[SLUG]["records_source"], "machine")
        saved = json.loads((self.out / "records" / "14130-kawasaki-shi" / f"{DOC_A}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], "2.0-form")
        shown = saved["records"][0]
        self.assertEqual((shown["id"], shown["layout"], shown["page"]), (f"{DOC_A}-1", "form_reader", 3))
        self.assertEqual([(f["key"], f["text"]) for f in shown["fields"]],
                         [("project_name", "人権関連事業"), ("department", "市民文化局人権課"), ("evaluation_year", "2025年度"),
                          ("evaluation_rating", "3"), ("future_direction", "継続")])
        self.assertEqual(shown["raw_fields"], [{"label": "根拠法令", "text": "人権教育法"}])
        table = shown["amounts"]
        self.assertEqual(table["unit"], "千円")
        self.assertEqual(table["columns"], [{"year": 2024, "stage": "決算"}, {"year": 2025, "stage": "予算"}])
        # 金額は原文の値と、円にそろえた値（千円 × 1,000）を並べて持つ。
        self.assertEqual(table["rows"][0], {"label": "事業費A", "kind": "事業費（人件費を除く）", "unit": "", "values": ["100", "120"],
                                            "yen": [100_000, 120_000]})
        self.assertEqual(shown["indicators"][0]["values"], [{"year": 2024, "target": "560", "actual": "612", "rate": ""}])
        self.assertEqual(shown["checks"], {"pairs": 1, "problems": []})
        self.assertEqual(b.direction_text({"direction_raw": "Ⅰ", "direction": "continue"}), "Ⅰ（継続）")
        self.assertEqual(b.direction_text({"direction_raw": "改善しながら継続", "direction": "improve"}), "改善しながら継続")
        self.assertTrue((self.out / "sheets" / "14130-kawasaki-shi" / f"{DOC_A}.json.gz").is_file())

    def test_form_2_fields_are_carried_to_the_display_record(self) -> None:
        value = lambda raw, ref="p3t1r0c0": {"raw": raw, "ref": ref}
        form = {"doc_id": DOC_A, "pages": [3], "evaluation_year": 2025,
                "project": {"name": [value("同和対策事業（川崎区）")]}, "amounts": [], "funding": [], "staff": [], "indicators": [],
                "level_raw": "事務事業", "parent": None, "is_main": {"value": True, **value("○")},
                "variant": {"base_name": "同和対策事業", "label": "川崎区", "kind": "区"},
                "policy_path": [{"level_raw": "基本政策", "code": "5", "name": "市民自治の\n地域づくり", "source": value("x")},
                                {"level_raw": "施策", "code": "5-2-1", "name": "人権尊重", "source": value("y"), "goal": value("差別の\n無い社会")}],
                "texts": [{"key": "description", "label_raw": "事業の内容", "text": value("隣保館を\n運営する")},
                          {"key": "plan", "label_raw": "取組内容", "text": value("")}],
                "extra_fields": [{"section_raw": "事業の概要", "label": value("事業開始年度"), "values": [value("1970"), value("")]},
                                 {"section_raw": "事業の概要", "label": value("課題名"), "values": [value("")]}],
                "evaluations": [{"stage_raw": "所管局", "status": "done", "rating_raw": "3", "direction_raw": "継続",
                                 "achievement": {"raw": "3", "ref": "p3t1r1c1", "rank": 3, "scale": 5, "option": "ほぼ目標どおり", "level": "on_target"},
                                 "overall": {"raw": "B", "rank": 2, "scale": 3, "option": "やや貢献", "level": "", "aspect_raw": "施策への貢献度"},
                                 "aspects": [{"aspect_raw": "成果", "aspect": "有効性", "rating_raw": "c", "option": "上がっていない", "level": "low"}],
                                 "reasons": [{"aspect": "有効性", "label_raw": "評価の理由", "text": value("参加者が減った")}]}]}
        shown = b.compact_form_record(form, DOC_A, 1)
        self.assertEqual(shown["policy_path"], [{"level": "基本政策", "code": "5", "name": "市民自治の地域づくり"},
                                                {"level": "施策", "code": "5-2-1", "name": "人権尊重", "goal": "差別の無い社会"}])
        self.assertEqual((shown["level"], shown["is_main"], shown["variant"]["label"]), ("事務事業", True, "川崎区"))
        self.assertNotIn("parent", shown)
        # 空の文は落とす。折り返しの改行は詰める
        self.assertEqual(shown["texts"], [{"key": "description", "name": "事業の内容・手段", "label": "事業の内容", "text": "隣保館を運営する"}])
        self.assertEqual(shown["extra_fields"], [{"section": "事業の概要", "label": "事業開始年度", "text": "1970"}])
        evaluation = shown["evaluations"][0]
        self.assertEqual((evaluation["achievement"]["level"], evaluation["achievement"]["name"], evaluation["achievement"]["option"]),
                         ("on_target", "ほぼ目標どおり", "ほぼ目標どおり"))
        self.assertNotIn("overall", evaluation)  # 段階の決まらない評価は持たない
        self.assertEqual(evaluation["aspect_levels"][0]["level"], "low")
        self.assertEqual(evaluation["reasons"], [{"aspect": "有効性", "label": "評価の理由", "text": "参加者が減った"}])

    def test_policy_paths_are_completed_within_the_municipality(self) -> None:
        def step(level, code, name):
            return {"level_raw": level, "code": code, "name": name, "source": {"raw": name, "ref": "p1t1r0c0"}}
        top = step("基本政策", "1", "生命を守り生き生きと暮らすことができるまちづくり")
        policy = step("政策", "1-4", "誰もが安心して暮らせる地域のつながり・しくみをつくる")
        rows = [
            # 体系図のある分冊の前の方
            {"doc_id": "a" * 16, "evaluation_year": 2024, "policy_path": [top, policy, step("施策", "1-4-1", "地域包括ケア")]},
            # 体系図の無い後ろの方（施策だけ）
            {"doc_id": "b" * 16, "evaluation_year": 2024, "policy_path": [step("施策", "1-4-4", "障害福祉サービスの充実")]},
            # 別の年度には補わない
            {"doc_id": "c" * 16, "evaluation_year": 2023, "policy_path": [step("施策", "1-4-4", "障害福祉サービスの充実")]},
            # 仮名の抜けた体系図の名前は、同じ番号の字の多い名前にそろえる
            {"doc_id": "d" * 16, "evaluation_year": 2021, "policy_path": [step("基本政策", "1", "生命守生生暮"), step("政策", "1-4", "誰安心暮地域")]},
        ]
        path = self.out.parent / "policy.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        names, ancestors = b.policy_index(path)
        b.repair_policy_path(rows[1], names, ancestors)
        self.assertEqual([(s["code"], s.get("from_doc")) for s in rows[1]["policy_path"]], [("1", "a" * 16), ("1-4", "a" * 16), ("1-4-4", None)])
        b.repair_policy_path(rows[2], names, ancestors)
        self.assertEqual(len(rows[2]["policy_path"]), 1)
        # 番号の無い段は、同じ年度・同じ呼び名で骨組みの同じ名前の段から番号を取り、そこから上を補う
        bare = {"doc_id": "e" * 16, "evaluation_year": 2024,
                "policy_path": [step("政策", "", "誰もが安心して暮らせる地域のつながり・しくみをつくる"), step("施策", "", "障害福祉サービスの充実")]}
        b.repair_policy_path(bare, names, ancestors)
        self.assertEqual([(s["code"], s.get("from_doc"), s.get("code_from_doc")) for s in bare["policy_path"]],
                         [("1", "a" * 16, None), ("1-4", None, "a" * 16), ("1-4-4", None, "b" * 16)])
        # 同じ文書の中の道筋から補った段には、ほかの文書の印を付けない
        same = {"doc_id": "a" * 16, "evaluation_year": 2024, "policy_path": [step("施策", "1-4-2", "高齢者福祉")]}
        b.repair_policy_path(same, names, ancestors)
        self.assertEqual([(s["code"], s.get("from_doc")) for s in same["policy_path"]], [("1", None), ("1-4", None), ("1-4-2", None)])
        b.repair_policy_path(rows[3], names, ancestors)
        self.assertEqual([s["name"] for s in rows[3]["policy_path"]], [top["name"], policy["name"]])
        self.assertEqual(rows[3]["policy_path"][0]["source"]["raw"], "生命守生生暮")  # 原文は残す
        shown = b.compact_form_record({**rows[1], "project": {"name": [{"raw": "x", "ref": "p1t1r0c0"}]}}, "b" * 16, 1)
        self.assertEqual(shown["policy_path"][0]["from_doc"], "a" * 16)
        self.assertNotIn("from_doc", shown["policy_path"][2])

    def test_ai_records_are_used_only_where_machine_records_are_missing(self) -> None:
        other = "88888-other-shi"
        (self.work / other).mkdir(parents=True)
        (self.work / other / "manifest.json").write_text(json.dumps({
            "slug": other, "jis_code": "88888", "name": "例市", "documents": {DOC_B: {"text_status": "ok", "file_type": "pdf",
                                                                                    "source_url": "https://example.jp/b.pdf"}},
        }, ensure_ascii=False), encoding="utf-8")
        ai_root = self.out.parent / "ai"
        for slug in (SLUG, other):
            ai_record = record(f"ai-{DOC_B}-p0002-01", DOC_B, 2, f"AI の事業 {slug[:5]}")
            ai_record["layout"] = "ai_transcription"
            ai_record["extraction"] = {"method": "ai_transcription", "model": "claude-haiku-5-5"}
            (ai_root / slug).mkdir(parents=True)
            (ai_root / slug / "records.jsonl").write_text(json.dumps(ai_record, ensure_ascii=False) + "\n", encoding="utf-8")
        status = b.build(self.work, self.records, self.out, [], ai_root)
        rows = {row["slug"]: row for row in status["municipalities"]}
        self.assertEqual((rows[SLUG]["records_source"], rows[SLUG]["records"]), ("machine", 3))  # 機械の記録を優先
        self.assertEqual((rows[other]["records_source"], rows[other]["records"]), ("ai", 1))
        self.assertEqual((status["totals"]["records_ai"], status["totals"]["municipalities_with_ai_records"]), (1, 1))
        saved = json.loads((self.out / "records" / other / f"{DOC_B}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["records"][0]["layout"], "ai_transcription")
        self.assertEqual(saved["records"][0]["model"], "claude-haiku-5-5")
        self.assertEqual(saved["source_url"], "https://example.jp/b.pdf")
        self.assertFalse((self.out / "records" / SLUG / f"{DOC_B}.json").exists())

    def test_empty_machine_records_fall_back_to_ai(self) -> None:
        # 機械の抽出で 1 件も取れなかった自治体には、空の records.jsonl が残っている
        (self.records / SLUG / "records.jsonl").write_text("", encoding="utf-8")
        ai_root = self.out.parent / "ai"
        ai_record = record(f"ai-{DOC_B}-p0002-01", DOC_B, 2, "AI の事業")
        ai_record["layout"] = "ai_transcription"
        (ai_root / SLUG).mkdir(parents=True)
        (ai_root / SLUG / "records.jsonl").write_text(json.dumps(ai_record, ensure_ascii=False) + "\n", encoding="utf-8")
        status = b.build(self.work, self.records, self.out, [], ai_root)
        rows = {row["slug"]: row for row in status["municipalities"]}
        self.assertEqual((rows[SLUG]["records_source"], rows[SLUG]["records"]), ("ai", 1))
        self.assertEqual(status["totals"]["records_ai"], 1)

    def test_sheets_are_copied_and_stale_ones_removed(self) -> None:
        ai_root = self.out.parent / "ai"
        (ai_root / SLUG / "layouts").mkdir(parents=True)
        (ai_root / SLUG / "layouts" / f"{DOC_B}.json.gz").write_bytes(b"sheet")
        stale = self.out / "sheets" / SLUG / f"{'c' * 16}.json.gz"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        other = self.out / "sheets" / "88888-other-shi" / f"{'d' * 16}.json.gz"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"keep")
        status = b.build(self.work, self.records, self.out, [], ai_root)
        self.assertEqual((self.out / "sheets" / SLUG / f"{DOC_B}.json.gz").read_bytes(), b"sheet")
        self.assertFalse(stale.exists())
        self.assertTrue(other.exists())  # AI の置き場に無い自治体の控えは消さない
        self.assertEqual(status["totals"]["sheets"], 1)

    def test_sheets_stay_when_the_ai_folder_is_not_mounted(self) -> None:
        kept = self.out / "sheets" / SLUG / f"{DOC_B}.json.gz"
        kept.parent.mkdir(parents=True)
        kept.write_bytes(b"sheet")
        b.build(self.work, self.records, self.out, [], None)
        self.assertTrue(kept.exists())

    def test_empty_machine_records_stay_empty_without_ai(self) -> None:
        (self.records / SLUG / "records.jsonl").write_text("", encoding="utf-8")
        status = b.build(self.work, self.records, self.out, [], self.out.parent / "ai")
        rows = {row["slug"]: row for row in status["municipalities"]}
        self.assertEqual(rows[SLUG]["records"], 0)

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
