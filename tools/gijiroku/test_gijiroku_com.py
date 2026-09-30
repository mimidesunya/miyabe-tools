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


OTA_ACT200 = (
    "https://www.gikai-ota-tokyo.jp/ota/cgi/voiweb.exe?ACT=200&KENSAKU=1&SORT=0&KTYP=0,1,2,3&KGTP=3"
    "&FYY=2010&TYY=2010&TITL=%91%8D%96%B1%8D%E0%90%AD&TITL_SUBT=%95%BD%90%AC%82Q%82Q%94N%81@%82T%8C%8E"
    "&KGNO=524&FINO=1108&HUID=109114&UNID=k_H22052531011"
)
NO_SPEECH_HTML = (
    "<HTML><HEAD><TITLE>Server Busy</TITLE></HEAD><BODY><CENTER><BR>"
    "該当する発言がありませんでした。<br>キーワードの見直しをして再度検索を行ってください。<BR>"
    "</CENTER></BODY></HTML>"
)


class Act203FallbackTest(unittest.TestCase):
    def test_body_url_is_built_when_container_has_no_speech(self) -> None:
        # 大田区 平成22年5月25日 総務財政委員会（FINO=1108）。ACT=200 が発言なしを
        # 返すが、同じ FINO・HUID を ACT=203 に渡すと本文が返る。
        url = gijiroku_com.act203_url_from_act200_page(OTA_ACT200, NO_SPEECH_HTML)

        self.assertTrue(url.startswith("https://www.gikai-ota-tokyo.jp/ota/cgi/voiweb.exe?ACT=203&"))
        self.assertIn("FINO=1108", url)
        self.assertIn("HUID=109114", url)
        self.assertIn("HATSUGENMODE=1", url)
        # Shift_JIS の百分率符号化は組み直さない。
        self.assertIn("TITL=%91%8D%96%B1%8D%E0%90%AD", url)
        self.assertNotIn("KGNO=", url)
        self.assertNotIn("UNID=", url)
        self.assertEqual(url.count("ACT="), 1)

    def test_frame_is_used_when_present(self) -> None:
        act200_html = '<FRAMESET><FRAME SRC="voiweb.exe?ACT=203&amp;FINO=1097&amp;HATSUGENMODE=0&amp;HUID=107913"></FRAMESET>'

        url = gijiroku_com.act203_url_from_act200_page(OTA_ACT200, act200_html)

        self.assertIn("ACT=203", url)
        self.assertIn("HATSUGENMODE=1", url)

    def test_unknown_page_gives_nothing(self) -> None:
        self.assertEqual(gijiroku_com.act203_url_from_act200_page(OTA_ACT200, "<html>busy</html>"), "")


class FakeResponse:
    def __init__(self, raw_html: str | None) -> None:
        self.ok = raw_html is not None
        self._body = (raw_html or "").encode("cp932")

    def body(self) -> bytes:
        return self._body


class FakeRequest:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, **_kwargs) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(self.pages.get(url))


BASE_URL = "https://example.gijiroku.com/voices/"
FIRST_URL = BASE_URL + "CGI/voiweb.exe?ACT=100&KTYP=2,3,0&KGTP=1,2,3,4&SORT=0"
YEAR_URL = BASE_URL + "CGI/voiweb.exe?ACT=100&KENSAKU=0&SORT=0&KTYP=2,3,0&KGTP=1,2,3,4&YEAR=2025"


def legacy_list_html(declared: int, fino_numbers: list[int], year_link: bool) -> str:
    rows = "".join(
        f'<tr><td><a href="voiweb.exe?ACT=100&amp;FINO={fino}&amp;YEAR=2026"><img></a>'
        f"令和　８年　３月定例会,03月0{index + 1}日-01号</td></tr>"
        for index, fino in enumerate(fino_numbers)
    )
    year = (
        '<a href="voiweb.exe?ACT=100&amp;KENSAKU=0&amp;SORT=0&amp;KTYP=2,3,0&amp;KGTP=1,2,3,4&amp;YEAR=2025">令和 07年</a>'
        if year_link
        else ""
    )
    return f"<p><b>{declared}</b>件の日程がヒットしました。</p>{year}<table>{rows}</table>"


class LegacyVoicesWalkTest(unittest.TestCase):
    def discover(self, pages: dict[str, str], coverage: dict) -> list:
        return gijiroku_com.discover_legacy_voices_meeting_items(
            FakeRequest(pages), BASE_URL, 1_000, 0, 0, coverage=coverage
        )

    def test_walked_to_the_end_is_complete(self) -> None:
        # 各務原市・調布市の形。最初の一覧が申告する件数を、年の絞り込みまで
        # 歩いて見つけ切った。
        pages = {
            FIRST_URL: legacy_list_html(3, [1, 2], year_link=True),
            YEAR_URL: legacy_list_html(1, [3], year_link=False),
        }
        coverage: dict = {}

        meetings = self.discover(pages, coverage)

        self.assertEqual(len(meetings), 3)
        self.assertEqual(coverage["missed_pages"], 0)
        self.assertEqual(coverage["declared_total"], 3)
        self.assertEqual(gijiroku_com.legacy_voices_walk_state(coverage, len(meetings)), "complete")

    def test_stale_missed_pages_are_overwritten(self) -> None:
        # 走査記録は前回の記録に重ねて保存される。今回開けたなら 0 を書く。
        pages = {
            FIRST_URL: legacy_list_html(3, [1, 2], year_link=True),
            YEAR_URL: legacy_list_html(1, [3], year_link=False),
        }
        coverage = {"missed_pages": 1, "missed_examples": [FIRST_URL]}

        self.discover(pages, coverage)

        self.assertEqual(coverage["missed_pages"], 0)
        self.assertEqual(coverage["missed_examples"], [])

    def test_unopened_list_is_partial_error(self) -> None:
        pages = {FIRST_URL: legacy_list_html(3, [1, 2], year_link=True)}
        coverage: dict = {}

        meetings = self.discover(pages, coverage)

        self.assertEqual(coverage["missed_pages"], 1)
        self.assertEqual(coverage["missed_examples"], [YEAR_URL])
        self.assertEqual(gijiroku_com.legacy_voices_walk_state(coverage, len(meetings)), "partial_error")

    def test_fewer_than_declared_is_partial_error(self) -> None:
        # 申告件数は完了の根拠にしないが、足りないことの検出には使う。
        pages = {FIRST_URL: legacy_list_html(5, [1, 2], year_link=False)}
        coverage: dict = {}

        meetings = self.discover(pages, coverage)

        self.assertEqual(gijiroku_com.legacy_voices_walk_state(coverage, len(meetings)), "partial_error")

    def test_unreadable_declared_total_does_not_block_completion(self) -> None:
        coverage = {"missed_pages": 0, "declared_total": 0, "limit_reached": False}

        self.assertEqual(gijiroku_com.legacy_voices_walk_state(coverage, 12), "complete")

    def test_missing_entry_is_not_complete(self) -> None:
        # 岩手県は voices の CGI が 404 を返す。起点を開けなければ 1 件も無い。
        coverage: dict = {}

        meetings = self.discover({}, coverage)

        self.assertEqual(meetings, [])
        self.assertEqual(coverage["missed_examples"], [FIRST_URL])
        self.assertEqual(gijiroku_com.legacy_voices_walk_state(coverage, 0), "partial_error")

    def test_limit_is_partial_limit(self) -> None:
        coverage = {"missed_pages": 0, "declared_total": 0, "limit_reached": True}

        self.assertEqual(gijiroku_com.legacy_voices_walk_state(coverage, 10), "partial_limit")


if __name__ == "__main__":
    unittest.main()
