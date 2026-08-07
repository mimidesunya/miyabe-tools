import unittest
from unittest import mock

from tools.gijiroku.scrapers import gikai_pdf, static_kaigiroku_dir

FRAMESET = """
<html><head><title>令和８年第２回臨時会</title></head>
<frameset cols="300,*">
  <frame name="index" src="R0803R_index.html">
  <frame name="main" src="R080330.html">
  <noframes><body><p>フレームをサポートしているブラウザが必要です。</p></body></noframes>
</frameset></html>
"""

INDEX_FRAME = """
<html><body><a href="R080330.html">第 １ 号　３月３０日（月曜日）</a></body></html>
"""

BODY_FRAME = """
<html><head><title>令和８年第２回市議会臨時会議事日程（第１号）</title></head><body>
<p>令和８年３月３０日（月）午後２時３０分開会</p>
<p>議事日程 第１ 会議録署名議員の指名</p>
<p>〇出席議員（１５名）</p><p>〇欠席議員（なし）</p>
<p>開議 午後２時３０分</p><p>質疑</p><p>討論</p>
<p>%s</p>
<p><a href="shiryou.pdf">令和８年第２回臨時会 会議録</a></p>
</body></html>
""" % ("本文本文本文。" * 200)

PAGES = {
    "https://example.lg.jp/data/gikai/kaigiroku/index.html": FRAMESET,
    "https://example.lg.jp/data/gikai/kaigiroku/R0803R_index.html": INDEX_FRAME,
    "https://example.lg.jp/data/gikai/kaigiroku/R080330.html": BODY_FRAME,
}
START = "https://example.lg.jp/data/gikai/kaigiroku/index.html"


def fake_request_text(session, url, timeout_ms, *args, **kwargs):
    try:
        return PAGES[url]
    except KeyError:
        raise RuntimeError(f"404 {url}")


class GikaiPdfFrameTest(unittest.TestCase):
    def test_pdf_inside_frame_is_found(self) -> None:
        with mock.patch.object(gikai_pdf, "request_text", fake_request_text):
            items = gikai_pdf.crawl_pdf_items(
                object(), START, timeout_ms=1000, max_pages=10, max_depth=3
            )
        self.assertEqual([item.url for item in items],
                         ["https://example.lg.jp/data/gikai/kaigiroku/shiryou.pdf"])

    def test_frames_are_visited_before_other_links(self) -> None:
        # frame を末尾に積むとページ上限に阻まれて本文へ届かない。
        with mock.patch.object(gikai_pdf, "request_text", fake_request_text):
            items = gikai_pdf.crawl_pdf_items(
                object(), START, timeout_ms=1000, max_pages=3, max_depth=3
            )
        self.assertEqual(len(items), 1)


class StaticKaigirokuDirFrameTest(unittest.TestCase):
    def test_html_minutes_inside_frame_is_found(self) -> None:
        with mock.patch.object(static_kaigiroku_dir, "request_text", fake_request_text):
            items = static_kaigiroku_dir.discover_items(
                object(), START, 1000, None, max_pages=10, include_html_documents=True
            )
        html_items = [item for item in items if item.doc_type == "html"]
        self.assertEqual([item.url for item in html_items],
                         ["https://example.lg.jp/data/gikai/kaigiroku/R080330.html"])


if __name__ == "__main__":
    unittest.main()
