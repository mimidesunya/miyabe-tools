import unittest

from tools.gijiroku.scrapers import amivoice


class AmiVoiceParserTest(unittest.TestCase):
    def test_period_list_and_next_cursor(self) -> None:
        raw_html = """
        <table><tr><td>2026/02/27</td><td>
          <a href="search.exe?vcsm=m1.vcsm&amp;process=list">令和８年第１回定例会</a>
        </td></tr></table>
        <input type="image" name="param[process:list_vcsm,cur_id:10]" alt="次の10件">
        """
        periods, cursor = amivoice.parse_period_list(raw_html, "https://example.test/usr/search.exe")
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0].held_on, "2026-02-27")
        self.assertEqual(cursor, 10)

    def test_meeting_list_builds_stable_source_and_fetch_urls(self) -> None:
        raw_html = """
        <table><tr><td>2026/02/27</td><td>
          <a href="#" onClick="DataSubmit4('search.exe?vcsv=v1.vcsv&amp;process=disp_base');">第１号</a>
        </td></tr></table>
        """
        period = amivoice.PeriodItem(
            title="令和８年第１回定例会",
            url="https://example.test/usr/search.exe?vcsm=m1.vcsm&process=list",
        )
        meetings = amivoice.parse_meeting_list(raw_html, period.url, period)
        self.assertEqual(len(meetings), 1)
        self.assertIn("process=disp_base", meetings[0].url)
        self.assertIn("process=disp_right", meetings[0].fetch_url)
        self.assertEqual(meetings[0].held_on, "2026-02-27")

    def test_minutes_body_and_source_header(self) -> None:
        body = amivoice.parse_minutes_body(
            '<div class="whitebag_right"><div class="NameArea">会議名</div><div class="sub2">本文</div></div>'
        )
        item = amivoice.MeetingItem(
            title="第１号",
            url="https://example.test/source",
            year_label="令和8年",
            held_on="2026-02-27",
        )
        text = amivoice.build_minutes_text(item, body)
        self.assertIn("本文", text)
        self.assertIn("Source URL: https://example.test/source", text)


if __name__ == "__main__":
    unittest.main()
