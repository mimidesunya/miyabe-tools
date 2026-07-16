import unittest
from types import SimpleNamespace

from tools.gijiroku.scrapers import msearch


class MsearchParserTest(unittest.TestCase):
    def test_static_index_and_minutes_link(self) -> None:
        raw_html = """
        <p><b>【 令和８年第１回定例会 】</b></p>
        <table><tr><td><a href="../kensaku/r0801t1.html">議事日程 第１</a></td><td>２月２６日</td></tr></table>
        <a href="../kensaku/r0801ti.html">目次</a>
        """
        meetings = msearch.parse_index(raw_html, "https://city.example/kensaku/mokuji.html")
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].held_on, "2026-02-26")
        self.assertEqual(meetings[0].url, "https://city.example/kensaku/r0801t1.html")

    def test_cgi_source_resolves_to_static_index(self) -> None:
        self.assertEqual(
            msearch.static_index_url("https://city.example/cgi-bin/kaigiroku/msearch.cgi"),
            "https://city.example/kensaku/mokuji.html",
        )

    def test_response_without_charset_uses_utf8_bytes(self) -> None:
        response = SimpleNamespace(content="令和８年".encode("utf-8"), apparent_encoding="ISO-8859-1")
        self.assertEqual(msearch.decode_response(response), "令和８年")


if __name__ == "__main__":
    unittest.main()
