"""事務事業評価の AI による書き写し（tools/hyoka/ai）を確かめる。API は呼ばない。"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "ai"))

import openai_api  # noqa: E402
import records as rec  # noqa: E402
import run_ai  # noqa: E402

try:
    import fitz  # noqa: F401

    import grid
    HAVE_FITZ = True
except ImportError:  # PyMuPDF は専用の容器（docker/hyoka-ai）にだけある
    HAVE_FITZ = False

DOC = "c" * 16


def chunk(index, pages, text, records, kind="project_evaluation"):
    return {"index": index, "pages": pages, "text": text, "parsed": {"document_kind": kind, "records": records}}


def item(page, name, labels=None, **values):
    out = {"page": page, "project_name": name, "department": "", "future_direction": "", "evaluation_rating": "",
           "purpose": "", "year_text": "", "labels": {k: "" for k in rec.FIELDS}}
    out.update(values)
    out["labels"].update(labels or {})
    return out


META = {"slug": "99999-example-shi", "code": "99999", "name": "例市", "doc_id": DOC,
        "source_url": "https://example.jp/a.pdf", "page_url": "https://example.jp/"}


class AssembleTest(unittest.TestCase):
    def test_values_not_in_source_are_not_shown(self) -> None:
        text = "=== 頁 1 ===\n## table 1\n0,0: 事務事業名\n0,1: 広報広聴事業\n1,0: 担当課\n1,1: 秘書課\n2,0: 今後の方向性\n2,1: ●継続"
        got, summary = rec.assemble_document(META, [chunk(0, [1], text, [
            item(1, "広報広聴事業", {"future_direction": "今後の方向性"}, department="秘書課", future_direction="継続",
                 purpose="市政を伝える")])], {"model": "m"})
        fields = got[0]["fields"]
        self.assertEqual(fields["department"]["raw_text"], "秘書課")
        self.assertEqual(fields["future_direction"]["value"], {"raw_term": "継続", "mapping_status": "unmapped"})
        self.assertNotIn("purpose", fields)  # 原典に無い（作った）値は出さない
        self.assertEqual(got[0]["unverified"], {"purpose": "市政を伝える"})
        self.assertEqual(summary["unverified_values"], 1)
        self.assertEqual(got[0]["layout"], "ai_transcription")
        self.assertEqual(got[0]["record_evidence"][0]["locator"], {"type": "pdf", "page": 1})
        self.assertEqual(got[0]["record_id"], f"ai-{DOC}-p0001-01")
        self.assertFalse(got[0]["extraction"]["human_reviewed"])

    def test_split_cells_and_wrapped_values_are_verified(self) -> None:
        text = "=== 頁 2 ===\n0,0: 市民生活部\n0,1: 環境政策課\n[05,10] 作業効率\n[06,10] 化を図る"
        got, _ = rec.assemble_document(META, [chunk(0, [2], text, [
            item(2, "作業効率化を図る", department="市民生活部 環境政策課")])], {})
        self.assertEqual(got[0]["fields"]["department"]["raw_text"], "市民生活部 環境政策課")
        self.assertEqual(got[0]["fields"]["project_name"]["raw_text"], "作業効率化を図る")

    def test_records_without_name_or_with_unverified_name_are_dropped(self) -> None:
        text = "=== 頁 1 ===\n0,0: 事業名\n0,1: 道路維持事業"
        got, summary = rec.assemble_document(META, [chunk(0, [1], text, [item(1, ""), item(1, "架空の事業")])], {})
        self.assertEqual(got, [])
        self.assertEqual(summary["dropped_no_name"], 2)

    def test_same_project_across_chunks_is_merged(self) -> None:
        first = chunk(0, [5, 6], "=== 頁 6 ===\n0,1: 公園整備事業\n0,2: 公園緑地課", [item(6, "公園整備事業", department="公園緑地課")])
        second = chunk(1, [7, 8], "=== 頁 7 ===\n0,1: 公園整備事業\n1,1: B 継続",
                       [item(7, "公園整備事業", {"future_direction": "今後の事業の方向性"}, future_direction="B 継続")])
        got, _ = rec.assemble_document(META, [first, second], {})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["record_evidence"][0]["locator"]["page"], 6)
        self.assertEqual(got[0]["fields"]["future_direction"]["raw_text"], "B 継続")
        self.assertEqual(got[0]["fields"]["department"]["raw_text"], "公園緑地課")

    def test_direction_from_rating_field_is_not_a_dedicated_direction(self) -> None:
        text = "=== 頁 1 ===\n0,0: 事務事業名\n0,1: 交流事業\n1,0: 評価結果\n1,1: A:現状のまま継続"
        got, _ = rec.assemble_document(META, [chunk(0, [1], text, [
            item(1, "交流事業", {"future_direction": "評価結果", "evaluation_rating": "評価結果"},
                 future_direction="A:現状のまま継続", evaluation_rating="A:現状のまま継続")])], {})
        fields = got[0]["fields"]
        self.assertEqual(fields["future_direction"]["status"], "represented_elsewhere")
        self.assertEqual(fields["evaluation_rating"]["raw_text"], "A:現状のまま継続")
        self.assertEqual(got[0]["raw_fields"][0]["standard_key"], "future_direction")

    def test_year_text_goes_to_raw_fields_and_errors_are_counted(self) -> None:
        text = "=== 頁 1 ===\n[03,20] 令和6年度 事務事業評価シート\n0,1: 戸籍事務"
        got, summary = rec.assemble_document(META, [chunk(0, [1], text, [item(1, "戸籍事務", year_text="令和6年度")]),
                                                    {"index": 1, "pages": [2], "text": "", "error": "timeout"}], {})
        self.assertEqual(got[0]["raw_fields"][0]["label"], "年度の記載")
        self.assertEqual(got[0]["raw_fields"][0]["content"]["raw_text"], "令和6年度")
        self.assertEqual(summary["errors"], 1)

    def test_each_record_keeps_the_model_that_wrote_it(self) -> None:
        text = "=== 頁 1 ===\n0,0: 事務事業名\n0,1: 広報事業\n0,2: 防災事業\n0,3: 継続"
        luna = chunk(0, [1], text, [item(1, "広報事業")])
        haiku = dict(chunk(1, [1], text, [item(1, "防災事業"), item(1, "広報事業", future_direction="継続")]), model="claude-haiku-5-5")
        got, _summary = rec.assemble_document(META, [luna, haiku], {"model": "gpt-6-luna"})
        models = {r["fields"]["project_name"]["value"]: r["extraction"]["model"] for r in got}
        self.assertEqual(models, {"広報事業": "claude-haiku-5-5 + gpt-6-luna", "防災事業": "claude-haiku-5-5"})

    def test_direction_label_rule(self) -> None:
        self.assertTrue(rec.direction_is_dedicated("今後の方向性"))
        self.assertTrue(rec.direction_is_dedicated("事業の課題に対する改善事項"))
        self.assertTrue(rec.direction_is_dedicated(""))
        self.assertFalse(rec.direction_is_dedicated("総合評価"))
        self.assertFalse(rec.direction_is_dedicated("２次評価 評価結果"))


class PlanRoleTest(unittest.TestCase):
    def test_survey_documents_are_chosen_by_role(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "work"
            folder = root / "14130-kawasaki-shi"
            (folder / "source").mkdir(parents=True)
            documents = {}
            # 調査から集めた文書は役割で、以前の収集の文書は kind で選ぶ
            for doc_id, origin, kind in (("a" * 16, "listed:sheet", "overview"), ("b" * 16, "followed:other", "project_evaluation"),
                                         ("c" * 16, "", "project_evaluation"), ("d" * 16, "", "overview")):
                (folder / "source" / f"{doc_id}.pdf").write_bytes(b"%PDF-1.4")
                documents[doc_id] = {"file": f"source/{doc_id}.pdf", "file_type": "pdf", "text_status": "ok", "origin": origin, "kind": kind}
            (folder / "manifest.json").write_text(json.dumps({"jis_code": "14130", "name": "川崎市", "documents": documents}), encoding="utf-8")
            out = Path(temporary) / "out"
            run_ai.cmd_plan(argparse.Namespace(work_root=root, extracted_root=Path(temporary) / "none", out=out, slug=None,
                                               limit_docs=0, include_extracted=False, roles="sheet,report,external"))
            plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
        chosen = sorted(doc["doc_id"] for doc in plan["municipalities"]["14130-kawasaki-shi"]["docs"])
        self.assertEqual(chosen, ["a" * 16, "c" * 16])


class RunTest(unittest.TestCase):
    def test_lone_surrogate_is_written_as_replacement(self) -> None:
        # PDF の文字に対の無いサロゲートが混ざっても書ける（2026-11-04 の g5 は、これで全体が止まった）
        broken = "事業" + chr(0xDBC0) + "名"
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / "s" / "inputs" / "d.json.gz"
            run_ai.write_doc_grid(str(dest), "d", [[1, broken]], [{"page": 1, "lines": [{"text": broken}]}], "")
            inputs = json.loads(gzip.open(dest, "rt", encoding="utf-8").read())
            layouts = run_ai.read_gzip_lines(run_ai.layouts_path(dest))
        self.assertEqual(inputs["pages"][0][1], "事業" + chr(0xFFFD) + "名")
        self.assertEqual(layouts[1]["lines"][0]["text"], "事業" + chr(0xFFFD) + "名")

    def test_unexpected_failure_does_not_stop_grids(self) -> None:
        with mock.patch.object(run_ai, "build_doc_grid_unchecked", side_effect=OSError("disk")):
            doc_id, dest, pages, error = run_ai.build_doc_grid(("x.pdf", "out/d.json.gz", "d"))
        self.assertEqual((doc_id, dest, pages), ("d", "out/d.json.gz", 0))
        self.assertIn("OSError", error)

    def test_chunks_split_by_pages_and_size(self) -> None:
        pages = [[n, "あ" * 100] for n in range(1, 14)]
        chunks = run_ai.chunk_pages(pages)
        self.assertEqual([c["pages"] for c in chunks], [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12], [13]])
        self.assertIn("=== 頁 7 ===", chunks[1]["text"])
        big = run_ai.chunk_pages([[1, "い" * 30000], [2, "う" * 30000]])
        self.assertEqual([c["pages"] for c in big], [[1], [2]])

    def test_batch_lines_are_parsed(self) -> None:
        ok = {"custom_id": "s/d/0", "response": {"status_code": 200, "body": {
            "usage": {"prompt_tokens": 1000000, "completion_tokens": 0},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"document_kind": "other", "records": []})}}]}}}
        custom_id, result = run_ai.parse_batch_line(json.dumps(ok))
        self.assertEqual(custom_id, "s/d/0")
        self.assertEqual(result["parsed"]["document_kind"], "other")
        self.assertAlmostEqual(result["usd"], 0.05)  # 一括処理は半額
        bad = {"custom_id": "s/d/1", "response": {"status_code": 400, "body": {"error": {"message": "x"}}}}
        self.assertIn("error", run_ai.parse_batch_line(json.dumps(bad))[1])
        broken = {"custom_id": "s/d/2", "response": {"status_code": 200, "body": {"choices": [{"message": {"content": "{"}}]}}}
        self.assertIn("error", run_ai.parse_batch_line(json.dumps(broken))[1])

    def test_sync_goes_to_claude_with_the_fallback_model(self) -> None:
        answer = json.dumps({"document_kind": "other", "records": []})
        usage = {"prompt_tokens": 1000000, "completion_tokens": 0}
        with mock.patch.object(run_ai.anthropic_api, "chat", return_value=(answer, usage, "end_turn")) as claude, \
                mock.patch.object(run_ai.openai_api, "chat") as openai:
            got = run_ai.extract_sync("key", "指示", {"index": 0, "pages": [1], "text": "升目"}, run_ai.FALLBACK_MODEL)
        openai.assert_not_called()
        self.assertEqual(claude.call_args.args[:4], ("key", "claude-haiku-5-5", "指示", "升目"))
        self.assertEqual((got["model"], got["parsed"]["document_kind"]), ("claude-haiku-5-5", "other"))
        self.assertAlmostEqual(got["usd"], 0.10)  # 一括処理の半額はきかない

    def test_failed_batch_keeps_what_it_finished(self) -> None:
        line = json.dumps({"custom_id": "s/d/0", "response": {"status_code": 200, "body": {
            "usage": {}, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"document_kind": "other", "records": []})}}]}}})

        def download(_key, _file_id, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(line, encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run_ai.write_json(out / "batches.json", {"batches": [{"part": "p", "batch_id": "b1", "status": "in_progress"}]})
            config = out / "config.json"
            config.write_text(json.dumps({"openai": {"apiKey": "k"}}), encoding="utf-8")
            info = {"status": "failed", "output_file_id": "f1", "request_counts": {"completed": 1, "failed": 1}}
            with mock.patch.object(run_ai.openai_api, "get_batch", return_value=info), \
                    mock.patch.object(run_ai.openai_api, "download_file", side_effect=download):
                run_ai.cmd_collect(argparse.Namespace(out=out, config=config))
            saved = json.loads((out / "batches.json").read_text(encoding="utf-8"))["batches"][0]
            self.assertEqual((saved["status"], saved["final_status"]), ("collected", "failed"))
            self.assertIn("parsed", run_ai.read_json(out / "s" / "results" / "d.json")["chunks"]["0"])

    def test_no_credit_is_not_retried(self) -> None:
        body = json.dumps({"error": {"code": "insufficient_quota"}}).encode("utf-8")
        error = urllib.error.HTTPError("https://example.jp", 429, "Too Many Requests", {}, io.BytesIO(body))
        with mock.patch("urllib.request.urlopen", side_effect=error) as urlopen, mock.patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "insufficient_quota"):
                openai_api.get_batch("key", "b1")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_legend_is_used_only_when_nothing_gets_worse(self) -> None:
        base = {"records": 20, "fill": 0.9, "verify": 1.0, "kinds": 4, "options": 0.3}
        self.assertTrue(run_ai.accept_legend(base, dict(base, fill=1.0, kinds=3, options=1.0))[0])
        self.assertFalse(run_ai.accept_legend(base, dict(base, fill=0.7))[0])
        self.assertFalse(run_ai.accept_legend(base, dict(base, verify=0.5))[0])
        self.assertFalse(run_ai.accept_legend(base, dict(base, records=10))[0])
        self.assertFalse(run_ai.accept_legend(base, dict(base, kinds=6, options=0.3))[0])
        self.assertFalse(run_ai.accept_legend(dict(base, records=0), dict(base, records=0))[0])

    def test_plan_picks_text_pdfs_of_project_evaluations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            extracted = root / "extracted"
            for slug in ("11111-a-shi", "22222-b-shi"):
                (work / slug / "source").mkdir(parents=True)
            docs = {
                "d" * 16: {"file_type": "pdf", "text_status": "ok", "kind": "project_evaluation", "pages": 3, "fiscal_year": 2024},
                "e" * 16: {"file_type": "pdf", "text_status": "ok", "kind": "policy_evaluation", "pages": 3},
                "f" * 16: {"file_type": "pdf", "text_status": "ok", "kind": "project_evaluation", "text_source": "ocr:ndlocr-lite"},
                "0" * 16: {"file_type": "pdf", "text_status": "ok", "kind": "project_evaluation", "status": "gone"},
                "1" * 16: {"file_type": "pdf", "text_status": "ok", "kind": "project_evaluation"},  # ファイルが無い
            }
            for slug in ("11111-a-shi", "22222-b-shi"):
                (work / slug / "manifest.json").write_text(json.dumps({"jis_code": slug[:5], "name": "市", "documents": docs}), encoding="utf-8")
                for doc_id in ("d" * 16, "e" * 16, "f" * 16, "0" * 16):
                    (work / slug / "source" / f"{doc_id}.pdf").write_bytes(b"%PDF")
            (extracted / "22222-b-shi").mkdir(parents=True)
            (extracted / "22222-b-shi" / "records.jsonl").write_text("{}\n", encoding="utf-8")
            out = root / "out"
            run_ai.main(["plan", "--work-root", str(work), "--extracted-root", str(extracted), "--out", str(out)])
            plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(list(plan["municipalities"]), ["11111-a-shi"])
            self.assertEqual([d["doc_id"] for d in plan["municipalities"]["11111-a-shi"]["docs"]], ["d" * 16])
            self.assertEqual(plan["totals"]["excluded_extracted"], 1)

    def test_assemble_writes_records_from_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            slug = "11111-a-shi"
            run_ai.write_json(out / "plan.json", {"municipalities": {slug: {"code": "11111", "name": "市", "docs": [
                {"doc_id": DOC, "file": "x.pdf", "source_url": "u", "page_url": "p"}]}}})
            path = run_ai.inputs_path(out, slug, DOC)
            path.parent.mkdir(parents=True)
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump({"doc_id": DOC, "pages": [[1, "0,0: 事業名\n0,1: 戸籍事務"]]}, stream, ensure_ascii=False)
            run_ai.write_json(out / slug / "chunks" / f"{DOC}.json", {"chunks": [{"index": 0, "pages": [1]}], "prompt_version": "p", "legend_version": None})
            run_ai.write_json(run_ai.results_path(out, slug, DOC), {"chunks": {"0": {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "usd": 0.001,
                "parsed": {"document_kind": "project_evaluation", "records": [item(1, "戸籍事務")]}}}})
            run_ai.main(["assemble", "--out", str(out), "--work-root", str(Path(tmp) / "work")])
            lines = (out / slug / "records.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0])["fields"]["project_name"]["raw_text"], "戸籍事務")
            totals = json.loads((out / "summary.json").read_text(encoding="utf-8"))["totals"]
            self.assertEqual((totals["records"], totals["tokens_in"]), (1, 10))


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF が無い")
class GridTest(unittest.TestCase):
    def make_page(self):
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        # 3 行 3 列の表: 1 行目「事務事業名｜広報広聴事業」、2 行目「●｜：見直して継続」（印が別の升目）
        xs, ys = [20, 120, 140, 380], [20, 50, 80, 140]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        page.insert_text((25, 40), "事務事業名", fontname="japan", fontsize=10)
        page.insert_text((145, 40), "広報広聴事業", fontname="japan", fontsize=10)
        page.insert_text((124, 70), "●", fontname="japan", fontsize=10)
        page.insert_text((145, 70), "：見直して継続", fontname="japan", fontsize=10)
        # 升目の幅いっぱいで折り返す文
        page.insert_textbox(fitz.Rect(142, 82, 378, 138), "作業効率化を図ると共に、適正なごみ収集運搬作業を実施する。",
                            fontname="japan", fontsize=10)
        page.insert_text((25, 200), "令和6年度 事務事業評価シート", fontname="japan", fontsize=10)
        return doc, page

    def test_cells_marks_wraps_and_outside_text(self) -> None:
        doc, page = self.make_page()
        text = grid.build_cells(page)
        doc.close()
        self.assertIn("広報広聴事業", text)
        self.assertIn("●：見直して継続", text)  # 印を右の選択肢へ付け直す
        self.assertIn("作業効率化を図ると共に、適正なごみ収集運搬作業を実施する。", text)  # 折り返しは空白なしでつなぐ
        self.assertRegex(text, r"\[\d\d,\d\d\] 令和6年度 ?事務事業評価シート")

    def make_mark_table(self, rows):
        """見出しの行の下に、列で印を付ける表。"""
        doc = fitz.open()
        page = doc.new_page(width=400, height=200)
        xs, ys = [20, 120, 180, 240, 300], [20 + 30 * i for i in range(len(rows) + 1)]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                if text:
                    page.insert_text((xs[c] + 4, ys[r] + 20), text, fontname="japan", fontsize=10)
        return doc, page

    def test_mark_under_a_heading_stays_in_its_column(self) -> None:
        # 宇土市の形。◎ は「現状維持」の列にあり、右隣の「－」の升目へ動かしてはいけない
        doc, page = self.make_mark_table([
            ["事業名", "拡充", "現状維持", "規模縮小"],
            ["広報事業", "－", "◎", "－"],
            ["防災事業", "◎", "－", "－"],
        ])
        text = grid.build_cells(page)
        doc.close()
        self.assertIn("1,2: ◎", text)
        self.assertIn("2,1: ◎", text)
        self.assertNotIn("◎－", text)

    def test_mark_next_to_a_filler_is_not_moved(self) -> None:
        doc, page = self.make_mark_table([["広報事業", "○", "－", "－"]])
        text = grid.build_cells(page)
        doc.close()
        self.assertIn("0,1: ○", text)
        self.assertIn("0,2: －", text)

    def test_rotated_page_reads_the_way_it_looks(self) -> None:
        # 三島市の形。頁に 90 度の回転の指定があると、文字の座標が回転の前のままで字が逆に並ぶ
        source, _ = self.make_page()
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.show_pdf_page(page.rect, source, 0, rotate=90)
        page.set_rotation(90)
        text = grid.build_cells(page)
        doc.close()
        source.close()
        self.assertIn("広報広聴事業", text)
        self.assertIn("●：見直して継続", text)

    def test_grid_step_writes_the_list_and_the_layout(self) -> None:
        doc, page = self.make_page()
        doc.new_page(width=400, height=300)  # 文字の無い頁
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "a.pdf"
            doc.save(pdf)
            dest = Path(tmp) / "out" / "11111-a-shi" / "inputs" / "abc.json.gz"
            result = run_ai.build_doc_grid((str(pdf), str(dest), "abc"))
            with gzip.open(dest, "rt", encoding="utf-8") as stream:
                inputs = json.load(stream)
            head, first, second = run_ai.read_gzip_lines(run_ai.layouts_path(dest))
        doc.close()
        self.assertEqual(result[2:], (1, ""))
        self.assertEqual([p for p, _ in inputs["pages"]], [1])
        self.assertEqual(head["pages"], [1, 2])
        self.assertEqual(inputs["pages"][0][1], grid.layout_text(first))
        self.assertTrue(second["image_only"])  # 画像だけの頁も、あったことを残す

    def test_stacked_numbers_in_a_cell_are_not_joined(self) -> None:
        # 那珂市の形。点線の行が 1 つの升目に重なり、右端にそろった数字がつながると 48.92.0.0. になる
        doc = fitz.open()
        page = doc.new_page(width=300, height=200)
        for x in (20, 60):
            page.draw_line((x, 20), (x, 100))
        for y in (20, 100):
            page.draw_line((20, y), (60, y))
        for y, text in ((40, "48."), (60, "92."), (80, "0.")):
            page.insert_text((58 - fitz.get_text_length(text, fontsize=10), y), text, fontsize=10)
        glyphs = grid.text_glyphs(page)
        text = grid.cell_text(glyphs, 60)
        doc.close()
        self.assertEqual(text, "48. 92. 0.")

    def test_close_lines_do_not_interleave(self) -> None:
        # 久山町の形。上下の行が近いと、字が「実事施業方…」と混ざる
        doc = fitz.open()
        page = doc.new_page(width=300, height=200)
        page.insert_text((20, 40), "実施方法の工夫", fontname="japan", fontsize=10)
        page.insert_text((20, 44.5), "事業の効率化", fontname="japan", fontsize=10)
        text = grid.cell_text(grid.text_glyphs(page), 280)
        doc.close()
        self.assertIn("実施方法の工夫", text)
        self.assertIn("事業の効率化", text)

    def test_page_without_tables_keeps_the_text_positions(self) -> None:
        doc = fitz.open()
        page = doc.new_page(width=400, height=200)
        page.insert_text((40, 50), "事業名", fontname="japan", fontsize=10)
        page.insert_text((200, 50), "広報事業", fontname="japan", fontsize=10)
        layout = grid.page_layout(page)
        doc.close()
        items = {item["x"]: item for item in layout["canvas"]}
        self.assertAlmostEqual(items["事業名"]["l"], 10, delta=0.5)
        self.assertAlmostEqual(items["広報事業"]["l"], 50, delta=0.5)  # 離れた字は別の塊のまま位置を保つ

    def test_page_with_a_good_table_has_no_canvas(self) -> None:
        doc, page = self.make_mark_table([["事業名", "拡充", "現状維持", "規模縮小"], ["広報事業", "－", "◎", "－"]])
        layout = grid.page_layout(page)
        doc.close()
        self.assertNotIn("canvas", layout)

    def test_overprinted_text_is_read_once(self) -> None:
        # 足立区の形。太く見せるために同じ字を少しずらして重ねた PDF は、字が「事事務務」と二重に取れる
        doc = fitz.open()
        page = doc.new_page(width=300, height=100)
        page.insert_text((20, 40), "事務事業名", fontname="japan", fontsize=12)
        page.insert_text((20.3, 40), "事務事業名", fontname="japan", fontsize=12)
        page.insert_text((20, 70), "ああ", fontname="japan", fontsize=12)  # 本当に続く同じ字は残す
        text = grid.build_cells(page)
        doc.close()
        self.assertRegex(text, r"\] 事務事業名")
        self.assertRegex(text, r"\] ああ")

    def test_every_character_of_the_page_is_kept(self) -> None:
        doc, page = self.make_page()
        kept = grid.layout_text(grid.page_layout(page))
        doc.close()
        for word in ("事務事業名", "広報広聴事業", "令和6年度", "事務事業評価シート"):
            self.assertIn(word, kept)

    def test_layout_keeps_the_table_shape_and_empty_cells(self) -> None:
        doc, page = self.make_mark_table([
            ["事業名", "拡充", "", "規模縮小"],
            ["広報事業", "", "◎", ""],
        ])
        layout = grid.page_layout(page)
        doc.close()
        table = layout["tables"][0]
        self.assertEqual((table["rows"], table["cols"]), (2, 4))
        cells = {(c["r"], c["c"]): c["text"] for c in table["cells"]}
        self.assertEqual(table["widths"], [35.7, 21.4, 21.4, 21.4])
        self.assertEqual(cells[(0, 2)], "")
        self.assertEqual(cells[(1, 2)], "◎")
        self.assertEqual(len(cells), 8)

    def test_garbled_text_layer_is_not_passed(self) -> None:
        glyphs = [grid.Glyph((0, 0, 1, 1), chr(c)) for c in list(range(1, 20)) * 4]
        self.assertTrue(grid.garbled(glyphs))
        self.assertFalse(grid.garbled([grid.Glyph((0, 0, 1, 1), "事") for _ in range(80)]))

    def test_spaced_labels_are_joined(self) -> None:
        line = [grid.Glyph((x, 0, x + 10, 10), c) for x, c in zip((0, 14, 28), "担当課")]
        self.assertEqual(grid.join_line(line), "担当課")
        words = [grid.Glyph((0, 0, 5, 10), "A"), grid.Glyph((8, 0, 13, 10), "B")]
        self.assertEqual(grid.join_line(words), "A B")


if __name__ == "__main__":
    unittest.main()
