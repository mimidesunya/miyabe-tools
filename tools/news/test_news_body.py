"""リンク先のページから本文を抜き出す。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.news.news_body import decode_html, extract_page  # noqa: E402

BASE = "https://www.city.example.jp/kurashi/1234.html"
FILLER = "市民のみなさまへのお知らせです。" * 8


def page(body: str, head: str = "<title>お知らせ / 例市</title>") -> bytes:
    return f'<!DOCTYPE html><html><head><meta charset="utf-8">{head}</head><body>{body}</body></html>'.encode("utf-8")


class ExtractTest(unittest.TestCase):
    def test_article_is_used_and_menus_are_dropped(self) -> None:
        raw = page(
            "<header>例市ホームページ</header><nav><a href='/'>くらし</a></nav>"
            "<div id='pankuzu'>トップ &gt; くらし</div>"
            f"<article><h1>ごみの収集日</h1><p>{FILLER}</p><p>二段落目</p></article>"
            "<footer>例市役所</footer>"
        )
        body = extract_page(raw, "text/html", BASE)
        self.assertEqual(body.method, "article")
        self.assertEqual(body.title, "お知らせ / 例市")
        self.assertTrue(body.text.startswith("ごみの収集日\n市民のみなさま"))
        self.assertIn("\n二段落目", body.text)
        for noise in ("例市ホームページ", "くらし", "トップ", "例市役所"):
            self.assertNotIn(noise, body.text)

    def test_short_article_falls_through_to_the_next_place(self) -> None:
        raw = page(f"<article>共有する</article><div id='main'><p>{FILLER}</p></div>")
        self.assertEqual(extract_page(raw, "", BASE).method, "#main")

    def test_without_known_places_it_descends_to_where_the_text_is(self) -> None:
        menu = "".join(f"<li><a href='/m{n}'>メニュー{n}</a></li>" for n in range(30))
        raw = page(f"<div id='wrap'><ul class='menu'>{menu}</ul>"
                   f"<div class='mol_contents'><p>{FILLER}</p><p>{FILLER}</p></div></div>")
        body = extract_page(raw, "", BASE)
        self.assertEqual(body.method, "descend")
        self.assertNotIn("メニュー", body.text)
        self.assertTrue(body.text.startswith("市民のみなさま"))

    def test_attachments_are_absolute_and_unique(self) -> None:
        raw = page(f"<main><p>{FILLER}</p>"
                   "<a href='files/a.pdf'>チラシ（PDF：120KB）</a> <a href='files/a.pdf'>同じ</a>"
                   "<a href='/b.xlsx?v=2'>名簿</a> <a href='/c.html'>ページ</a> <a href='javascript:void(0)'>x</a></main>")
        body = extract_page(raw, "", BASE)
        self.assertEqual(body.attachments, [
            {"url": "https://www.city.example.jp/kurashi/files/a.pdf", "text": "チラシ（PDF：120KB）"},
            {"url": "https://www.city.example.jp/b.xlsx?v=2", "text": "名簿"},
        ])

    def test_shift_jis_page_with_xml_declaration(self) -> None:
        raw = ('<?xml version="1.0" encoding="Shift_JIS"?><html><head>'
               '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS"><title>入札</title></head>'
               f'<body><div id="contents"><p>{FILLER}</p></div></body></html>').encode("cp932")
        body = extract_page(raw, "text/html", BASE)
        self.assertEqual(body.title, "入札")
        self.assertIn("お知らせです", body.text)

    def test_header_charset_wins_over_guessing(self) -> None:
        self.assertEqual(decode_html("ごみ".encode("euc_jp"), "text/html; charset=EUC-JP"), "ごみ")

    def test_empty_page_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            extract_page(b"   ", "text/html", BASE)


if __name__ == "__main__":
    unittest.main()
