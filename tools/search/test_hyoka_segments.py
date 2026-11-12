"""事務事業評価の PDF を事業ごとのシートとそれ以外に分ける。OpenSearch には繋がない。"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

SEARCH_DIR = Path(__file__).resolve().parent
ROOT = SEARCH_DIR.parents[1]
sys.path.insert(0, str(SEARCH_DIR))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "hyoka"))

import build_opensearch_index as indexer  # noqa: E402
import hyoka_projects  # noqa: E402
import hyoka_source_records as records  # noqa: E402

SLUG = "14130-kawasaki-shi"
DOC = "0123456789abcdef"


def pages(*texts):
    return [(number, text, "form_feed") for number, text in enumerate(texts, 1) if text]


def test_segment_pages_groups_sheet_and_other():
    segments = records.segment_pages(pages("体系図", "シート1", "続き", "シート2", "付録"), [(2, 3), (4, 4)])
    assert [(start, end, kind) for start, end, _, _, kind in segments] == [
        (1, 1, "other"), (2, 3, "sheet"), (4, 4, "sheet"), (5, 5, "other")]
    assert segments[1][2] == "シート1\n\n続き"


def test_overlapping_records_make_a_table_and_empty_ranges_are_dropped():
    segments = records.segment_pages(pages("総括", "", "x"), [(1, 1), (1, 1), (2, 2)])
    assert [(start, end, kind) for start, end, _, _, kind in segments] == [(1, 1, "table"), (3, 3, "other")]


def test_documents_without_records_or_pages():
    assert [kind for *_, kind in records.segment_pages(pages("a", "b"), [])] == ["page", "page"]
    whole = [(None, "本文", "saved_text_no_page_boundaries")]
    assert records.segment_pages(whole, [])[0][4] == "document"
    assert records.segment_pages(whole, [(3, 3)])[0][4] == "sheet"
    assert records.segment_pages(whole, [(3, 3), (5, 6)])[0][4] == "table"


def write_display(display: Path, items: list[dict]) -> None:
    path = display / "records" / SLUG / f"{DOC}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"slug": SLUG, "doc_id": DOC, "records": items}), encoding="utf-8")


def test_record_spans_use_pages_then_page(tmp_path):
    write_display(tmp_path, [{"id": "a", "page": 2, "pages": [2, 3]}, {"id": "b", "page": 5}, {"id": "c"}])
    assert records.record_spans(tmp_path, SLUG, DOC) == [(2, 3), (5, 5)]
    assert records.record_spans(tmp_path, SLUG, "f" * 16) == []
    assert records.record_spans(None, SLUG, DOC) == []


def make_work(root: Path, body: str, page_count: int) -> dict:
    work = root / "work" / SLUG
    (work / "text").mkdir(parents=True)
    entry = {"title": "政策 4-8", "year_label": "令和7年度", "fiscal_year": 2025, "kind": "project_evaluation",
             "source_url": "https://example.jp/4-8.pdf", "page_url": "https://example.jp/hyoka", "sha256": "a" * 64,
             "fetched_at": "2026-11-03T20:00:00+09:00", "text_status": "ok", "status": "ok", "file_type": "pdf",
             "pages": page_count, "text_file": f"text/{DOC}.txt.gz", "file": f"source/{DOC}.pdf"}
    (work / "text" / f"{DOC}.txt.gz").write_bytes(gzip.compress(("題名: x\n年度: x\n種別: x\n出典: x\n掲載ページ: x\n\n" + body).encode("utf-8")))
    (work / "manifest.json").write_text(json.dumps({"slug": SLUG, "jis_code": "14130", "name": "川崎市", "documents": {DOC: entry}},
                                                  ensure_ascii=False), encoding="utf-8")
    return entry


def test_index_documents_are_sheets_with_page_ranges(tmp_path):
    make_work(tmp_path, "政策体系図\f事業Aの概要\f事業Aの評価\f事業B", 4)
    display = tmp_path / "display"
    write_display(display, [{"id": "a", "page": 2, "pages": [2, 3]}, {"id": "b", "page": 4}])
    indexer.reset_source_integrity_tracking()
    found = dict(indexer.iter_hyoka_documents(root=tmp_path / "work", display_dir=display))
    assert list(found) == [f"hyoka:{SLUG}:{DOC}:p1", f"hyoka:{SLUG}:{DOC}:p2-3", f"hyoka:{SLUG}:{DOC}:p4"]
    sheet = found[f"hyoka:{SLUG}:{DOC}:p2-3"]
    assert (sheet["page"], sheet["page_end"], sheet["segment"]) == (2, 3, "sheet")
    assert sheet["body"] == "事業Aの概要\n\n事業Aの評価"
    assert sheet["source_page_link"].endswith("#page=2")
    assert found[f"hyoka:{SLUG}:{DOC}:p1"]["segment"] == "other"


def test_mapping_has_the_added_fields():
    properties = json.loads((SEARCH_DIR / "index_mappings.json").read_text(encoding="utf-8"))["properties"]
    for name in indexer.HYOKA_ADDED_FIELDS:
        assert name in properties


def test_project_index_picks_the_segment_containing_the_record():
    metadata = {f"{SLUG}:{DOC}": {"ids": [f"hyoka:{SLUG}:{DOC}:p1", f"hyoka:{SLUG}:{DOC}:p2-3", f"hyoka:{SLUG}:{DOC}:p4"]}}
    assert hyoka_projects.document_meta(metadata, SLUG, DOC, 3)["document_id"] == f"hyoka:{SLUG}:{DOC}:p2-3"
    assert hyoka_projects.document_meta(metadata, SLUG, DOC, 4)["document_id"] == f"hyoka:{SLUG}:{DOC}:p4"
    assert hyoka_projects.document_meta(metadata, SLUG, DOC, 9)["document_id"] == f"hyoka:{SLUG}:{DOC}:p1"


def pdf_bytes(texts: list[str]) -> bytes:
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for text in texts:
        if text:
            pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_repage_rewrites_saved_text_with_page_breaks(tmp_path):
    pytest.importorskip("pypdf")
    import document_crawl
    import repage_text

    raw = pdf_bytes(["Policy map", "Sheet A", "", "Sheet B"])
    old_body, page_count = document_crawl.extract_pdf_text(raw)
    entry = make_work(tmp_path, old_body, page_count)
    source = tmp_path / "work" / SLUG / "source" / f"{DOC}.pdf"
    source.parent.mkdir()
    source.write_bytes(raw)
    manifest = tmp_path / "work" / SLUG / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["documents"][DOC]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    text_path = tmp_path / "work" / SLUG / entry["text_file"]
    before = text_path.read_bytes()

    assert repage_text.main(["--root", str(tmp_path / "work"), "--dry-run"]) == 0
    assert text_path.read_bytes() == before
    assert repage_text.main(["--root", str(tmp_path / "work"), "--slug", SLUG]) == 0
    body = records.read_body(text_path)
    assert body.count("\f") == 3
    split = records.split_pages(body, data["documents"][DOC])
    assert [(number, reason) for number, _, reason in split] == [(1, "form_feed"), (2, "form_feed"), (4, "form_feed")]
    assert gzip.decompress(text_path.read_bytes()).decode("utf-8").startswith("題名: x\n")
    # もう区切りがあれば書き直さない。原典が違えば触らない。
    assert repage_text.repage_one(str(tmp_path / "work" / SLUG), DOC, data["documents"][DOC], True, False) == "already"
    # 頁の中に改頁文字が紛れた本文（区切りの数が頁の数と合わない）は取り出し直す。
    text_path.write_bytes(gzip.compress(("題名: x\n年度: x\n種別: x\n出典: x\n掲載ページ: x\n\n" + old_body + "\f").encode("utf-8")))
    assert repage_text.repage_one(str(tmp_path / "work" / SLUG), DOC, data["documents"][DOC], True, False) == "paged"
    assert records.read_body(text_path).count("\f") == 3
    assert repage_text.repage_one(str(tmp_path / "work" / SLUG), DOC, {**data["documents"][DOC], "sha256": "b" * 64}, True, False) \
        == "sha256_mismatch"
    # 壊れた本文（gzip でない）は印にして、全体を止めない。
    text_path.write_bytes(b"not gzip")
    assert repage_text.repage_one(str(tmp_path / "work" / SLUG), DOC, data["documents"][DOC], True, False) == "error:BadGzipFile"


def test_collector_keeps_page_breaks():
    pytest.importorskip("pypdf")
    import scrape_hyoka_docs

    body, extra = scrape_hyoka_docs.extract_document_text("pdf", pdf_bytes(["One", "", "Three"]))
    assert extra == {"pages": 3}
    assert body.split("\f") == ["One", "", "Three"]


def test_project_index_metadata_groups_segment_ids_by_document():
    import build_hyoka_projects_index

    class Client:
        def __init__(self):
            self.pages = [[{"_id": f"hyoka:{SLUG}:{DOC}:p21-22", "_source": {"slug": SLUG, "local_id": f"{DOC}:p21-22", "fiscal_year": 2025}},
                           {"_id": f"hyoka:{SLUG}:{DOC}:p1", "_source": {"slug": SLUG, "local_id": f"{DOC}:p1", "fiscal_year": 2025}}], []]

        def request(self, method, path, **kwargs):
            if method == "DELETE":
                return {}
            return {"_scroll_id": "s", "hits": {"hits": self.pages.pop(0)}}

    metadata = build_hyoka_projects_index.metadata_from_index(Client(), "hyoka-current")
    assert list(metadata) == [f"{SLUG}:{DOC}"]
    assert metadata[f"{SLUG}:{DOC}"]["ids"] == [f"hyoka:{SLUG}:{DOC}:p1", f"hyoka:{SLUG}:{DOC}:p21-22"]
    assert hyoka_projects.document_meta(metadata, SLUG, DOC, 21)["document_id"] == f"hyoka:{SLUG}:{DOC}:p21-22"
