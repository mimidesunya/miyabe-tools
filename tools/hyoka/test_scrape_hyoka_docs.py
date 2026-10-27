"""事務事業評価の取得の判定と保存を確かめる。ネットワークには出ない。"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

import scrape_hyoka_docs as h  # noqa: E402
from test_document_crawl import make_docx, make_xlsx, make_zip  # noqa: E402


def take(label: str, url: str = "https://x.lg.jp/a.pdf", heading: str = "", **kwargs) -> tuple[str, str]:
    kwargs.setdefault("page_is_evaluation", True)
    return h.classify_file_link(label, url, heading, **kwargs)


class ProjectNameTest(unittest.TestCase):
    """事業ごとに 1 ファイルを置く自治体では、リンク文字が事業の名前になる。"""

    def test_project_names_on_a_sheet_page_are_taken(self) -> None:
        # 札幌市・堺市の評価調書。分野の言葉が入っていても事業の名前なので取る。
        for label in ("広報誌等発行費", "橋りょう長寿命化修繕費", "（公社）札幌市シルバー人材センター運営補助金",
                      "鳥獣被害防止対策推進費", "文化芸術基本計画推進費", "維持費（指定管理費以外分）",
                      "空き家対策推進事業", "介護予防普及啓発事業（北区）", "選挙人名簿管理費",
                      "ＡＩを活用した公共事業の最適化に関する研究費", "地方創生推進費"):
            with self.subTest(label=label):
                self.assertEqual(take(label), ("take", "project_evaluation"))

    def test_department_names_are_not_other_schemes(self) -> None:
        # 岐阜市は課ごとの評価シート。「監査委員」「保育」「地方創生」は部署の名前。
        for label in ("監査委員事務局事業評価シート", "子ども保育課（事業評価シート）", "地方創生推進課（事業評価シート）"):
            with self.subTest(label=label):
                self.assertEqual(take(label)[0], "take")


class ExclusionTest(unittest.TestCase):
    def test_other_schemes_are_excluded_with_a_reason(self) -> None:
        cases = {
            "令和6年度教育委員会の事務の点検・評価報告書": "education_board",
            "人事評価制度の概要": "personnel",
            "環境影響評価書": "environment_assessment",
            "総合評価落札方式の実施状況": "procurement",
            "令和6年度公共事業再評価結果": "public_works",
            "都市再生整備計画事後評価シート（駅周辺地区）": "public_works",
            "電源立地地域対策交付金事業評価報告書": "grant_project",
            "まち・ひと・しごと創生総合戦略（第2期）の効果検証": "regional_strategy",
            "総合戦略事業評価シート": "regional_strategy",
            "公社等経営評価の結果": "public_corporation",
            "指定管理者評価シート": "designated_manager",
            "水道局-事業評価について": "public_enterprise",
            "福祉サービス第三者評価": "school_welfare",
            "固定資産評価基準": "tax_assessment",
            "パブリックコメントの実施結果": "public_comment",
            "主要施策の成果説明書": "settlement_report",
            "評価シート（様式）": "form",
            "意見用紙(ワード:26KB)": "form",
            "札幌市行政評価実施要綱": "system_document",
            "参考：事業評価調書の見方": "system_document",
            "委員名簿": "meeting_admin",
            "令和2年度「まちづくりのための市民アンケート」結果": "survey",
            "阿見町組織機構図": "organization_chart",
            "第6次総合計画（PDF：2MB）": "other_plan",
            "北区基本計画2024": "other_plan",
            "北海道人口ビジョン(2019年度改訂版)": "other_plan",
            "令和６年度公共事業新規事業評価結果": "public_works",
            "大規模施設建設事業評価": "public_works",
            "研究開発事業の評価に関する総括表": "research_evaluation",
            "森林環境税事業の評価に関する総括表": "grant_project",
            "「第2期川崎市子ども・若者の未来応援プラン」の点検・評価結果報告書": "plan_progress",
            "職員名簿（施策8）": "meeting_admin",
            "平成28年度川内村の給与・定員管理等について": "personnel",
            "令和7年度当初予算": "budget",
        }
        for label, reason in cases.items():
            with self.subTest(label=label):
                self.assertEqual(take(label), ("excluded", reason))

    def test_jimu_jigyo_overrides_soft_rules(self) -> None:
        # 「事務事業」と書いてあれば、地方創生・教育委員会の言葉が混じっても取る。
        self.assertEqual(take("事務事業評価（地方創生関連事業）")[0], "take")
        self.assertEqual(take("事務事業評価（教育委員会）")[0], "take")
        # 人事評価・公共事業の再評価は書いてあっても取らない。
        self.assertEqual(take("事務事業評価と人事評価の連携")[0], "excluded")

    def test_report_chapters_and_plan_reviews_inside_the_evaluation_are_taken(self) -> None:
        # 江東区の行政評価の報告書の章。予算の言葉が入っても予算書ではない。
        self.assertEqual(take("4.新たな取り組み等（平成27年当初予算）（P117～126）")[0], "take")
        # 総合計画の進捗を施策評価として行う形は取る。
        self.assertEqual(take("第5次総合計画の進捗状況（施策評価）"), ("take", "policy_evaluation"))

    def test_public_comment_page_that_hosts_the_sheets(self) -> None:
        # 三田市は意見募集の頁に評価調書を置く。文脈の意見募集では落とさない。
        self.assertEqual(take("令和7年度実施行政評価（内部評価）", heading="意見を募集する対象")[0], "take")
        self.assertEqual(take("パブリックコメント意見の概要と市の考え方"), ("excluded", "public_comment"))

    def test_post_evaluation_sheet_alone_is_real(self) -> None:
        # 三条市・大月市の「事後評価シート」は事務事業評価そのもの。
        self.assertEqual(take("事後評価シート"), ("take", "project_evaluation"))

    def test_heading_and_page_context(self) -> None:
        # 板橋区の指定管理の頁。リンクに「評価」とあっても取らない。
        self.assertEqual(take("令和6年度サービス水準評価結果", page_exclusion="designated_manager"),
                         ("excluded", "designated_manager"))
        self.assertEqual(take("令和6年度 報告書", heading="教育委員会の点検・評価"), ("excluded", "education_board"))
        # 「事務事業」と名乗るファイルは、文脈が別制度でも取る。
        self.assertEqual(take("令和6年度事務事業評価", page_exclusion="designated_manager")[0], "take")
        # 様式の見出しの下でも、評価結果は取る。
        self.assertEqual(take("令和6年度評価結果", heading="様式・記入要領")[0], "take")

    def test_bare_label_needs_an_evaluation_page(self) -> None:
        # 津島市は分野の名前だけで評価表を並べる。評価のページの上なら取る。
        self.assertEqual(take("1 健康づくり(PDF:119KB)")[0], "take")
        self.assertEqual(take("1 健康づくり(PDF:119KB)", page_is_evaluation=False), ("no_signal", ""))
        self.assertEqual(take("令和6年度 報告書", heading="行政評価", page_is_evaluation=False)[0], "take")
        self.assertEqual(take("事務事業評価シート", page_is_evaluation=False)[0], "take")


class KindTest(unittest.TestCase):
    def test_kinds_from_the_label(self) -> None:
        self.assertEqual(h.document_kind("令和6年度行政評価外部評価報告書"), "external_review")
        self.assertEqual(h.document_kind("令和6年度事務事業分【令和7年11月具申】"), "external_review")
        self.assertEqual(h.document_kind("令和6年度事務事業評価結果一覧"), "overview")
        self.assertEqual(h.document_kind("施策評価シート"), "policy_evaluation")
        self.assertEqual(h.document_kind("施策評価・事務事業評価結果"), "project_evaluation")
        self.assertEqual(h.document_kind("令和8年度北区事務事業評価"), "project_evaluation")
        self.assertEqual(h.document_kind("施策26 地域防災力の強化"), "policy_evaluation")
        self.assertEqual(h.document_kind("政策事前評価書"), "policy_evaluation")

    def test_collection_plan_is_not_an_overview(self) -> None:
        # 「ごみ収集計画費」の「集計」に当たらない。
        self.assertEqual(h.document_kind("ごみ収集計画費"), "project_evaluation")

    def test_heading_then_page_title(self) -> None:
        # 阿見町は「第1章 …」の PDF が施策評価の頁に並ぶ。
        self.assertEqual(h.document_kind("第1章 ふれあいあふれる協働のまちづくり", "令和7年度の評価結果",
                                         "施策評価を実施しています"), "policy_evaluation")
        self.assertEqual(h.document_kind("令和6年度", "行政評価委員会の意見"), "external_review")
        # 概要・一覧はリンク文字だけで決める。北区は「…事務事業評価の概要」の頁に本体を置く。
        self.assertEqual(h.document_kind("令和8年度北区", "", "令和8年度事務事業評価の概要"), "project_evaluation")


class FileLinkTest(unittest.TestCase):
    def test_extensions(self) -> None:
        cases = {
            "https://x/r6.pdf": "pdf", "https://x/r6.PDF?v=1": "pdf", "https://x/sheet.xlsx": "xlsx",
            "https://x/sheet.xlsm": "xlsm", "https://x/old.xls": "xls", "https://x/report.docx": "docx",
            "https://x/old.doc": "doc", "https://x/all.zip": "zip", "https://x/list.csv": "csv",
        }
        for url, kind in cases.items():
            with self.subTest(url=url):
                self.assertEqual(h.link_file_type(url, "評価シート"), kind)

    def test_note_without_extension(self) -> None:
        self.assertEqual(h.link_file_type("https://x/documents/d/r6sheet", "評価シート（エクセル：30KB）"), "xlsx")
        self.assertEqual(h.link_file_type("https://x/documents/d/r6sheet", "評価シート（Word 20KB）"), "docx")
        self.assertEqual(h.link_file_type("https://x/documents/d/r6", "報告書（PDF 10.1MB）"), "pdf")
        self.assertEqual(h.link_file_type("https://x/download.php?id=3", "評価シート Excel"), "xlsx")

    def test_pages_and_assets(self) -> None:
        self.assertIsNone(h.link_file_type("https://x/hyoka/r6.html", "令和6年度"))
        self.assertIsNone(h.link_file_type("https://x/download/", "評価シート PDF"))
        self.assertIsNone(h.link_file_type("https://x/banner.png", "評価"))

    def test_size_hint_from_the_note(self) -> None:
        self.assertEqual(h.size_hint("区域まちづくり事業（北区）（PDF：705KB）"), 705 * 1024)
        self.assertEqual(h.size_hint("報告書（PDF：1.2MB）"), int(1.2 * 1024 * 1024))
        self.assertEqual(h.size_hint("公表第1号(592キロバイト)"), 592 * 1024)
        self.assertEqual(h.size_hint("総務課"), 0)

    def test_entities_are_unescaped(self) -> None:
        self.assertEqual(h.unescape_label("1&nbsp; 安心の子育て環境づくりプロジェクト"), "1 安心の子育て環境づくりプロジェクト")
        self.assertEqual(h.unescape_label("R&amp;D 推進費"), "R&D 推進費")

    def test_title_drops_the_file_note(self) -> None:
        self.assertEqual(h.strip_file_note("事業評価シート（エクセル：30KB）"), "事業評価シート")
        self.assertEqual(h.strip_file_note("区域まちづくり事業（北区）（PDF：705KB）"), "区域まちづくり事業（北区）")


class YearTest(unittest.TestCase):
    def candidate(self, **kwargs) -> h.Candidate:
        values = dict(url="https://x/a.pdf", label="", page_url="https://x/p.html", heading="", page_title="",
                      file_type="pdf", kind="project_evaluation")
        values.update(kwargs)
        return h.Candidate(**values)

    def test_label_heading_title_order(self) -> None:
        self.assertEqual(h.candidate_year(self.candidate(label="令和6年度事務事業評価", heading="令和5年度")),
                         (2024, "令和6年度", "link_text"))
        self.assertEqual(h.candidate_year(self.candidate(label="総務課", heading="令和5年度")), (2023, "令和5年度", "heading"))
        self.assertEqual(h.candidate_year(self.candidate(label="総務課", page_title="2025年度 評価結果")),
                         (2025, "2025年度", "page_title"))

    def test_romaji_in_the_url_then_the_parent_link(self) -> None:
        # 堺市の部局ページは題名が「北区役所」だけで、年度は URL の jimujigyo_r6 にある。
        self.assertEqual(h.candidate_year(self.candidate(url="https://x/jimujigyo_r6/sosiki/kitaku.files/216-002.pdf")),
                         (2024, "令和6年度", "url"))
        self.assertEqual(h.candidate_year(self.candidate(page_url="https://x/hyoka/h30/index.html")),
                         (2018, "平成30年度", "page_url"))
        self.assertEqual(h.candidate_year(self.candidate(path_year=2022)), (2022, "2022年度", "parent_link"))
        self.assertEqual(h.candidate_year(self.candidate()), (None, "", ""))
        # 札幌市のファイル名 20251037152.pdf は年度ではなく番号として扱う。
        self.assertEqual(h.romaji_year("https://x/documents/20251037152.pdf"), (None, ""))

    def test_year_only_links(self) -> None:
        self.assertTrue(h.is_year_link("令和7年度"))
        self.assertTrue(h.is_year_link("2025年(令和7年)度"))
        self.assertFalse(h.is_year_link("令和8年度阿見町組織機構図"))
        self.assertFalse(h.is_year_link("評価シート"))


class DescendTest(unittest.TestCase):
    def test_sheets_first_committees_later(self) -> None:
        sheets = h.Link("https://x/zikohyouka/r7.html", "事業評価調書を見る", "自己評価の結果")
        results = h.Link("https://x/kekka/index.html", "評価結果", "行政評価制度")
        committee = h.Link("https://x/iinkai/r7/no11.html", "令和7年度第11回行政評価委員会を開催しました。", "")
        self.assertGreater(h.descend_priority(sheets, True), h.descend_priority(results, True))
        self.assertGreater(h.descend_priority(results, True), h.descend_priority(committee, True))
        self.assertGreater(h.descend_priority(committee, True), 0)

    def test_other_schemes_and_unrelated_links_are_not_followed(self) -> None:
        for link in (
            h.Link("https://x/kensetsu/hyoka/index.html", "札幌市の公共事業評価", "行政評価の結果は、"),
            h.Link("https://x/kyoiku/tenkenhyouka.html", "教育委員会の事務の点検・評価", ""),
            h.Link("https://x/shusshi/system.html", "札幌市出資団体評価システム", ""),
            h.Link("https://x/0000009851.html", "令和8年度阿見町組織機構図", "施策評価を実施していますへの別ルート"),
            # 北区は行政運営のページを全部 /evaluation/ の下に置く。
            h.Link("https://x/city-information/evaluation/1011931.html", "内部統制", "行政運営"),
            h.Link("https://x/iinkai/r8/no7.html", "令和8年度第7回委員会を開催します。", "行政評価制度"),
        ):
            with self.subTest(label=link.label):
                self.assertEqual(h.descend_priority(link, True), 0)

    def test_year_and_department_links_on_evaluation_pages(self) -> None:
        year = h.Link("https://x/kekka/r7.html", "令和7年度", "評価結果の公表")
        self.assertGreater(h.descend_priority(year, True), 0)
        self.assertEqual(h.descend_priority(year, False), 0)
        # 栃木県は「政策評価」の見出しの下に年度つきの名前で並べる。
        management = h.Link("https://x/a01/pref/seisakuhyouka/manejimento/08management.html",
                            "令和8(2026)年度重点戦略マネジメント", "政策評価")
        self.assertGreater(h.descend_priority(management, True), 0)
        self.assertEqual(h.descend_priority(h.Link("https://x/plan.html", "北区中期計画（令和6年度～令和8年度）",
                                                   "関連リンク"), True), 0)
        # 札幌市の「事業評価調書を見る」の頁に並ぶ局の名前。
        bureau = h.Link("https://x/zikohyouka/r7kyoku/soumu.html", "総務局", "局一覧")
        self.assertGreater(h.descend_priority(bureau, True, sheet_index=True), 0)
        self.assertEqual(h.descend_priority(bureau, True, sheet_index=False), 0)


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
    def __init__(self, status: int, body: bytes, headers: dict | None = None, url: str = "") -> None:
        self.status_code = status
        self.content = body
        self.headers = headers or {}
        self.url = url

    def iter_content(self, chunk_size: int = 1024):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


class FakeFetcher(h.Fetcher):
    """ページとファイルを辞書から返す。"""

    def __init__(self, pages: dict[str, str], files: dict[str, tuple[bytes, dict]], work_root: Path) -> None:
        super().__init__(session=requests.Session(), guard=h.HostGuard(work_root), delay=0)
        self.pages = pages
        self.files = files
        self.file_requests: list[tuple[str, dict]] = []
        self.page_requests: list[str] = []

    def html(self, url: str):
        self.requests_made += 1
        self.page_requests.append(url)
        return (url, self.pages[url]) if url in self.pages else None

    def get(self, url: str, *, headers: dict | None = None, stream: bool = False):
        self.requests_made += 1
        self.file_requests.append((url, dict(headers or {})))
        if url not in self.files:
            return FakeResponse(404, b"", url=url)
        body, extra = self.files[url]
        if headers and headers.get("If-None-Match") and headers["If-None-Match"] == extra.get("ETag"):
            return FakeResponse(304, b"", extra, url=url)
        return FakeResponse(200, body, extra, url=url)


BASE = "https://www.city.example.lg.jp/hyoka/"
ENTRY = BASE + "index.html"
YEAR_PAGE = BASE + "r6.html"
SHEET_PDF = BASE + "r6/somu_jimujigyo.pdf"
SHEET_XLSX = BASE + "r6/kikaku.xlsx"
REPORT_DOCX = BASE + "r6/gaibu.docx"
ARCHIVE = BASE + "r5/sheets.zip"
YOUKOU = BASE + "youkou.pdf"
JINJI = BASE + "jinji.pdf"
KOUKYOU = BASE + "kokyo/index.html"
OTHER_SITE = "https://other.example.com/r6.pdf"


def site() -> tuple[dict[str, str], dict[str, tuple[bytes, dict]]]:
    pages = {
        ENTRY: f"""<title>行政評価｜例示市</title>
            <h1>行政評価</h1>
            <h2>事務事業評価</h2>
            <a href="r6.html">令和6年度事務事業評価</a>
            <a href="r5/sheets.zip">令和5年度事務事業評価シート一式（ZIP：3MB）</a>
            <h2>制度</h2>
            <a href="youkou.pdf">例示市行政評価実施要綱（PDF：60KB）</a>
            <a href="jinji.pdf">人事評価制度について（PDF）</a>
            <a href="kokyo/index.html">公共事業評価</a>
            <a href="{OTHER_SITE}">他団体の評価シート（PDF）</a>""",
        YEAR_PAGE: """<title>令和6年度事務事業評価｜例示市</title>
            <h2>評価シート</h2>
            <a href="r6/somu_jimujigyo.pdf">総務課（PDF：200KB）</a>
            <a href="r6/kikaku.xlsx">企画課（エクセル：30KB）</a>
            <h2>外部評価</h2>
            <a href="r6/gaibu.docx">行政評価委員会の意見（ワード：20KB）</a>
            <table><tr><th>事業名</th><th>事業費</th><th>評価</th><th>今後の方向</th></tr></table>""",
    }
    files = {
        SHEET_PDF: (make_pdf("Jimu jigyo hyoka sheet " * 20), {"Content-Type": "application/pdf", "ETag": '"s1"'}),
        SHEET_XLSX: (make_xlsx([["事業名", "決算額", "今後の方向"], ["企画調整事業", "1200", "継続"]]),
                     {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
        REPORT_DOCX: (make_docx(["行政評価委員会の意見", "事業の目的を見直すこと。"]),
                      {"Content-Type": "application/octet-stream"}),
        ARCHIVE: (make_zip({"r5/somu.pdf": b"%PDF-1.4", "r5/kikaku.pdf": b"%PDF-1.4"}), {"Content-Type": "application/zip"}),
        YOUKOU: (make_pdf("youkou"), {"Content-Type": "application/pdf"}),
        JINJI: (make_pdf("jinji"), {"Content-Type": "application/pdf"}),
    }
    return pages, files


TARGET = h.Target(code="99999", slug="99999-example-shi", name="例示市", url=ENTRY, crawl_status="enabled")


class ScrapeOneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_once(self, pages, files, **kwargs):
        fetcher = FakeFetcher(pages, files, self.root)
        result = h.scrape_one(TARGET, fetcher, self.root, **kwargs)
        manifest_path = self.root / TARGET.slug / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        return result, manifest, fetcher

    def documents(self, manifest) -> dict[str, dict]:
        return {entry["source_url"]: entry for entry in manifest["documents"].values()}

    def test_saves_sheets_with_provenance_and_skips_other_schemes(self) -> None:
        result, manifest, fetcher = self.run_once(*site())
        documents = self.documents(manifest)
        self.assertEqual(set(documents), {SHEET_PDF, SHEET_XLSX, REPORT_DOCX, ARCHIVE})
        sheet = documents[SHEET_PDF]
        self.assertEqual(sheet["page_url"], YEAR_PAGE)
        self.assertEqual(sheet["link_text"], "総務課（PDF：200KB）")
        self.assertEqual(sheet["title"], "総務課")
        self.assertEqual(sheet["heading"], "評価シート")
        self.assertEqual((sheet["fiscal_year"], sheet["year_label"], sheet["year_source"]),
                         (2024, "令和6年度", "page_title"))
        self.assertEqual(sheet["kind"], "project_evaluation")
        self.assertEqual(sheet["status"], "ok")
        self.assertEqual(sheet["file"], f"source/{h.doc_id_for(SHEET_PDF)}.pdf")
        self.assertEqual(len(sheet["sha256"]), 64)
        self.assertTrue(sheet["bytes"] > 0 and sheet["fetched_at"] and sheet["etag"] == '"s1"')
        text = gzip.decompress((self.root / TARGET.slug / sheet["text_file"]).read_bytes()).decode("utf-8")
        self.assertIn(f"出典: {SHEET_PDF}", text)
        self.assertIn(f"掲載ページ: {YEAR_PAGE}", text)
        # Excel と Word は本文を抜き出す。ZIP はそのまま置いて中の名前を残す。
        xlsx = documents[SHEET_XLSX]
        self.assertEqual((xlsx["file_type"], xlsx["text_status"]), ("xlsx", "ok"))
        self.assertTrue(xlsx["file"].endswith(".xlsx"))
        body = gzip.decompress((self.root / TARGET.slug / xlsx["text_file"]).read_bytes()).decode("utf-8")
        self.assertIn("企画調整事業\t1200\t継続", body)
        docx = documents[REPORT_DOCX]
        self.assertEqual((docx["file_type"], docx["kind"], docx["text_status"]), ("docx", "external_review", "ok"))
        archive = documents[ARCHIVE]
        self.assertEqual((archive["file_type"], archive["text_status"]), ("zip", "archive"))
        self.assertEqual(archive["archive_members"], ["r5/somu.pdf", "r5/kikaku.pdf"])
        self.assertEqual(archive["fiscal_year"], 2023)
        # 別制度・要綱・別サイトは取りに行かず、理由ごとに数える。
        requested = {url for url, _ in fetcher.file_requests}
        self.assertFalse({YOUKOU, JINJI, OTHER_SITE} & requested)
        self.assertNotIn(KOUKYOU, fetcher.page_requests)
        self.assertEqual(manifest["last_run"]["excluded"], {"personnel": 1, "system_document": 1})
        self.assertEqual(manifest["excluded_examples"]["personnel"][0]["url"], JINJI)
        self.assertEqual(result.offsite_files, 1)
        self.assertEqual(manifest["last_run"]["kinds"], {"external_review": 1, "project_evaluation": 3})
        self.assertEqual(manifest["last_run"]["downloaded"], 4)
        self.assertEqual(manifest["last_run"]["html_tables"], 1)
        self.assertFalse(manifest["last_run"]["html_only"])
        self.assertEqual(result.status, "ok")

    def test_second_run_does_not_fetch_again(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        result, _manifest, fetcher = self.run_once(pages, files)
        self.assertEqual(result.skipped_recent, 4)
        self.assertEqual(fetcher.file_requests, [])

    def test_refresh_uses_conditional_request(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        result, _manifest, fetcher = self.run_once(pages, files, refresh_days=0)
        self.assertEqual(dict(fetcher.file_requests)[SHEET_PDF].get("If-None-Match"), '"s1"')
        self.assertEqual(result.unchanged, 4)
        self.assertEqual(result.downloaded + result.updated, 0)

    def test_changed_file_is_replaced(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        files[SHEET_XLSX] = (make_xlsx([["事業名"], ["企画調整事業（改訂）"]]), files[SHEET_XLSX][1])
        result, manifest, _ = self.run_once(pages, files, refresh_days=0)
        self.assertEqual(result.updated, 1)
        entry = self.documents(manifest)[SHEET_XLSX]
        body = gzip.decompress((self.root / TARGET.slug / entry["text_file"]).read_bytes()).decode("utf-8")
        self.assertIn("企画調整事業（改訂）", body)

    def test_removed_document_is_marked_gone_and_kept(self) -> None:
        pages, files = site()
        _result, manifest, _ = self.run_once(pages, files)
        saved = self.documents(manifest)[REPORT_DOCX]["file"]
        pages[YEAR_PAGE] = pages[YEAR_PAGE].replace('<a href="r6/gaibu.docx">行政評価委員会の意見（ワード：20KB）</a>', "")
        result, manifest, _ = self.run_once(pages, files)
        entry = self.documents(manifest)[REPORT_DOCX]
        self.assertEqual(entry["status"], "gone")
        self.assertTrue(entry["missing_since"])
        self.assertTrue((self.root / TARGET.slug / saved).is_file())
        self.assertEqual(result.missing, 1)
        # 戻ってきたら印を外す。
        pages, _files = site()
        _result, manifest, _ = self.run_once(pages, files)
        self.assertEqual(self.documents(manifest)[REPORT_DOCX]["status"], "ok")

    def test_max_docs_leaves_the_rest_pending_for_the_next_run(self) -> None:
        pages, files = site()
        result, manifest, fetcher = self.run_once(pages, files, max_docs=2)
        self.assertEqual(len(fetcher.file_requests), 2)
        self.assertEqual(result.pending, 2)
        statuses = sorted(entry["status"] for entry in manifest["documents"].values())
        self.assertEqual(statuses, ["ok", "ok", "pending", "pending"])
        # 新しい年度から取る。令和5年度の ZIP は後回し。
        self.assertEqual(self.documents(manifest)[ARCHIVE]["status"], "pending")
        self.assertEqual(result.missing, 0)
        result, manifest, _ = self.run_once(pages, files, max_docs=2)
        self.assertEqual(result.downloaded, 2)
        self.assertTrue(all(entry["status"] == "ok" for entry in manifest["documents"].values()))

    def test_html_answer_is_not_saved(self) -> None:
        pages, files = site()
        files[SHEET_PDF] = (b"<!DOCTYPE html><html>not found</html>", {"Content-Type": "text/html"})
        result, manifest, _ = self.run_once(pages, files)
        entry = self.documents(manifest)[SHEET_PDF]
        self.assertNotIn("file", entry)
        self.assertEqual(entry["status"], "error")
        self.assertIn("評価表のファイルではない", entry["status_reason"])
        self.assertEqual(result.status, "partial")

    def test_temporary_outage_does_not_mark_documents_gone(self) -> None:
        pages, files = site()
        self.run_once(pages, files)
        result, manifest, _ = self.run_once({}, files)
        self.assertEqual(result.status, "entry_unreachable")
        self.assertEqual(result.missing, 0)
        self.assertTrue(all(entry["status"] == "ok" for entry in manifest["documents"].values()))

    def test_dry_run_writes_nothing(self) -> None:
        result, manifest, fetcher = self.run_once(*site(), dry_run=True)
        self.assertEqual(manifest, {})
        self.assertEqual(fetcher.file_requests, [])
        self.assertEqual(result.found, 4)
        self.assertTrue(any("総務課" in line for line in result.lines))
        self.assertFalse((self.root / TARGET.slug).exists())

    def test_html_tables_only(self) -> None:
        pages = {ENTRY: """<title>事務事業評価</title>
            <table><tr><th>事業名</th><th>事業費</th><th>評価</th><th>担当課</th></tr>
            <tr><td>防災訓練</td><td>100</td><td>A</td><td>防災課</td></tr></table>"""}
        result, _manifest, _ = self.run_once(pages, {}, dry_run=True)
        self.assertTrue(result.html_only)

    def test_entry_page_of_another_scheme_is_flagged(self) -> None:
        # 板橋区の台帳の入口は、指定管理者のモニタリング・評価結果の頁だった。
        pages = {ENTRY: """<title>モニタリング・評価結果（体育施設）</title>
            <a href="hyoukar6.pdf">令和6年度サービス水準評価結果 （PDF 345.6KB）</a>
            <a href="hyoukaseat.pdf">令和4年度指定管理者評価シート （PDF 232.1KB）</a>"""}
        result, _manifest, _ = self.run_once(pages, {}, dry_run=True)
        self.assertEqual(result.entry_excluded, "designated_manager")
        self.assertEqual(result.found, 0)
        self.assertEqual(result.excluded, {"designated_manager": 2})


class FileHostTest(unittest.TestCase):
    def test_object_storage_is_allowed_only_for_files(self) -> None:
        self.assertTrue(h.file_host_allowed("www.city.omachi.nagano.jp", "city.omachi.nagano.jp"))
        # 大町市はファイルをさくらのオブジェクトストレージに置く。
        self.assertTrue(h.file_host_allowed("www.city.omachi.nagano.jp", "x3.isk01.sakurastorage.jp"))
        self.assertFalse(h.file_host_allowed("www.city.omachi.nagano.jp", "www.city.matsumoto.nagano.jp"))
        self.assertFalse(h.file_host_allowed("www.city.omachi.nagano.jp", "evil-sakurastorage.jp"))


class HostGroupTest(unittest.TestCase):
    def test_same_host_goes_to_one_worker(self) -> None:
        targets = [
            h.Target("1", "a", "A", "https://www.city.a.lg.jp/x", "enabled"),
            h.Target("2", "b", "B", "https://city.a.lg.jp/y", "enabled"),
            h.Target("3", "c", "C", "https://www.town.c.lg.jp/z", "enabled"),
        ]
        groups = h.host_groups(targets)
        self.assertEqual(sorted(len(group) for group in groups), [1, 2])


if __name__ == "__main__":
    unittest.main()
