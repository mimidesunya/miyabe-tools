import unittest
from unittest import mock

from tools.gijiroku.scrapers import gikai_pdf

# 河内町のように、会議録の一覧に「令和8年」だけを並べる取得元がある。
# リンク文字列にも URL にも会議録を示す語が無い。
ENTRY = """
<html><head><title>会議録 | 河内町公式ホームページ</title></head><body>
<a href="page003121.html">令和8年</a>
<a href="page002683.html">令和7年</a>
<a href="yosan.html">令和8年度予算</a>
</body></html>
"""

YEAR_2026 = """
<html><head><title>令和8年 | 河内町公式ホームページ</title></head><body>
<a href="r08_teirei1.pdf">令和8年第1回定例会 会議録</a>
</body></html>
"""

YEAR_2025 = """
<html><head><title>令和7年 | 河内町公式ホームページ</title></head><body>
<a href="r07_teirei1.pdf">令和7年第1回定例会 会議録</a>
</body></html>
"""

# 2 段目より深い年リンクは、通常どおり会議録らしさで判定する。
DEEPER = """
<html><head><title>令和8年 | 河内町公式ホームページ</title></head><body>
<a href="page009999.html">令和9年</a>
</body></html>
"""

BASE = "https://www.town.example.lg.jp/page/"
PAGES = {
    BASE + "dir000122.html": ENTRY,
    BASE + "page003121.html": YEAR_2026,
    BASE + "page002683.html": YEAR_2025,
    BASE + "yosan.html": "<html><head><title>予算</title></head><body></body></html>",
}


def fake_request_text(session, url, timeout_ms, *args, **kwargs):
    try:
        return PAGES[url]
    except KeyError:
        raise RuntimeError("404 " + url)


class YearOnlyLinkTest(unittest.TestCase):
    def test_year_links_under_a_minutes_index_are_followed(self) -> None:
        with mock.patch.object(gikai_pdf, "request_text", fake_request_text):
            items = gikai_pdf.crawl_pdf_items(
                object(), BASE + "dir000122.html", timeout_ms=1000, max_pages=10, max_depth=3
            )
        self.assertEqual(
            sorted(item.url for item in items),
            [BASE + "r07_teirei1.pdf", BASE + "r08_teirei1.pdf"],
        )

    def test_year_links_deeper_in_are_left_alone(self) -> None:
        # 入口の 1 段下までに絞る。深部で年リンクを追い続けると、
        # 会議録と関係ないページへ広がっていく。
        pages = dict(PAGES)
        pages[BASE + "page003121.html"] = DEEPER
        pages[BASE + "page009999.html"] = (
            "<html><head><title>令和9年</title></head><body>"
            "<a href='r09.pdf'>令和9年第1回定例会 会議録</a></body></html>"
        )
        with mock.patch.object(gikai_pdf, "request_text",
                               lambda s, u, t, *a, **k: pages[u]):
            items = gikai_pdf.crawl_pdf_items(
                object(), BASE + "dir000122.html", timeout_ms=1000, max_pages=10, max_depth=3
            )
        self.assertEqual([item.url for item in items], [BASE + "r07_teirei1.pdf"])

    def test_year_only_link_pattern(self) -> None:
        for text in ("令和8年", "令和 8 年", "平成31年度", "令和元年"):
            self.assertTrue(gikai_pdf.YEAR_ONLY_LINK.match(text), text)
        for text in ("令和8年度予算", "会議録", "令和8年第1回定例会"):
            self.assertFalse(gikai_pdf.YEAR_ONLY_LINK.match(text), text)

    def test_year_written_in_western_or_abbreviated_era(self) -> None:
        # 年の書き方は取得元でばらつく。元号だけを見ていたころは、
        # 岐南町の「2026年」と東峰村の「R8年度」で 1 件も見つけられなかった。
        for text in ("2026年", "2021年", "R8年度", "H30年度", "R.8年", "S60年度"):
            self.assertTrue(gikai_pdf.YEAR_ONLY_LINK.match(text), text)
        # 年だけを指す形に限る。緩めても他所のページへは出ない。
        for text in ("2026年度予算", "R8年度当初予算", "2026年の予定", "8年"):
            self.assertFalse(gikai_pdf.YEAR_ONLY_LINK.match(text), text)


if __name__ == "__main__":
    unittest.main()


class ExtensionlessPdfTest(unittest.TestCase):
    """拡張子の無い配信口の PDF を、開けなかったページではなく会議録として拾う。

    野辺地町（/download_file/view/6249/2834）・榛東村（/manage/contents/upload/…）は、
    会議録らしいリンクをページとして辿ると PDF が返り、「一覧を開けなかった」に
    数えられて「エラー停止」のままだった。
    """

    ENTRY = (
        "<html><head><title>会議録 | 例町</title></head><body>"
        "<a href='/download_file/view/6249/2834'>令和7年第3回定例会 会議録</a>"
        "<a href='/download_file/view/1/1'>令和7年度予算書</a>"
        "<a href='broken.html'>令和6年 会議録</a>"
        "</body></html>"
    )

    def fake(self, session, url, timeout_ms, *args, **kwargs):
        if url == BASE + "index.html":
            return self.ENTRY
        if "/download_file/view/" in url:
            raise gikai_pdf.NonHtmlResponse("not html", content_type="application/pdf", head=b"%PDF-1.7")
        raise RuntimeError("connection reset " + url)

    def test_a_pdf_behind_a_page_link_becomes_a_meeting(self) -> None:
        walk: dict = {}
        with mock.patch.object(gikai_pdf, "request_text", self.fake):
            items = gikai_pdf.crawl_pdf_items(
                object(), BASE + "index.html", timeout_ms=1000, max_pages=10, max_depth=3, walk=walk
            )
        self.assertEqual([item.url for item in items], ["https://www.town.example.lg.jp/download_file/view/6249/2834"])
        self.assertEqual(items[0].year_label, "令和7年")
        # 本当に開けなかったページだけが残る。
        self.assertEqual(walk["missed_pages"], 1)

    def test_a_non_html_response_that_is_not_a_pdf_is_still_missed(self) -> None:
        error = gikai_pdf.NonHtmlResponse("not html", content_type="application/zip", head=b"PK")
        self.assertFalse(error.is_pdf)
