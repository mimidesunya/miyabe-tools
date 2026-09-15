import unittest

from tools.gijiroku.scrapers import gijiroku_com


class LegacyVoicesListTest(unittest.TestCase):
    def test_parse_list_keeps_detail_url_and_pagination(self) -> None:
        raw_html = """
        <table>
          <tr><td>
            <a href="voiweb.exe?ACT=100&amp;FINO=978&amp;FINOS=978%2C979"><img></a>
            令和　７年１２月定例会,
            <a href="voiweb.exe?ACT=200&amp;KGNO=221&amp;FINO=978">11月28日-01号</a>
          </td></tr>
          <tr><td><a href="voiweb.exe?ACT=100&amp;PAGE=2&amp;HIT=983">2</a></td></tr>
        </table>
        """
        page_url = "https://example.test/VOICES/CGI/voiweb.exe?ACT=100"

        meetings, page_urls = gijiroku_com.parse_legacy_voices_list_page(raw_html, page_url)

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].year_label, "令和 ７年")
        self.assertIn("FINO=978", meetings[0].url)
        self.assertIn("令和 ７年１２月定例会", meetings[0].title)
        self.assertEqual(
            page_urls,
            ["https://example.test/VOICES/CGI/voiweb.exe?ACT=100&PAGE=2&HIT=983"],
        )

    def test_pagination_ignores_the_opened_meeting(self) -> None:
        # 伊東市: 会議の枝を開いた一覧では、ページ送りに会議名が付く。
        # 同じページが別 URL に見えて一覧の巡回が終わらなかった。
        raw_html = """
        <table>
          <tr><td><a href="voiweb.exe?ACT=100&amp;KENSAKU=0&amp;TITL_SUBT=%97%DF%98a%81%7C08%8C%8E30%93%FA&amp;PAGE=10&amp;HIT=943">10</a></td></tr>
          <tr><td><a href="voiweb.exe?ACT=100&amp;KENSAKU=0&amp;PAGE=10&amp;HIT=943">10</a></td></tr>
        </table>
        """
        page_url = "https://example.test/voices/CGI/voiweb.exe?ACT=100"

        _, page_urls = gijiroku_com.parse_legacy_voices_list_page(raw_html, page_url)

        self.assertEqual(
            page_urls,
            ["https://example.test/voices/CGI/voiweb.exe?ACT=100&KENSAKU=0&PAGE=10&HIT=943"],
        )

    def test_fallback_text_keeps_source_url(self) -> None:
        item = gijiroku_com.MeetingItem(
            title="令和7年12月定例会 11月28日-01号",
            url="https://example.test/VOICES/CGI/voiweb.exe?ACT=100&FINO=978",
            year_label="令和7年",
        )

        text = gijiroku_com.build_fallback_meeting_text(item, "本文")

        self.assertIn(f"Source URL: {item.url}", text)
        self.assertTrue(text.endswith("本文\n"))




class TrimGroupLabelTest(unittest.TestCase):
    def test_drops_date_suffix(self) -> None:
        # 会議種別と日付を続けて持つ取得元（八代市・伊東市など）。
        # 日付から先を残すと会議ごとに別々の種別になってしまう。
        self.assertEqual(
            gijiroku_com.trim_group_label("１２月定例会－11月28日-01号", "11月28日-01号"),
            "１２月定例会",
        )

    def test_keeps_plain_group_name(self) -> None:
        self.assertEqual(gijiroku_com.trim_group_label("厚生経済", "厚生経済"), "厚生経済")

if __name__ == "__main__":
    unittest.main()
