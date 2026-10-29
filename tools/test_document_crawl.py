"""自治体の文書取得の共通部品（形式の見分け・Excel/Word の本文・年度）を確かめる。ネットワークには出ない。

取得器・ホストの休止・条件付き GET は、包括外部監査の取得のテスト
（`tools/kansa/test_scrape_kansa_reports.py`）でも通している。
"""

from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import document_crawl as dc  # noqa: E402


class Cp932ZipInfo(zipfile.ZipInfo):
    """日本語名を cp932 のまま入れる（UTF-8 の印なし）。Windows の古い圧縮の形。"""

    def _encodeFilenameFlags(self):  # noqa: N802 - zipfile の内部の名前に合わせる
        return self.filename.encode("cp932"), self.flag_bits


def make_zip(members: dict[str, str | bytes], *, cp932_names: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            data = body.encode("utf-8") if isinstance(body, str) else body
            archive.writestr(Cp932ZipInfo(name) if cp932_names else name, data)
    return buffer.getvalue()


def make_xlsx(rows: list[list[str]], sheet_name: str = "評価シート") -> bytes:
    """共有文字列を使う、いちばん小さい xlsx。"""
    strings: list[str] = []
    cells_xml = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if value.isdigit():
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        cells_xml.append(f'<row r="{r}">{"".join(cells)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    return make_zip({
        "[Content_Types].xml": "<Types/>",
        "xl/workbook.xml": f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                           f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{main}">'
                                + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{main}"><sheetData>{"".join(cells_xml)}</sheetData></worksheet>',
    })


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>"
            for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    return make_zip({
        "[Content_Types].xml": "<Types/>",
        "word/document.xml": f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>',
    })


class SniffTest(unittest.TestCase):
    def test_pdf_and_html(self) -> None:
        self.assertEqual(dc.sniff_document_type(b"%PDF-1.7 ..."), "pdf")
        self.assertIsNone(dc.sniff_document_type(b"<!DOCTYPE html><html>not found", "https://x/a.pdf"))
        self.assertIsNone(dc.sniff_document_type(b"\n  <html>", "https://x/a.csv", "text/csv"))

    def test_office_zip_is_told_apart_by_its_members(self) -> None:
        self.assertEqual(dc.sniff_document_type(make_xlsx([["事業名"]])), "xlsx")
        self.assertEqual(dc.sniff_document_type(make_docx(["評価"])), "docx")
        xlsm = make_zip({"[Content_Types].xml": "", "xl/workbook.xml": "", "xl/vbaProject.bin": b"x"})
        self.assertEqual(dc.sniff_document_type(xlsm), "xlsm")
        self.assertEqual(dc.sniff_document_type(make_zip({"r6/sheet01.pdf": b"%PDF-"})), "zip")

    def test_ole_needs_a_hint(self) -> None:
        ole = dc.OLE_MAGIC + b"\x00" * 100
        self.assertEqual(dc.sniff_document_type(ole, "https://x/a.xls"), "xls")
        self.assertEqual(dc.sniff_document_type(ole, "https://x/a.doc"), "doc")
        self.assertEqual(dc.sniff_document_type(ole, "https://x/dl?id=1", "application/msword"), "doc")
        self.assertEqual(dc.sniff_document_type(ole, "https://x/dl?id=1", "", 'attachment; filename="r6.doc"'), "doc")

    def test_csv(self) -> None:
        self.assertEqual(dc.sniff_document_type("事業名,評価\n".encode("cp932"), "https://x/list.csv"), "csv")
        self.assertIsNone(dc.sniff_document_type(b"plain", "https://x/list.txt"))


class ExtractTest(unittest.TestCase):
    def test_xlsx_rows_are_tab_separated_per_sheet(self) -> None:
        raw = make_xlsx([["事業名", "決算額", "今後の方向"], ["防災訓練事業", "1200", "継続"]])
        body, sheets = dc.extract_xlsx_text(raw)
        self.assertEqual(sheets, 1)
        self.assertIn("## 評価シート", body)
        self.assertIn("事業名\t決算額\t今後の方向", body)
        self.assertIn("防災訓練事業\t1200\t継続", body)

    def test_docx_paragraphs_and_tables(self) -> None:
        body = dc.extract_docx_text(make_docx(["令和6年度 事務事業評価"], [["事業名", "評価"], ["広報事業", "B"]]))
        self.assertIn("令和6年度 事務事業評価", body)
        self.assertIn("事業名\t評価", body)
        self.assertIn("広報事業\tB", body)

    def test_broken_files_give_empty_text(self) -> None:
        self.assertEqual(dc.extract_xlsx_text(b"PK\x03\x04broken"), ("", 0))
        self.assertEqual(dc.extract_docx_text(b"not a zip"), "")

    def test_pdf_with_a_broken_page_tree_is_unreadable_not_fatal(self) -> None:
        # 頁を辿る所で例外になる PDF（岡崎市・橿原市）。自治体ごと止めない。
        class BrokenReader:
            def __init__(self, _stream) -> None:
                pass

            @property
            def pages(self):
                raise RecursionError("Maximum page tree depth reached")

        from unittest import mock

        with mock.patch("pypdf.PdfReader", BrokenReader):
            self.assertEqual(dc.extract_pdf_text(b"%PDF-1.4 broken"), ("", 0))

    def test_csv_in_shift_jis(self) -> None:
        self.assertEqual(dc.decode_csv("事業名,評価\r\n".encode("cp932")), "事業名,評価")

    def test_zip_member_names_read_cp932(self) -> None:
        raw = make_zip({"令和6年度/評価シート.pdf": b"%PDF-"}, cp932_names=True)
        self.assertEqual(dc.zip_member_names(raw), ["令和6年度/評価シート.pdf"])


class PageLinksTest(unittest.TestCase):
    def test_base_href_decides_relative_links(self) -> None:
        # 京都市の形。ページの URL ではなく <base href> を基準に解く。
        html = """<html><head><base href="https://www.city.kyoto.lg.jp/sogo/" /></head><body>
            <h2>評価結果</h2><a href="./cmsfiles/contents/0000317/317431/R5hyokahyo1.pdf">評価票（PDF）</a>
            <a href="https://other.example.jp/a.pdf">外部</a></body></html>"""
        links = dc.page_links("https://www.city.kyoto.lg.jp/sogo/page/0000317431.html", html)
        self.assertEqual(links[0].url, "https://www.city.kyoto.lg.jp/sogo/cmsfiles/contents/0000317/317431/R5hyokahyo1.pdf")
        self.assertEqual(links[0].heading, "評価結果")
        self.assertEqual(links[1].url, "https://other.example.jp/a.pdf")

    def test_without_base_the_page_url_is_used(self) -> None:
        links = dc.page_links("https://x.example.jp/a/b/page.html", '<a href="c.pdf">c</a>')
        self.assertEqual(links[0].url, "https://x.example.jp/a/b/c.pdf")


class FiscalYearSourceTest(unittest.TestCase):
    def test_reports_which_text_it_was_read_from(self) -> None:
        self.assertEqual(dc.fiscal_year_source("報告書", "令和5年度"), (2023, "令和5年度", 1))
        self.assertEqual(dc.fiscal_year_source("報告書"), (None, "", -1))
        # 「年度」を先に探す。公表日の「令和8年」は後回し。
        self.assertEqual(dc.fiscal_year_source("公表（令和8年2月2日）", "令和6年度")[0], 2024)
        self.assertEqual(dc.fiscal_year("令和元年度"), (2019, "令和元年度"))


if __name__ == "__main__":
    unittest.main()
