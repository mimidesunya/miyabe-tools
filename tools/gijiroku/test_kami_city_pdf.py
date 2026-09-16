import unittest

from tools.gijiroku.scrapers import kami_city_pdf


class HtmlResponseGuardTest(unittest.TestCase):
    def test_html_response_is_accepted(self) -> None:
        self.assertTrue(
            kami_city_pdf.looks_like_html_response("text/html; charset=UTF-8", b"<html><body>\xe4\xbc\x9a\xe8\xad\xb0")
        )

    def test_missing_content_type_falls_back_to_body_check(self) -> None:
        self.assertTrue(kami_city_pdf.looks_like_html_response("", b"<html></html>"))

    def test_pdf_content_type_is_rejected(self) -> None:
        self.assertFalse(kami_city_pdf.looks_like_html_response("application/pdf", b"%PDF-1.4 ..."))

    def test_pdf_body_with_html_content_type_is_rejected(self) -> None:
        # Content-Type が text/html でも実体が PDF のサーバがある。
        self.assertFalse(kami_city_pdf.looks_like_html_response("text/html", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3"))

    def test_binary_body_is_rejected(self) -> None:
        self.assertFalse(kami_city_pdf.looks_like_html_response("text/html", b"<!\x00\x01\x02binary"))

    def test_xhtml_is_accepted(self) -> None:
        self.assertTrue(kami_city_pdf.looks_like_html_response("application/xhtml+xml", b"<?xml version=\"1.0\"?>"))


class NormalizePdfTextTest(unittest.TestCase):
    def test_lone_surrogates_are_removed(self) -> None:
        raw = "会議録" + chr(0xDB40) + chr(0xDC01) + "本文"
        with self.assertRaises(UnicodeEncodeError):
            raw.encode("utf-8")
        text = kami_city_pdf.normalize_pdf_text(raw)
        self.assertEqual(text, "会議録本文")
        # 保存時に UnicodeEncodeError にならないこと。
        self.assertEqual(text.encode("utf-8").decode("utf-8"), text)

    def test_normal_text_is_preserved(self) -> None:
        self.assertEqual(kami_city_pdf.normalize_pdf_text("定例会\r\n議事日程"), "定例会\n議事日程")

class AttachmentPdfLinkTest(unittest.TestCase):
    ASHX = "https://example.lg.jp/common/UploadFileOutput.ashx?c_id=3&id=9324&flid=15063"

    def test_minutes_attachment_is_detected(self) -> None:
        self.assertTrue(kami_city_pdf.looks_like_attachment_pdf(self.ASHX, "令和7年度 第1回町議会定例会（6月会議）"))

    def test_unrelated_attachment_is_ignored(self) -> None:
        # 会議録と関係ない添付まで PDF 扱いしない。
        self.assertFalse(kami_city_pdf.looks_like_attachment_pdf(self.ASHX, "広報紙 8月号"))

    def test_other_urls_are_ignored(self) -> None:
        self.assertFalse(kami_city_pdf.looks_like_attachment_pdf("https://example.lg.jp/page9324.html", "会議録"))


if __name__ == "__main__":
    unittest.main()


class QueryNamesAPdfTest(unittest.TestCase):
    def test_pdf_named_in_the_query_is_recognised(self) -> None:
        # 上天草市は /dl?q=…filelib_….pdf でファイルを配る。
        url = "https://www.city.kamiamakusa.kumamoto.jp/dl?q=83923_filelib_666f9d.pdf"
        self.assertTrue(kami_city_pdf.query_names_a_pdf(url))
        self.assertTrue(kami_city_pdf.looks_like_attachment_pdf(url, "令和5年11月30日 上天草市議会会議録"))

    def test_query_without_a_pdf_is_not_one(self) -> None:
        url = "https://example.lg.jp/dl?q=83923_filelib_666f9d.xlsx"
        self.assertFalse(kami_city_pdf.query_names_a_pdf(url))

    def test_unrelated_link_text_is_still_rejected(self) -> None:
        # 拡張子だけでは判断しない。会議録らしい文字列を伴うものに限る。
        url = "https://example.lg.jp/dl?q=1_filelib_x.pdf"
        self.assertFalse(kami_city_pdf.looks_like_attachment_pdf(url, "入札公告"))


class AnchorTextNamesAPdfTest(unittest.TestCase):
    def test_download_endpoint_with_a_pdf_label(self) -> None:
        # 上士幌町は /dl.php?up_code=… にファイル名を文字列で添える。
        url = "https://www.kamishihoro.jp/dl.php?up_code=11145"
        label = "令和6年第6回上士幌町議会定例会会議録.pdf"
        self.assertTrue(kami_city_pdf.anchor_text_names_a_pdf(url, label))
        self.assertTrue(kami_city_pdf.looks_like_attachment_pdf(url, label))

    def test_label_without_a_pdf_suffix_is_not_one(self) -> None:
        url = "https://www.kamishihoro.jp/dl.php?up_code=11145"
        self.assertFalse(kami_city_pdf.anchor_text_names_a_pdf(url, "会議録を開く"))

    def test_ordinary_page_link_is_untouched(self) -> None:
        self.assertFalse(
            kami_city_pdf.anchor_text_names_a_pdf("https://example.lg.jp/entry/1", "資料.pdf")
        )

class DownloadEndpointWithoutExtensionTest(unittest.TestCase):
    """拡張子を出さずディレクトリの形で PDF を配る取得元（仁淀川町）。"""

    NIYODO = "https://www.town.niyodogawa.lg.jp/download/?t=LD&id=3119&fid=21124"

    def test_minutes_link_with_a_pdf_annotation_is_detected(self) -> None:
        self.assertTrue(
            kami_city_pdf.looks_like_attachment_pdf(self.NIYODO, "令和７年 第２回定例会（初日）（PDF：653KB）")
        )

    def test_other_annotation_styles_are_detected(self) -> None:
        for label in (
            "令和8年第1回定例会会議録 [PDFファイル／1.2MB]",
            "第85回定例会 PDF(1192KB)",
            "令和5年第1回臨時会会議録（PDF形式：1.58MB）",
        ):
            self.assertTrue(kami_city_pdf.looks_like_attachment_pdf(self.NIYODO, label), label)

    def test_a_directory_without_a_query_is_not_an_endpoint(self) -> None:
        # ただの /download/ ページを PDF 扱いしない。
        self.assertFalse(
            kami_city_pdf.looks_like_attachment_pdf("https://example.lg.jp/download/", "会議録（PDF：399KB）")
        )

    def test_an_ordinary_page_link_is_not_an_endpoint(self) -> None:
        self.assertFalse(
            kami_city_pdf.looks_like_attachment_pdf("https://example.lg.jp/page9324.html", "会議録（PDF：399KB）")
        )

    def test_a_label_without_a_pdf_annotation_is_ignored(self) -> None:
        # 種別の注記が無ければ、配信口でも PDF とは決められない。
        self.assertFalse(kami_city_pdf.looks_like_attachment_pdf(self.NIYODO, "令和７年 第２回定例会（初日）"))

    def test_a_non_minutes_label_is_ignored(self) -> None:
        self.assertFalse(kami_city_pdf.looks_like_attachment_pdf(self.NIYODO, "広報紙 8月号（PDF：399KB）"))


class PdfResponseGuardTest(unittest.TestCase):
    def test_pdf_body_is_accepted(self) -> None:
        self.assertTrue(kami_city_pdf.looks_like_pdf_response("text/html", "", b"%PDF-1.4 ..."))

    def test_pdf_content_type_is_accepted(self) -> None:
        self.assertTrue(kami_city_pdf.looks_like_pdf_response("application/pdf", "", b"garbled"))

    def test_pdf_filename_in_content_disposition_is_accepted(self) -> None:
        self.assertTrue(
            kami_city_pdf.looks_like_pdf_response("application/octet-stream", 'attachment; filename="r7-1.pdf"', b"garbled")
        )

    def test_html_response_is_refused(self) -> None:
        # 配信口だと思って開いたら案内ページだった、を本文にしない。
        self.assertFalse(kami_city_pdf.looks_like_pdf_response("text/html; charset=UTF-8", "", b"<html><body>"))


class RequestPdfBytesTest(unittest.TestCase):
    class _Response:
        def __init__(self, content: bytes, headers: dict) -> None:
            self.content = content
            self.headers = headers

        def raise_for_status(self) -> None:
            return None

    class _Session:
        def __init__(self, response) -> None:
            self.response = response

        def get(self, _url, **_kwargs):
            return self.response

    def test_pdf_is_returned(self) -> None:
        session = self._Session(self._Response(b"%PDF-1.7 body", {"Content-Type": "application/pdf"}))
        self.assertEqual(kami_city_pdf.request_pdf_bytes(session, "https://example.lg.jp/download/?id=1", 10_000), b"%PDF-1.7 body")

    def test_html_is_refused(self) -> None:
        session = self._Session(self._Response("<html>案内</html>".encode("utf-8"), {"Content-Type": "text/html"}))
        with self.assertRaises(ValueError):
            kami_city_pdf.request_pdf_bytes(session, "https://example.lg.jp/download/?id=1", 10_000)

