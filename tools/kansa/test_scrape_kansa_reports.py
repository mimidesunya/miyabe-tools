"""包括外部監査の報告書取得の判定と保存を確かめる。ネットワークには出ない。"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

import scrape_kansa_reports as k  # noqa: E402


class FiscalYearTest(unittest.TestCase):
    def test_reiwa(self) -> None:
        self.assertEqual(k.fiscal_year("令和6年度包括外部監査結果報告書"), (2024, "令和6年度"))

    def test_fullwidth_and_first_year(self) -> None:
        self.assertEqual(k.fiscal_year("令和７年度")[0], 2025)
        self.assertEqual(k.fiscal_year("令和元年度")[0], 2019)

    def test_heisei(self) -> None:
        self.assertEqual(k.fiscal_year("平成30年度")[0], 2018)

    def test_western(self) -> None:
        self.assertEqual(k.fiscal_year("2023年度 報告書")[0], 2023)

    def test_fiscal_year_wins_over_a_publication_date(self) -> None:
        # 久留米市「公表第1号（令和8年2月2日）」は令和6年度の監査の公表。
        self.assertEqual(k.fiscal_year("公表第1号（令和8年2月2日）", "令和6年度 包括外部監査")[0], 2024)
        self.assertEqual(k.fiscal_year("公表第1号（令和8年2月2日）")[0], 2026)

    def test_first_text_wins_then_falls_back(self) -> None:
        self.assertEqual(k.fiscal_year("報告書", "令和5年度")[0], 2023)
        self.assertEqual(k.fiscal_year("報告書"), (None, ""))


class DocumentKindTest(unittest.TestCase):
    def test_measure_comes_first(self) -> None:
        self.assertEqual(k.document_kind("監査の結果に基づき講じた措置"), "measure")

    def test_chapter_is_part_of_the_report(self) -> None:
        # 札幌市は報告書を章ごとに置く。「概要」を含んでも概要版ではない。
        self.assertEqual(k.document_kind("第1章 外部監査の概要"), "report")
        self.assertEqual(k.document_kind("表紙、目次及び注意事項"), "report")

    def test_summary(self) -> None:
        self.assertEqual(k.document_kind("包括外部監査結果報告書の要旨"), "summary")
        self.assertEqual(k.document_kind("報告書（概要版）"), "summary")

    def test_notice(self) -> None:
        self.assertEqual(k.document_kind("包括外部監査の結果の公表について"), "notice")
        self.assertEqual(k.document_kind("報道発表資料（総務局）"), "notice")

    def test_report(self) -> None:
        self.assertEqual(k.document_kind("令和7年度包括外部監査結果報告書"), "report")

    def test_label_decides_before_the_heading(self) -> None:
        # 群馬県は見出しに「措置」を含むページに報告書の章が並んでいた。
        self.assertEqual(k.document_kind("報告書（監査の結果及び意見4 税債権について）", "監査の結果に基づく措置"), "report")
        self.assertEqual(k.document_kind("本文（表紙 概要）", "包括外部監査"), "report")
        # リンク文字が素のときは見出しに従う。
        self.assertEqual(k.document_kind("令和6年度", "監査の結果に基づき講じた措置"), "measure")

    def test_publication_number_is_a_notice(self) -> None:
        self.assertEqual(k.document_kind("公表第1号（令和8年2月2日）"), "notice")

    def test_file_size_note_is_removed_from_the_title(self) -> None:
        self.assertEqual(k.strip_file_note("公表第1号(592キロバイト)"), "公表第1号")
        self.assertEqual(k.strip_file_note("報告書（PDF：1.2MB）"), "報告書")


class ClassifyLinkTest(unittest.TestCase):
    def test_strong_label_is_taken(self) -> None:
        self.assertEqual(k.classify_link("令和6年度包括外部監査報告書", "https://x/a.pdf", "", False), "houkatsu")

    def test_other_audits_are_not_taken(self) -> None:
        for label in ("令和6年度定期監査結果", "決算審査意見書", "住民監査請求の結果", "指導監査の結果", "内部統制評価報告書"):
            with self.subTest(label=label):
                self.assertEqual(k.classify_link(label, "https://x/a.pdf", "", True), "negative")

    def test_individual_audit_is_only_counted(self) -> None:
        self.assertEqual(k.classify_link("個別外部監査の結果", "https://x/a.pdf", "", True), "individual")

    def test_heading_decides_when_the_label_is_bare(self) -> None:
        # 監査委員のページは見出しで監査の種類を分け、リンクは「報告書」だけのことが多い。
        self.assertEqual(k.classify_link("令和6年度 報告書", "https://x/a.pdf", "包括外部監査", False), "houkatsu")
        self.assertEqual(k.classify_link("令和6年度 報告書", "https://x/a.pdf", "定期監査", True), "negative")
        self.assertEqual(k.classify_link("令和6年度 報告書", "https://x/a.pdf", "個別外部監査", True), "individual")

    def test_bare_label_follows_the_page(self) -> None:
        self.assertEqual(k.classify_link("本文", "https://x/a.pdf", "", True), "houkatsu")
        self.assertEqual(k.classify_link("本文", "https://x/a.pdf", "", False), "unknown")

    def test_forms_are_not_reports(self) -> None:
        self.assertEqual(k.classify_link("包括外部監査人の応募様式", "https://x/a.pdf", "", True), "unrelated")

    def test_romaji_filename_tells_the_audit(self) -> None:
        # 茨城県は素のリンク文字で、ファイル名だけが監査の種類を名乗っていた。
        self.assertEqual(k.classify_link("令和4年度第2回監査結果", "https://x/documents/teikikansakekka-r4.pdf", "", True), "negative")
        self.assertEqual(k.classify_link("P108～P126", "https://x/documents/kansakekka-houkatugaibu250322-3.pdf", "", False), "houkatsu")
        self.assertEqual(k.classify_link("令和5年度 報告書", "https://x/documents/kobetsugaibu-r5.pdf", "", True), "individual")

    def test_entry_context_needs_the_page_to_name_it(self) -> None:
        self.assertTrue(k.entry_is_houkatsu("https://x/kansa/index.html", "包括外部監査の結果", ""))
        self.assertTrue(k.entry_is_houkatsu("https://x/houkatsugaibu", "監査", ""))
        self.assertFalse(k.entry_is_houkatsu("https://x/kansa/kanichikikaku/index.html", "監査結果の公表", ""))

    def test_filename_counts(self) -> None:
        self.assertEqual(k.classify_link("本文", "https://x/%E5%8C%85%E6%8B%AC%E5%A4%96%E9%83%A8%E7%9B%A3%E6%9F%BB.pdf", "", False), "houkatsu")


class PdfLinkTest(unittest.TestCase):
    def test_extension(self) -> None:
        self.assertTrue(k.is_pdf_link("https://x/r7.pdf", "報告書"))
        self.assertTrue(k.is_pdf_link("https://x/r7.PDF?v=1", "報告書"))

    def test_annotated_label_without_extension(self) -> None:
        # 東京都は /documents/d/kansa/r7houkatsu_zenbun の形で配る。
        self.assertTrue(k.is_pdf_link("https://x/documents/d/kansa/r7houkatsu_zenbun", "報告書（PDF 10.1MB）"))

    def test_download_endpoint_needs_query_and_label(self) -> None:
        self.assertTrue(k.is_pdf_link("https://x/download/?id=1", "報告書 PDF"))
        self.assertFalse(k.is_pdf_link("https://x/download/", "報告書 PDF"))
        self.assertFalse(k.is_pdf_link("https://x/download/?id=1", "報告書"))

    def test_other_files_and_pages(self) -> None:
        self.assertFalse(k.is_pdf_link("https://x/a.xlsx", "報告書"))
        self.assertFalse(k.is_pdf_link("https://x/page.html", "報告書"))

    def test_response_check(self) -> None:
        self.assertTrue(k.looks_like_pdf("", "", b"%PDF-1.7"))
        self.assertTrue(k.looks_like_pdf("application/pdf; charset=binary", "", b""))
        self.assertTrue(k.looks_like_pdf("application/octet-stream", 'attachment; filename="a.pdf"', b""))
        self.assertFalse(k.looks_like_pdf("text/html", "", b"<html>"))


class PageLinksTest(unittest.TestCase):
    def test_links_carry_the_nearest_heading(self) -> None:
        html = """
        <h2>定期監査</h2><a href="teiki.pdf">令和6年度 報告書</a>
        <h2>包括外部監査</h2><a href='/h/r6.pdf'>令和6年度 報告書（PDF：1MB）</a>
        <a href="#top">上へ</a><a href="mailto:a@b">連絡</a>
        """
        links = k.page_links("https://x.lg.jp/kansa/index.html", html)
        self.assertEqual([(l.url, l.heading) for l in links], [
            ("https://x.lg.jp/kansa/teiki.pdf", "定期監査"),
            ("https://x.lg.jp/h/r6.pdf", "包括外部監査"),
        ])

    def test_descend_prefers_houkatsu_links(self) -> None:
        # 東京都は全体メニューに別の監査の年度ページが先に並ぶ。
        menu_year = k.Link(url="https://x/kansasochi/r7", label="令和７年", heading="")
        report_year = k.Link(url="https://x/houkatsugaibu/r7", label="令和７年度包括外部監査報告書の提出について", heading="")
        self.assertGreater(k.descend_priority(report_year, True), k.descend_priority(menu_year, True))
        self.assertEqual(k.descend_priority(k.Link("https://x/teiki", "定期監査", ""), True), 0)
        self.assertEqual(k.descend_priority(menu_year, False), 0)
        # 年度だけのリンクから来たページは、包括外部監査の文脈を引き継がない。
        self.assertFalse(k.link_names_houkatsu(menu_year))
        self.assertTrue(k.link_names_houkatsu(report_year))


class HostGuardTest(unittest.TestCase):
    def test_blocks_after_repeated_failures_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard = k.HostGuard(Path(tmp), threshold=3, seconds=100)
            self.assertFalse(guard.failure("a.jp", now=1000))
            self.assertFalse(guard.failure("a.jp", now=1000))
            self.assertTrue(guard.failure("a.jp", now=1000))
            self.assertTrue(guard.blocked("a.jp", now=1050))
            self.assertFalse(guard.blocked("a.jp", now=1101))
            self.assertFalse(guard.blocked("b.jp", now=1050))
            # 別プロセス（別の自治体）からも見える。
            self.assertTrue(k.HostGuard(Path(tmp)).blocked("a.jp", now=1050))

    def test_success_resets_the_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard = k.HostGuard(Path(tmp), threshold=2)
            guard.failure("a.jp", now=1)
            guard.success("a.jp")
            self.assertFalse(guard.failure("a.jp", now=1))


def make_pdf(text: str) -> bytes:
    """本文を持つ小さな PDF。pypdf で抜き出せる形にする。"""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.status_code = status
        self.content = body
        self.headers = headers or {}

    def iter_content(self, chunk_size: int = 1024):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


class FakeFetcher(k.Fetcher):
    """ページと PDF を辞書から返す。"""

    def __init__(self, pages: dict[str, str], files: dict[str, tuple[bytes, dict]], work_root: Path) -> None:
        super().__init__(session=requests.Session(), guard=k.HostGuard(work_root), delay=0)
        self.pages = pages
        self.files = files
        self.file_requests: list[tuple[str, dict]] = []

    def html(self, url: str):
        self.requests_made += 1
        return (url, self.pages[url]) if url in self.pages else None

    def get(self, url: str, *, headers: dict | None = None, stream: bool = False):
        self.requests_made += 1
        self.file_requests.append((url, dict(headers or {})))
        if url not in self.files:
            return FakeResponse(404, b"")
        body, extra = self.files[url]
        if headers and headers.get("If-None-Match") and headers["If-None-Match"] == extra.get("ETag"):
            return FakeResponse(304, b"", extra)
        return FakeResponse(200, body, extra)


ENTRY = "https://www.city.example.lg.jp/kansa/houkatsu.html"
YEAR_PAGE = "https://www.city.example.lg.jp/kansa/houkatsu/r6.html"
REPORT = "https://www.city.example.lg.jp/kansa/r6_houkatsu.pdf"
SUMMARY = "https://www.city.example.lg.jp/kansa/r6_youshi.pdf"
TEIKI = "https://www.city.example.lg.jp/kansa/teiki.pdf"
KOBETSU = "https://www.city.example.lg.jp/kansa/kobetsu.pdf"
OTHER_SITE = "https://other.example.com/r6_houkatsu.pdf"


def site() -> tuple[dict[str, str], dict[str, tuple[bytes, dict]]]:
    pages = {
        ENTRY: f"""<title>包括外部監査</title>
            <h2>包括外部監査</h2>
            <a href="houkatsu/r6.html">令和6年度包括外部監査結果報告書</a>
            <h2>個別外部監査</h2><a href="kobetsu.pdf">令和5年度 報告書</a>
            <h2>定期監査</h2><a href="teiki.pdf">令和6年度 結果</a>
            <a href="{OTHER_SITE}">包括外部監査の参考（PDF）</a>""",
        YEAR_PAGE: """<title>令和6年度包括外部監査結果報告書</title>
            <a href="../r6_houkatsu.pdf">令和6年度包括外部監査結果報告書（PDF：2MB）</a>
            <a href="../r6_youshi.pdf">報告書の要旨</a>""",
    }
    files = {
        REPORT: (make_pdf("Houkatsu gaibu kansa report " * 20), {"Content-Type": "application/pdf", "ETag": '"r1"'}),
        SUMMARY: (make_pdf(""), {"Content-Type": "application/pdf"}),
        TEIKI: (make_pdf("teiki"), {"Content-Type": "application/pdf"}),
        KOBETSU: (make_pdf("kobetsu"), {"Content-Type": "application/pdf"}),
    }
    return pages, files


TARGET = k.Target(code="99999", slug="99999-example-shi", name="例示市", url=ENTRY, crawl_status="enabled")


class ScrapeOneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_once(self, pages, files, **kwargs):
        fetcher = FakeFetcher(pages, files, self.root)
        result = k.scrape_one(TARGET, fetcher, self.root, **kwargs)
        manifest = json.loads((self.root / TARGET.slug / "manifest.json").read_text(encoding="utf-8"))
        return result, manifest, fetcher

    def test_saves_reports_with_source_and_skips_other_audits(self) -> None:
        result, manifest, fetcher = self.run_once(*site())
        documents = {entry["source_url"]: entry for entry in manifest["documents"].values()}
        self.assertEqual(set(documents), {REPORT, SUMMARY})
        report = documents[REPORT]
        self.assertEqual(report["fiscal_year"], 2024)
        self.assertEqual(report["kind"], "report")
        self.assertEqual(report["page_url"], YEAR_PAGE)
        self.assertEqual(report["title"], "令和6年度包括外部監査結果報告書")
        self.assertEqual(report["text_status"], "ok")
        self.assertTrue((self.root / TARGET.slug / report["file"]).is_file())
        text = gzip.decompress((self.root / TARGET.slug / report["text_file"]).read_bytes()).decode("utf-8")
        self.assertIn(f"出典: {REPORT}", text)
        self.assertIn("Houkatsu gaibu kansa report", text)
        # 文字の無い PDF は OCR 待ちにする。
        self.assertEqual(documents[SUMMARY]["kind"], "summary")
        self.assertEqual(documents[SUMMARY]["text_status"], "needs_ocr")
        # 別の監査・別サイトは取りに行かない。個別外部監査は数えるだけ。
        requested = {url for url, _ in fetcher.file_requests}
        self.assertNotIn(TEIKI, requested)
        self.assertNotIn(KOBETSU, requested)
        self.assertNotIn(OTHER_SITE, requested)
        self.assertEqual([entry["source_url"] for entry in manifest["individual_audits"]], [KOBETSU])
        self.assertEqual(result.negative, 1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(manifest["last_run"]["downloaded"], 2)

    def test_second_run_does_not_fetch_again(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        result, _manifest, fetcher = self.run_once(pages, files)
        self.assertEqual(result.skipped_recent, 2)
        self.assertEqual(fetcher.file_requests, [])

    def test_refresh_uses_conditional_request(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        result, _manifest, fetcher = self.run_once(pages, files, refresh_days=0)
        report_request = dict(fetcher.file_requests)[REPORT]
        self.assertEqual(report_request.get("If-None-Match"), '"r1"')
        self.assertEqual(result.unchanged, 2)
        self.assertEqual(result.downloaded + result.updated, 0)

    def test_changed_file_is_replaced(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        files[REPORT] = (make_pdf("Revised report text " * 20), {"Content-Type": "application/pdf", "ETag": '"r2"'})
        result, manifest, _fetcher = self.run_once(pages, files, refresh_days=0)
        self.assertEqual(result.updated, 1)
        entry = next(e for e in manifest["documents"].values() if e["source_url"] == REPORT)
        text = gzip.decompress((self.root / TARGET.slug / entry["text_file"]).read_bytes()).decode("utf-8")
        self.assertIn("Revised report text", text)

    def test_removed_document_is_marked_and_kept(self) -> None:
        pages, files = site()
        _result, manifest, _ = self.run_once(pages, files)
        summary = next(e for e in manifest["documents"].values() if e["source_url"] == SUMMARY)
        pages[YEAR_PAGE] = pages[YEAR_PAGE].replace('<a href="../r6_youshi.pdf">報告書の要旨</a>', "")
        result, manifest, _ = self.run_once(pages, files)
        entry = next(e for e in manifest["documents"].values() if e["source_url"] == SUMMARY)
        self.assertTrue(entry["missing_since"])
        self.assertTrue((self.root / TARGET.slug / summary["file"]).is_file())
        self.assertEqual(result.missing, 1)

    def test_limited_fetch_does_not_mark_the_rest_missing(self) -> None:
        pages, files = site()
        result, manifest, _ = self.run_once(pages, files, max_fetch=1)
        self.assertEqual(len(manifest["documents"]), 1)
        self.assertEqual(result.missing, 0)

    def test_html_answer_is_not_saved_as_a_report(self) -> None:
        pages, files = site()
        files[REPORT] = (b"<html>not found</html>", {"Content-Type": "text/html"})
        result, manifest, _ = self.run_once(pages, files)
        entry = next(e for e in manifest["documents"].values() if e["source_url"] == REPORT)
        self.assertNotIn("file", entry)
        self.assertIn("PDF ではない", entry["last_error"])
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.status, "partial")

    def test_too_many_candidates_are_not_fetched(self) -> None:
        pages, files = site()
        result, manifest, fetcher = self.run_once(pages, files, max_documents=1)
        self.assertEqual(result.status, "too_many_documents")
        self.assertEqual(fetcher.file_requests, [])
        self.assertEqual(manifest["documents"], {})

    def test_temporary_outage_does_not_mark_documents_missing(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        result, manifest, _ = self.run_once({}, files)
        self.assertEqual(result.status, "entry_unreachable")
        self.assertEqual(result.missing, 0)
        self.assertTrue(all(not e.get("missing_since") for e in manifest["documents"].values()))
        self.assertEqual(len(manifest["documents"]), 2)

    def test_unreachable_entry(self) -> None:
        result, manifest, _ = self.run_once({}, {})
        self.assertEqual(result.status, "entry_unreachable")
        self.assertEqual(manifest["documents"], {})


if __name__ == "__main__":
    unittest.main()
