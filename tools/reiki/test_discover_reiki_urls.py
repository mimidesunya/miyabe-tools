"""例規集の入口探索が、死んだ入口を正しいと判定せず、RILG から入口を拾えること。

御前崎市は d1-law から legal-square へ移ったが、d1-law の opensearch は HTTP 200 で
「システムエラー」を返すので、URL の形だけで入口と認めていた。公式サイトには
例規集へのリンクが無く、探索では新しい入口を見つけられなかった（2026-09）。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discover_reiki_urls as d  # noqa: E402

D1_ERROR = """<html><head><title>エラー</title></head>
<body><h1>システムエラー</h1><p>システムエラーが発生しました。</p></body></html>"""

RILG_SAMPLE = """
<a name="22"></a>
<table><tr>
<td><a href="https://krr029.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf" target="_top">御前崎市</a></td>
<!--
<td><a href="https://ops-jg.d1-law.com/opensearch/SrFeF01/init?jctcd=8A8573C81D">御前崎市</a></td>
-->
<td>吉田町</td>
</tr></table>
<a name="29"></a>
<table><tr>
<td colspan="10"><a href="https://en3-jg.d1-law.com/kawakami/d1w_reiki/reiki.html">川上村</a></td>
<td colspan="10">東吉野村</td>
</tr></table>
"""


class ErrorPageTest(unittest.TestCase):
    def test_d1_law_system_error_is_an_error_page(self):
        self.assertTrue(d.looks_like_error_page(D1_ERROR))

    def test_a_reiki_page_is_not_an_error_page(self):
        self.assertFalse(d.looks_like_error_page("<title>黒滝村例規集（奈良県）</title>"))
        self.assertFalse(d.looks_like_error_page(""))

    def test_verify_rejects_opensearch_error(self):
        url = "https://ops-jg.d1-law.com/opensearch/SrFeF01/init?jctcd=8A8573C81D"
        with mock.patch.object(d, "fetch", return_value=(200, url, D1_ERROR)):
            _entry, ok, _evidence = d.verify_entry(None, "d1-law", url)
        self.assertEqual(ok, "ng")


class LegacyTlsTest(unittest.TestCase):
    def test_legal_square_uses_the_legacy_tls_session(self):
        """legal-square は OpenSSL 3 の既定の暗号設定では握手に失敗する。"""
        plain = object()
        chosen = d.session_for(plain, "https://krr029.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf")
        self.assertIsNot(chosen, plain)
        self.assertIsInstance(chosen.get_adapter("https://krr029.legal-square.com/"), d._LegacyTlsAdapter)

    def test_other_hosts_keep_the_given_session(self):
        plain = object()
        self.assertIs(d.session_for(plain, "https://www1.g-reiki.net/naha/reiki_menu.html"), plain)
        self.assertIs(d.session_for(plain, "https://legal-square.com.example.jp/"), plain)


class RilgTest(unittest.TestCase):
    def test_links_are_split_by_prefecture(self):
        sections = d.parse_rilg_links(RILG_SAMPLE)
        self.assertEqual(
            sections["22"]["御前崎市"],
            ["https://krr029.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf"],
        )
        self.assertIn("川上村", sections["29"])
        self.assertNotIn("川上村", sections["22"])

    def test_names_without_links_and_commented_links_are_skipped(self):
        sections = d.parse_rilg_links(RILG_SAMPLE)
        self.assertNotIn("吉田町", sections["22"])
        self.assertNotIn("東吉野村", sections["29"])
        self.assertEqual(len(sections["22"]["御前崎市"]), 1)

    def test_url_used_by_another_municipality_is_detected(self):
        """奈良県川上村の RILG の行は、長野県川上村の例規集を指している。"""
        url = "http://en3-jg.d1-law.com/kawakami/d1w_reiki/reiki.html"
        self.assertEqual(d.registered_elsewhere(url, "29452"), "20304")
        self.assertEqual(d.registered_elsewhere(url, "20304"), "")

    def test_discover_falls_back_to_rilg(self):
        legal = "https://krr029.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf"
        dead = "https://ops-jg.d1-law.com/opensearch/SrFeF01/init?jctcd=8A8573C81D"
        home = "https://www.city.omaezaki.shizuoka.jp/"
        pages = {
            dead: (200, dead, D1_ERROR),
            home: (200, home, "<html><body><a href='/kurashi/'>くらし</a></body></html>"),
            d.RILG_LINK_URL: (200, d.RILG_LINK_URL, RILG_SAMPLE),
            legal: (200, legal, "<title>Reiki-Base</title> HAS-Shohin"),
        }

        def fake_fetch(_session, url, referer=""):
            return pages.get(url, (404, url, ""))

        finding = d.Finding(slug="", code="22223", name="御前崎市", registered_url=dead, registered_system="d1-law")
        d._rilg_cache.clear()
        with mock.patch.object(d, "fetch", side_effect=fake_fetch), mock.patch.object(d, "registered_elsewhere", return_value=""):
            result = d.discover_one(None, finding, home, pause=0)
        d._rilg_cache.clear()
        self.assertEqual(result.detected_system, "legal-square")
        self.assertEqual(result.verified, "ok")
        self.assertEqual(result.entry_url, legal)
        self.assertIn("rilg", result.evidence)


class GuessedGReikiTest(unittest.TestCase):
    def test_urls_are_built_from_romaji(self):
        self.assertEqual(
            d.guessed_g_reiki_urls("44322"),
            [
                "https://www1.g-reiki.net/himeshima/reiki_menu.html",
                "https://www1.g-reiki.net/vill.himeshima/reiki_menu.html",
            ],
        )
        self.assertEqual(d.guessed_g_reiki_urls("21421")[1], "https://www1.g-reiki.net/town.kitagata/reiki_menu.html")
        self.assertEqual(d.guessed_g_reiki_urls("99999"), [])

    def _discover(self, pages, name):
        def fake_fetch(_session, url, referer=""):
            return pages.get(url, (404, url, ""))

        finding = d.Finding(slug="", code="44322", name=name, registered_url="", registered_system="")
        d._rilg_cache["sections"] = {}
        try:
            with mock.patch.object(d, "fetch", side_effect=fake_fetch), mock.patch.object(d, "registered_elsewhere", return_value=""):
                return d.discover_one(None, finding, "", pause=0)
        finally:
            d._rilg_cache.clear()

    def _pages(self, taikei_text):
        base = "https://www1.g-reiki.net/himeshima/"
        return {
            base + "reiki_taikei/taikei_default.html": (200, base, "<a href='r_taikei_01.html'>reiki_taikei</a>"),
            base + "reiki_taikei/r_taikei_01.html": (200, base, taikei_text),
        }

    def test_a_guess_is_taken_when_the_name_appears(self):
        result = self._discover(self._pages("姫島村公告式条例 reiki_honbun"), "姫島村")
        self.assertEqual(result.verified, "ok")
        self.assertEqual(result.entry_url, "https://www1.g-reiki.net/himeshima/reiki_menu.html")

    def test_a_guess_is_rejected_for_another_municipality(self):
        result = self._discover(self._pages("別の村公告式条例 reiki_honbun"), "姫島村")
        self.assertNotEqual(result.verified, "ok")


if __name__ == "__main__":
    unittest.main()
