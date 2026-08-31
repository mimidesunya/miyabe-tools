"""取得元の製品を印で見分ける。

`独自` は「まだ見ていない」の意味で使われてきた。石川県・福井県・おいらせ町は
legal-square だったのに、登録が案内ページを指していたので `独自` のままだった。
登録の系統名を信じず、取得元を開いて見分ける。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detect_system import detect  # noqa: E402


class DetectTest(unittest.TestCase):
    def test_legal_square(self):
        html = '<a href="https://kri101.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf">石川県法規集</a>'
        self.assertEqual(detect(html), "legal-square")

    def test_d1_law(self):
        self.assertEqual(detect("Reiki-Base インターネット版 牛久市 例規集"), "d1-law")

    def test_g_reiki(self):
        self.assertEqual(detect('<a href="https://www1.g-reiki.net/town.kushiro/reiki_menu.html">x</a>'), "g-reiki")

    def test_legalcrud(self):
        self.assertEqual(detect('<a href="https://public2.legalcrud.com/izumo_city/">x</a>'), "legalcrud")

    def test_an_ordinary_page_is_unknown(self):
        self.assertEqual(detect("<html><body>広報いずも</body></html>"), "")

    def test_a_failed_fetch_is_not_a_product(self):
        """取れなかったことを製品名にしない。"""
        self.assertEqual(detect("__HTTP_404__"), "")
        self.assertEqual(detect("__ERR_RemoteDisconnected__"), "")

    def test_legal_square_wins_over_a_weaker_marker(self):
        """印は前にあるものほど強い。案内ページに複数の名前が出ることがある。"""
        html = "legal-square.com と jourei の両方が出るページ"
        self.assertEqual(detect(html), "legal-square")
