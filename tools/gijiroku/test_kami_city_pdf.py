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


if __name__ == "__main__":
    unittest.main()
