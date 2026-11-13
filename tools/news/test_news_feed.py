"""新着情報のフィードの読み取り。全国調査で見た形を小さく再現する。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.news.news_feed import FeedError, clean_text, parse_date, parse_feed  # noqa: E402

RSS1_NETCREW = """<?xml version="1.0" encoding="utf-8" ?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/" xml:lang="ja"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:nc="http://www.netcrew.co.jp/rss/1.0/"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<channel rdf:about="https://www.city.example.lg.jp/">
  <title>例市役所RSS（重要なお知らせ）</title>
  <link>https://www.city.example.lg.jp/</link>
  <dc:date>2026-11-05 00:00:00+09:00</dc:date>
</channel>
<item rdf:about="https://www.city.example.lg.jp/soshiki/19/10893.html">
  <title>119番通報の受付が切り替わります！</title>
  <link>https://www.city.example.lg.jp/soshiki/19/10893.html</link>
  <description></description>
  <dc:date>2026-11-05T00:00:00+09:00</dc:date>
  <dc:subject>消防本部・消防署</dc:subject>
  <nc:event_sdate>2026-12-01</nc:event_sdate>
  <nc:event_edate></nc:event_edate>
  <nc:category03>くらし・手続き</nc:category03>
  <nc:category01>消防</nc:category01>
</item>
</rdf:RDF>
"""

RSS2_SJIS = """<?xml version="1.0" encoding="Shift_JIS"?>
<rss version="2.0"><channel><title>例町 新着情報</title>
<item><title>議会だより &amp; お知らせ</title><link>/docs/2026/1.html</link>
<description>&lt;p&gt;本文の&lt;b&gt;一部&lt;/b&gt;です&lt;/p&gt;</description>
<pubDate>Thu, 12 Nov 2026 10:00:00 +0900</pubDate><category>議会</category></item>
<item><title>時差の書いていない日付</title><link>https://www.town.example.jp/b.html</link>
<pubDate>Thu, 12 Nov 2026 10:00:00 JST</pubDate></item>
</channel></rss>
""".encode("cp932")

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>注目情報 | 例市</title>
  <entry>
    <id>tag:www.city.example.jp,2026:/docs/2026022700054/</id>
    <title>市政アンケートの結果</title>
    <updated>2026-02-27T13:00:00+09:00</updated>
    <link rel="alternate" href="https://www.city.example.jp/docs/2026022700054/"/>
    <summary type="html"><![CDATA[<table><tr><td>第１回</td></tr></table>]]></summary>
  </entry>
</feed>
"""


class ParseFeedTest(unittest.TestCase):
    def test_rss1_with_netcrew_categories_and_event_dates(self) -> None:
        feed = parse_feed(RSS1_NETCREW.encode("utf-8"))
        self.assertEqual(feed.format, "rss1")
        self.assertEqual(feed.title, "例市役所RSS（重要なお知らせ）")
        self.assertEqual(len(feed.items), 1)
        item = feed.items[0]
        self.assertEqual(item.url, "https://www.city.example.lg.jp/soshiki/19/10893.html")
        self.assertEqual(item.date, "2026-11-05")
        self.assertEqual(item.published_at, "2026-11-05T00:00:00+09:00")
        self.assertEqual(item.categories, ["消防本部・消防署", "くらし・手続き", "消防"])
        self.assertEqual(item.event_start, "2026-12-01")
        self.assertEqual(item.event_end, "")
        self.assertEqual(feed.newest, "2026-11-05")

    def test_shift_jis_rss2_with_relative_links_and_html_description(self) -> None:
        feed = parse_feed(RSS2_SJIS, content_type="text/xml", base_url="https://www.town.example.jp/rss.xml")
        self.assertEqual(feed.format, "rss2")
        first, second = feed.items
        self.assertEqual(first.title, "議会だより & お知らせ")
        self.assertEqual(first.url, "https://www.town.example.jp/docs/2026/1.html")
        self.assertEqual(first.summary, "本文の 一部 です")
        self.assertEqual(first.categories, ["議会"])
        self.assertEqual(first.published_at, "2026-11-12T10:00:00+09:00")
        # JST のように読めない時差は日本時間と見なす。
        self.assertEqual(second.published_at, "2026-11-12T10:00:00+09:00")

    def test_atom_entry(self) -> None:
        feed = parse_feed(ATOM.encode("utf-8"))
        self.assertEqual(feed.format, "atom")
        item = feed.items[0]
        self.assertEqual(item.url, "https://www.city.example.jp/docs/2026022700054/")
        self.assertEqual(item.date, "2026-02-27")
        self.assertEqual(item.summary, "第１回")

    def test_bare_ampersand_and_html_entities_are_repaired(self) -> None:
        raw = (
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
            '<item><title>A & B&nbsp;C</title><link>https://a.example.jp/x?a=1&b=2</link></item>'
            "</channel></rss>"
        ).encode("utf-8")
        item = parse_feed(raw).items[0]
        self.assertEqual(item.title, "A & B C")
        self.assertEqual(item.url, "https://a.example.jp/x?a=1&b=2")

    def test_site_url_glued_to_an_external_url(self) -> None:
        raw = (
            '<rss version="2.0"><channel><item><title>t</title>'
            "<link>https://www.city.example.jphttps://www.meti.go.jp/a/index.htm</link></item>"
            '<item><title>u</title><link>https://www.city.example.jp/r?url=https://b.example.jp/</link></item>'
            "</channel></rss>"
        ).encode("utf-8")
        first, second = parse_feed(raw).items
        self.assertEqual(first.url, "https://www.meti.go.jp/a/index.htm")
        self.assertEqual(second.url, "https://www.city.example.jp/r?url=https://b.example.jp/")

    def test_html_page_is_not_a_feed(self) -> None:
        with self.assertRaises(FeedError):
            parse_feed(b"<!DOCTYPE html><html><body>rss</body></html>")

    def test_link_falls_back_to_guid(self) -> None:
        raw = (
            '<rss version="2.0"><channel><item><title>t</title>'
            "<guid>https://a.example.jp/p/1</guid><pubDate>2026-11-01</pubDate></item></channel></rss>"
        ).encode("utf-8")
        item = parse_feed(raw).items[0]
        self.assertEqual(item.url, "https://a.example.jp/p/1")
        self.assertEqual(item.date, "2026-11-01")

    def test_item_id_is_the_url_and_fingerprint_tracks_title_and_date(self) -> None:
        feed = parse_feed(RSS1_NETCREW.encode("utf-8"))
        item = feed.items[0]
        same_url = parse_feed(RSS1_NETCREW.replace("切り替わります！", "切り替わりました").encode("utf-8")).items[0]
        self.assertEqual(item.item_id(), same_url.item_id())
        self.assertNotEqual(item.fingerprint(), same_url.fingerprint())


class DateTest(unittest.TestCase):
    def test_formats_seen_in_the_survey(self) -> None:
        cases = {
            "2026-11-05T00:00:00+09:00": "2026-11-05T00:00:00+09:00",
            "2026-11-05 00:00:00+09:00": "2026-11-05T00:00:00+09:00",
            "Thu, 12 Nov 2026 01:00:00 GMT": "2026-11-12T10:00:00+09:00",
            "2026-11-11T15:00:00Z": "2026-11-12T00:00:00+09:00",
            "2026/11/05 09:30": "2026-11-05T09:30:00+09:00",
            "2026年11月5日": "2026-11-05T00:00:00+09:00",
            "tue, 10Nov202614:00:00 +09:00": "2026-11-10T14:00:00+09:00",
        }
        for raw, expected in cases.items():
            parsed = parse_date(raw)
            self.assertIsNotNone(parsed, raw)
            self.assertEqual(parsed.isoformat(), expected, raw)
        self.assertIsNone(parse_date("近日公開"))

    def test_clean_text_limits_length(self) -> None:
        self.assertEqual(clean_text("<p>あ　い</p>", 0), "あ い")
        self.assertEqual(clean_text("あいうえお", 3), "あいう…")


if __name__ == "__main__":
    unittest.main()
