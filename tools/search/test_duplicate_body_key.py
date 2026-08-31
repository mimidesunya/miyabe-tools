"""同じ文書を二度載せない。

南国市の議決一覧は `fd_17file` と `fd_21file` の下に同じ `downfile105294.pdf`
があり、本文は同じで `出典:` 行だけが違う。その行を含めて比べていたので、
別物として二つとも索引に載っていた。取得元の住所は文書の中身ではない。

無作為 50 自治体・16,553 ファイルのうち 56 件（0.34%）が同じ本文だった。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_opensearch_index import body_without_source_header  # noqa: E402


class BodyWithoutSourceHeaderTest(unittest.TestCase):
    def test_the_same_pdf_under_two_paths_matches(self):
        first = "令和1年\n議決一覧\n出典: https://example.jp/fd_17file/x.pdf\n本文\n"
        second = "令和1年\n議決一覧\n出典: https://example.jp/fd_21file/x.pdf\n本文\n"
        self.assertEqual(
            body_without_source_header(first), body_without_source_header(second)
        )

    def test_a_different_body_still_differs(self):
        first = "令和1年\n議決一覧\n出典: https://example.jp/a.pdf\n本文A\n"
        second = "令和1年\n議決一覧\n出典: https://example.jp/a.pdf\n本文B\n"
        self.assertNotEqual(
            body_without_source_header(first), body_without_source_header(second)
        )

    def test_a_different_title_still_differs(self):
        first = "令和1年\n議決一覧\n出典: https://example.jp/a.pdf\n本文\n"
        second = "令和1年\n会議録\n出典: https://example.jp/a.pdf\n本文\n"
        self.assertNotEqual(
            body_without_source_header(first), body_without_source_header(second)
        )

    def test_text_without_a_source_line_is_untouched(self):
        body = "令和1年\n議決一覧\n本文\n"
        self.assertEqual(body_without_source_header(body), body)

    def test_a_url_inside_the_body_is_kept(self):
        """本文中の URL は文書の一部である。落とすのは頭の `出典:` 行だけ。"""
        body = "令和1年\n議決一覧\n詳しくは https://example.jp/a.pdf を参照\n"
        self.assertIn("https://example.jp/a.pdf", body_without_source_header(body))
