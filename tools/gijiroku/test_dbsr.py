import unittest
from pathlib import Path
from unittest import mock

from tools.gijiroku.scrapers import dbsr


class FullPeriodListUrlTest(unittest.TestCase):
    def test_url_covers_whole_period_without_cabinet(self) -> None:
        url = dbsr.full_period_list_url("https://www.city.akiruno.tokyo.dbsr.jp/index.php/", "1995", "2026")
        self.assertEqual(
            url,
            "https://www.city.akiruno.tokyo.dbsr.jp/index.php/100000"
            "?Template=list&ListOrder=Asc&QueryType=New&TermStart=1995-01-01&TermEnd=2026-12-31",
        )

    def test_source_url_with_trailing_page_is_reduced_to_index_root(self) -> None:
        url = dbsr.full_period_list_url("https://example.dbsr.jp/index.php/100000?Template=list", "2000", "2001")
        self.assertTrue(url.startswith("https://example.dbsr.jp/index.php/100000?"))
        self.assertIn("TermStart=2000-01-01", url)
        self.assertIn("TermEnd=2001-12-31", url)


class EraYearLabelTest(unittest.TestCase):
    def test_heisei(self) -> None:
        self.assertEqual(dbsr.era_year_label("1997-12-18"), "平成9年")

    def test_reiwa(self) -> None:
        self.assertEqual(dbsr.era_year_label("2026-03-18"), "令和8年")

    def test_boundary_day_of_reiwa(self) -> None:
        self.assertEqual(dbsr.era_year_label("2019-05-01"), "令和1年")
        self.assertEqual(dbsr.era_year_label("2019-04-30"), "平成31年")

    def test_unknown_for_missing_date(self) -> None:
        self.assertEqual(dbsr.era_year_label(None), "不明")


class MeetingNameFromDocumentTitleTest(unittest.TestCase):
    def test_name_before_parenthesis(self) -> None:
        self.assertEqual(
            dbsr.meeting_name_from_document_title("平成９年第４回定例会（第５日目）  　議事日程・名簿"),
            "平成９年第４回定例会（第５日目）",
        )

    def test_name_without_parenthesis(self) -> None:
        self.assertEqual(dbsr.meeting_name_from_document_title("令和８年第１回臨時会議 本文"), "令和８年第１回臨時会議")


class MeetingGroupFromMeetingNameTest(unittest.TestCase):
    def test_strips_year_session_and_day_number(self) -> None:
        self.assertEqual(dbsr.meeting_group_from_meeting_name("令和８年第１回定例会（第７号）"), "定例会")
        self.assertEqual(dbsr.meeting_group_from_meeting_name("令和８年第１回定例会［ 署名 ］"), "定例会")
        self.assertEqual(dbsr.meeting_group_from_meeting_name("平成17年第１回臨時会 目次"), "臨時会")
        self.assertEqual(dbsr.meeting_group_from_meeting_name("平成30年第2回総務委員会"), "総務委員会")

    def test_keeps_name_without_year_or_number(self) -> None:
        self.assertEqual(
            dbsr.meeting_group_from_meeting_name("総務企画地域振興委員会"),
            "総務企画地域振興委員会",
        )

    def test_keeps_original_when_everything_would_be_stripped(self) -> None:
        self.assertEqual(dbsr.meeting_group_from_meeting_name("本文"), "本文")


class BuildFullPeriodDayGroupsTest(unittest.TestCase):
    def test_same_day_different_meetings_are_not_merged(self) -> None:
        rows = [
            dbsr.DocumentRow(title="平成９年第４回定例会（第５日目）　本文", url="u1", held_on="1997-12-18"),
            dbsr.DocumentRow(title="平成９年第４回定例会（第５日目）　名簿", url="u2", held_on="1997-12-18"),
            dbsr.DocumentRow(title="平成９年福祉委員会　本文", url="u3", held_on="1997-12-18"),
        ]
        groups = dbsr.build_full_period_day_groups("list", rows)
        self.assertEqual(len(groups), 2)
        # 会議のまとめ方は表題ごとだが、meeting_group には種別だけを残す。
        # 年や回次を残すと会議ごとに別々の種別になり、種別で絞り込めなくなる。
        self.assertEqual(groups[0].meeting_group, "定例会")
        self.assertEqual(groups[0].doc_urls, ["u1"])  # 本文がある日は本文だけを採る
        self.assertEqual(groups[1].meeting_group, "福祉委員会")
        self.assertEqual(groups[1].doc_urls, ["u3"])
        for group in groups:
            self.assertEqual(group.year_label, "平成9年")
            self.assertEqual(group.held_on, "1997-12-18")

    def test_documents_without_body_keep_every_row(self) -> None:
        rows = [
            dbsr.DocumentRow(title="平成10年第1回定例会（第1日目）　議事日程・名簿", url="u1", held_on="1998-03-04"),
            dbsr.DocumentRow(title="平成10年第1回定例会（第1日目）　〔資料〕", url="u2", held_on="1998-03-04"),
        ]
        groups = dbsr.build_full_period_day_groups("list", rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].doc_urls, ["u1", "u2"])



class FullTextDownloadUrlTest(unittest.TestCase):
    def test_doc_one_frame_is_converted(self) -> None:
        url = dbsr.full_text_download_url(
            "https://example.dbsr.jp/index.php/3855602?Template=doc-one-frame&VoiceType=onehit&DocumentID=1140"
        )
        self.assertEqual(
            url,
            "https://example.dbsr.jp/index.php/3855602"
            "?Template=download&Download=yes&VoiceType=all&DocumentID=1140",
        )

    def test_other_templates_are_untouched(self) -> None:
        self.assertEqual(dbsr.full_text_download_url("https://example.dbsr.jp/index.php/1?Template=view&Id=9"), "")

    def test_missing_document_id_is_untouched(self) -> None:
        self.assertEqual(
            dbsr.full_text_download_url("https://example.dbsr.jp/index.php/1?Template=doc-one-frame"), ""
        )
    def test_uppercase_template_is_converted(self) -> None:
        # Template 名の大文字小文字は取得元によって違う。
        url = dbsr.full_text_download_url(
            "https://example.dbsr.jp/index.php/1?Template=Doc-One-Frame&DocumentID=7"
        )
        self.assertEqual(
            url,
            "https://example.dbsr.jp/index.php/1?Template=download&Download=yes&VoiceType=all&DocumentID=7",
        )




class RecentOnlyListLinksTest(unittest.TestCase):
    def _items(self, urls):
        return {
            url: dbsr.ListPage(
                title="", year_label="", url=url, meeting_group="", auxiliary_docs=[]
            )
            for url in urls
        }

    def test_links_limited_to_two_years_are_recent_only(self) -> None:
        # 福岡県は直近 2 年分の期間つきリンクしか並べない。
        items = self._items([
            "https://example.dbsr.jp/index.php/1?Template=list&TermStart=2026-01-01&TermEnd=2026-12-31",
            "https://example.dbsr.jp/index.php/2?Template=list&TermStart=2025-02-04&TermEnd=2025-03-25",
        ])
        self.assertTrue(dbsr.list_links_cover_recent_years_only(items))

    def test_links_spanning_many_years_are_not_recent_only(self) -> None:
        items = self._items([
            "https://example.dbsr.jp/index.php/1?Template=list&TermStartYear=1998&TermEndYear=1998",
            "https://example.dbsr.jp/index.php/2?Template=list&TermStartYear=2026&TermEndYear=2026",
        ])
        self.assertFalse(dbsr.list_links_cover_recent_years_only(items))

    def test_links_without_a_period_are_left_alone(self) -> None:
        # 期間を持たないリンクが混ざると全期間かどうかは判断できない。
        items = self._items([
            "https://example.dbsr.jp/index.php/1?Template=list&TermStart=2026-01-01&TermEnd=2026-12-31",
            "https://example.dbsr.jp/index.php/2?Template=list&CabinetName=t",
        ])
        self.assertFalse(dbsr.list_links_cover_recent_years_only(items))


class KeywordListUrlTest(unittest.TestCase):
    def test_plain_phrase_is_a_keyword_list(self) -> None:
        self.assertTrue(
            dbsr.is_keyword_list_url(
                "https://example.dbsr.jp/index.php/1?Template=list&Phrase=%E8%A6%B3%E5%85%89&QueryType=New"
            )
        )

    def test_bracketed_phrase_is_a_keyword_list(self) -> None:
        # 荒尾市は Phrase[]= で渡す。正規化すると Phrase%5B%5D= になり、
        # 「phrase=」の文字列照合をすり抜けて検索語ごとに会議が複製されていた。
        url = dbsr.canonicalize_template_url(
            "https://www.city.arao.kumamoto.dbsr.jp/index.php/5260491"
            "?QueryType=New&Template=list&Phrase[]=%E4%B8%8B%E6%B0%B4%E9%81%93"
        )
        self.assertIn("Phrase%5B%5D=", url)
        self.assertTrue(dbsr.is_keyword_list_url(url))

    def test_session_list_is_not_a_keyword_list(self) -> None:
        self.assertFalse(
            dbsr.is_keyword_list_url(
                "https://example.dbsr.jp/index.php/1?Template=list&Cabinet=1&TermStart=2026-06-04&TermEnd=2026-06-23"
            )
        )


class FakeListPage:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def goto(self, url: str, **_kwargs) -> None:
        self.visited.append(url)

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None


class RecentOrWidenedTest(unittest.TestCase):
    SOURCE_URL = "https://www.city.example.dbsr.jp/index.php/"

    def setUp(self) -> None:
        self.original_extract = dbsr.extract_document_rows_from_page

    def tearDown(self) -> None:
        dbsr.extract_document_rows_from_page = self.original_extract

    def _items(self, urls):
        return {
            url: dbsr.ListPage(title="", year_label="", url=url, meeting_group="", auxiliary_docs=[])
            for url in urls
        }

    def _source_starts(self, held_on: str | None) -> None:
        rows = [] if held_on is None else [dbsr.DocumentRow(title="平成18年第１回臨時会 本文", url="u", held_on=held_on)]
        dbsr.extract_document_rows_from_page = lambda _page: rows

    def _session_links(self, years):
        return self._items(
            [
                f"https://www.city.example.dbsr.jp/index.php/1?Template=list&Cabinet=1&TermStart={year}-06-01&TermEnd={year}-06-20"
                for year in years
            ]
        )

    def test_recent_window_spanning_five_calendar_years_is_widened(self) -> None:
        # 宮若市の入口は 2022〜2026 年の会期だけを並べる。年の幅では直近分と
        # 判断できないが、取得元の全期間一覧は 2006 年から始まる。
        self._source_starts("2006-03-29")
        page = FakeListPage()

        pages, source = dbsr.recent_or_widened(
            self._session_links(range(2022, 2027)), page, self.SOURCE_URL, 1_000
        )

        self.assertEqual(source, dbsr.DISCOVERY_SOURCE_FULL_PERIOD)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].url, page.visited[0])
        self.assertIn("TermStart=1970-01-01", pages[0].url)
        self.assertIn("ListOrder=Asc", pages[0].url)

    def test_links_reaching_the_oldest_document_are_the_whole_library(self) -> None:
        # 生駒市の入口は 2006〜2026 年を並べ、取得元の最古も 2006 年。
        # 全部取れているのに「直近分しか出していない」と出していた。
        self._source_starts("2006-02-23")
        items = self._session_links(range(2006, 2027))

        pages, source = dbsr.recent_or_widened(items, FakeListPage(), self.SOURCE_URL, 1_000)

        self.assertEqual(source, dbsr.DISCOVERY_SOURCE_LIBRARY)
        self.assertEqual([page.url for page in pages], list(items))

    def test_year_links_mixed_with_whole_period_links_are_the_whole_library(self) -> None:
        # 千代田区は会議種別ごとの全期間リンクと、1974 年からの年別リンクを並べる。
        self._source_starts("1974-02-20")
        items = self._items(
            [
                "https://www.city.example.dbsr.jp/index.php/100000?Template=list&Cabinet=1&ListOrder=asc&QueryType=new",
                "https://www.city.example.dbsr.jp/index.php/100000?Template=list&Cabinet=1&ListOrder=asc&QueryType=new&TermEndYear=1974&TermStartYear=1974",
                "https://www.city.example.dbsr.jp/index.php/100000?Template=list&Cabinet=1&ListOrder=asc&QueryType=new&TermEndYear=2026&TermStartYear=2026",
            ]
        )

        _pages, source = dbsr.recent_or_widened(items, FakeListPage(), self.SOURCE_URL, 1_000)

        self.assertEqual(source, dbsr.DISCOVERY_SOURCE_LIBRARY)

    def test_unreadable_full_period_list_stays_recent(self) -> None:
        # 取得元の最古が確かめられないなら、従来どおり直近分として扱う。
        self._source_starts(None)
        items = self._session_links(range(2022, 2027))

        pages, source = dbsr.recent_or_widened(items, FakeListPage(), self.SOURCE_URL, 1_000)

        self.assertEqual(source, dbsr.DISCOVERY_SOURCE_RECENT)
        self.assertEqual([page.url for page in pages], list(items))

    def test_without_page_behaves_as_before(self) -> None:
        items = self._session_links(range(2022, 2027))

        _pages, source = dbsr.recent_or_widened(items)

        self.assertEqual(source, dbsr.DISCOVERY_SOURCE_RECENT)

    def test_narrow_links_are_widened_without_probing(self) -> None:
        # 荒尾市（キーワード一覧を除くと 2025〜2026 年の会期だけ）。
        page = FakeListPage()

        pages, source = dbsr.recent_or_widened(self._session_links([2025, 2026]), page, self.SOURCE_URL, 1_000)

        self.assertEqual(source, dbsr.DISCOVERY_SOURCE_FULL_PERIOD)
        self.assertEqual(page.visited, [])
        self.assertTrue(all("TermStart=1970-01-01" in list_page.url for list_page in pages))


class WidenedPeriodListPagesTest(unittest.TestCase):
    def _items(self, urls):
        return {
            url: dbsr.ListPage(
                title="", year_label="", url=url, meeting_group="", auxiliary_docs=[]
            )
            for url in urls
        }

    def test_period_is_widened_and_grouped_per_cabinet(self) -> None:
        items = self._items([
            "https://example.dbsr.jp/index.php/1?Template=list&CabinetName=t&TermStart=2026-02-20&TermEnd=2026-03-24",
            "https://example.dbsr.jp/index.php/1?Template=list&CabinetName=t&TermStart=2025-02-04&TermEnd=2025-03-25",
            "https://example.dbsr.jp/index.php/1?Template=list&CabinetName=r&TermStart=2025-05-16&TermEnd=2025-05-20",
        ])
        pages = dbsr.widened_period_list_pages(items)
        # 会議種別ごとに 1 本へまとめる。
        self.assertEqual(len(pages), 2)
        for page in pages:
            self.assertIn("TermStart=1970-01-01", page.url)
            self.assertEqual(page.year_label, "全期間")

    def test_links_without_a_period_cannot_be_widened(self) -> None:
        items = self._items([
            "https://example.dbsr.jp/index.php/1?Template=list&CabinetName=t",
        ])
        self.assertEqual(dbsr.widened_period_list_pages(items), [])


class MissingCabinetListPagesTest(unittest.TestCase):
    def _items(self) -> dict:
        url = (
            "https://example.dbsr.jp/index.php/100000?Cabinet=1&Template=list"
            "&TermEnd=2026-03-25&TermStart=2026-02-18"
        )
        return {
            url: dbsr.ListPage(
                title="令和8年2月定例会",
                year_label="令和8年",
                url=url,
                meeting_group="",
                auxiliary_docs=[],
            )
        }

    def test_adds_pages_for_cabinets_missing_from_the_listing(self) -> None:
        # 年度別一覧が全期間そろっていても、本会議だけということがある。
        options = [
            {"key": "Cabinet", "value": "1", "text": "本会議"},
            {"key": "Cabinet", "value": "5", "text": "総務企画委員会"},
        ]
        pages = dbsr.missing_cabinet_list_pages(self._items(), options)
        self.assertEqual(len(pages), 1)
        self.assertIn("Cabinet=5", pages[0].url)
        self.assertIn(f"TermStart={dbsr.WIDENED_PERIOD_START}", pages[0].url)
        self.assertEqual(pages[0].year_label, "全期間")
        self.assertIn("総務企画委員会", pages[0].title)
        # 種別が分かっているのに空で渡すと、収録した会議が種別なしになる。
        self.assertEqual(pages[0].meeting_group, "総務企画委員会")

    def test_no_pages_when_every_cabinet_is_present(self) -> None:
        options = [{"key": "Cabinet", "value": "1", "text": "本会議"}]
        self.assertEqual(dbsr.missing_cabinet_list_pages(self._items(), options), [])

    def test_no_pages_without_options(self) -> None:
        self.assertEqual(dbsr.missing_cabinet_list_pages(self._items(), []), [])

class BuildDayGroupsTest(unittest.TestCase):
    def _page(self, year_label: str) -> "dbsr.ListPage":
        return dbsr.ListPage(
            title="全期間（総務企画委員会）",
            year_label=year_label,
            url="list",
            meeting_group="総務企画委員会",
            auxiliary_docs=[],
        )

    def test_full_period_listing_takes_the_year_from_the_meeting_date(self) -> None:
        # 「全期間」のままだと、呼び出し側が (年, 種別, 表題) で重複を除くときに
        # 同じ委員会の同じ月日が年をまたいで 1 件へ潰れてしまう。
        rows = [
            dbsr.DocumentRow(title="総務企画委員会 本文", url="u1", held_on="2010-02-18"),
            dbsr.DocumentRow(title="総務企画委員会 本文", url="u2", held_on="2015-02-18"),
        ]
        groups = dbsr.build_day_groups(self._page(dbsr.WIDENED_PERIOD_LABEL), "list", rows)
        self.assertEqual([g.year_label for g in groups], ["平成22年", "平成27年"])
        keys = {(g.year_label, g.meeting_group, g.title) for g in groups}
        self.assertEqual(len(keys), 2)

    def test_year_listing_keeps_its_own_label(self) -> None:
        rows = [dbsr.DocumentRow(title="本文", url="u1", held_on="2010-02-18")]
        groups = dbsr.build_day_groups(self._page("平成22年度"), "list", rows)
        self.assertEqual(groups[0].year_label, "平成22年度")

class RelabelNavigationGroupsTest(unittest.TestCase):
    def _items(self, cabinet: str, group: str) -> dict:
        url = "https://x.dbsr.jp/index.php/100000?Cabinet=%s&Template=list" % cabinet
        return {
            url: dbsr.ListPage(
                title="t", year_label="平成14年", url=url,
                meeting_group=group, auxiliary_docs=[],
            )
        }

    def test_navigation_label_is_replaced_with_the_meeting_type(self) -> None:
        # 一覧の見出しをそのまま種別にすると「トップページ」のような値が入る。
        items = self._items("2", "トップページ")
        options = [{"key": "Cabinet", "value": "2", "text": "企画総務委員会"}]
        self.assertEqual(dbsr.relabel_navigation_groups(items, options), 1)
        self.assertEqual(list(items.values())[0].meeting_group, "企画総務委員会")

    def test_unmatched_cabinet_is_left_alone(self) -> None:
        # 空にすると保存先が変わり、次の取得で旧ファイルが孤児になる。
        items = self._items("9", "トップページ")
        options = [{"key": "Cabinet", "value": "2", "text": "企画総務委員会"}]
        self.assertEqual(dbsr.relabel_navigation_groups(items, options), 0)
        self.assertEqual(list(items.values())[0].meeting_group, "トップページ")

    def test_real_meeting_type_is_left_alone(self) -> None:
        items = self._items("2", "企画総務委員会")
        options = [{"key": "Cabinet", "value": "2", "text": "別の名前"}]
        self.assertEqual(dbsr.relabel_navigation_groups(items, options), 0)
        self.assertEqual(list(items.values())[0].meeting_group, "企画総務委員会")

class ExpandCabinetValuesTest(unittest.TestCase):
    def test_grouped_values_are_split_per_number(self) -> None:
        # 束ねたまま一覧 URL に入れると、既存の Cabinet=1 と別物になってしまう。
        options = [{"key": "Cabinet", "value": "1,2", "text": "本会議"}]
        self.assertEqual(
            dbsr.expand_cabinet_values(options),
            [
                {"key": "Cabinet", "value": "1", "text": "本会議"},
                {"key": "Cabinet", "value": "2", "text": "本会議"},
            ],
        )

    def test_duplicates_are_dropped(self) -> None:
        options = [
            {"key": "Cabinet", "value": "1,2", "text": "本会議"},
            {"key": "Cabinet", "value": "2", "text": "本会議(再掲)"},
        ]
        self.assertEqual(len(dbsr.expand_cabinet_values(options)), 2)


class _FakePage:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes
        self.timeouts: list[int] = []

    def goto(self, url, *, wait_until, timeout):
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return None


class GotoListPageRetryTest(unittest.TestCase):
    """10 秒の時間切れ 1 回で会議一覧を諦めない（島根県・橿原市・今治市がエラー停止のままだった）。"""

    def setUp(self) -> None:
        dbsr._consecutive_exhausted_gotos = 0
        mock.patch.object(dbsr.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(setattr, dbsr, "_consecutive_exhausted_gotos", 0)

    def test_timeout_is_retried_with_a_longer_wait(self) -> None:
        page = _FakePage([dbsr.PlaywrightTimeoutError("Page.goto: Timeout 10000ms exceeded."), None])

        dbsr.goto_list_page(page, "https://example.dbsr.jp/index.php/1?Template=list", 10_000)

        self.assertEqual(page.timeouts, [10_000, dbsr.LIST_GOTO_RETRY_MIN_TIMEOUT_MS])

    def test_connection_reset_is_retried(self) -> None:
        page = _FakePage([dbsr.PlaywrightError("net::ERR_CONNECTION_RESET at https://example.dbsr.jp/"), None])

        dbsr.goto_list_page(page, "https://example.dbsr.jp/", 10_000)

        self.assertEqual(len(page.timeouts), 2)

    def test_other_errors_are_not_retried(self) -> None:
        page = _FakePage([dbsr.PlaywrightError("Target page, context or browser has been closed")])

        with self.assertRaises(dbsr.PlaywrightError):
            dbsr.goto_list_page(page, "https://example.dbsr.jp/", 10_000)
        self.assertEqual(len(page.timeouts), 1)

    def test_gives_up_after_retries(self) -> None:
        page = _FakePage([dbsr.PlaywrightTimeoutError("t")] * 3)

        with self.assertRaises(dbsr.PlaywrightTimeoutError):
            dbsr.goto_list_page(page, "https://example.dbsr.jp/", 10_000)
        self.assertEqual(len(page.timeouts), 1 + len(dbsr.LIST_GOTO_RETRY_WAITS_SECONDS))

    def test_stops_retrying_while_the_source_is_down(self) -> None:
        attempts_per_call = 1 + len(dbsr.LIST_GOTO_RETRY_WAITS_SECONDS)
        give_up = dbsr.LIST_GOTO_RETRY_GIVE_UP_AFTER
        page = _FakePage([dbsr.PlaywrightTimeoutError("t")] * (attempts_per_call * give_up + 1) + [None])

        for _ in range(give_up + 1):
            with self.assertRaises(dbsr.PlaywrightTimeoutError):
                dbsr.goto_list_page(page, "https://example.dbsr.jp/", 10_000)
        self.assertEqual(len(page.timeouts), attempts_per_call * give_up + 1)

        dbsr.goto_list_page(page, "https://example.dbsr.jp/", 10_000)
        self.assertEqual(dbsr._consecutive_exhausted_gotos, 0)

    def test_retry_does_not_run_past_the_discovery_deadline(self) -> None:
        page = _FakePage([dbsr.PlaywrightTimeoutError("t"), None])

        with self.assertRaises(dbsr.DiscoveryTimeoutError):
            dbsr.goto_list_page(page, "https://example.dbsr.jp/", 10_000, deadline=0.0)
        self.assertEqual(len(page.timeouts), 1)


class _Node:
    """テスト用の DOM 要素。セレクタごとに子要素を持たせる。"""

    def __init__(self, text: str = "", attrs: dict | None = None, children: dict | None = None) -> None:
        self.text = text
        self.attrs = attrs or {}
        self.children = children or {}


class _Locator:
    def __init__(self, nodes: list, log: list | None = None) -> None:
        self.nodes = nodes
        self.log = log if log is not None else []

    @property
    def first(self) -> "_Locator":
        return _Locator(self.nodes[:1], self.log)

    def nth(self, index: int) -> "_Locator":
        return _Locator(self.nodes[index:index + 1], self.log)

    def count(self) -> int:
        return len(self.nodes)

    def locator(self, selector: str) -> "_Locator":
        return _Locator([child for node in self.nodes for child in node.children.get(selector, [])], self.log)

    def inner_text(self, timeout=None) -> str:
        self.log.append(("inner_text", timeout))
        if not self.nodes:
            raise dbsr.PlaywrightTimeoutError("no element")
        return self.nodes[0].text

    def get_attribute(self, name: str, timeout=None):
        self.log.append(("get_attribute", timeout))
        if not self.nodes:
            raise dbsr.PlaywrightTimeoutError("no element")
        return self.nodes[0].attrs.get(name)

    def evaluate_all(self, _script: str) -> list:
        return [node.attrs.get("value") for node in self.nodes]


class _Page:
    def __init__(self, url: str, selectors: dict) -> None:
        self.url = url
        self.selectors = selectors

    def locator(self, selector: str) -> _Locator:
        return _Locator(self.selectors.get(selector, []))


def _fukuoka_item(number: int, held_on: str) -> _Node:
    # 福岡県の行は修飾子付きの .ans-title__item--name / --date で組まれている。
    anchor = _Node(f"第{number}回定例会 本文", {"href": f"index.php/1?Template=document&Id={number}#one"})
    return _Node(children={
        ".ans-title__item--name a": [anchor],
        ".ans-title__item--date": [_Node(f"開催日:{held_on}")],
    })


class FukuokaListMarkupTest(unittest.TestCase):
    """福岡県の一覧は 1 行ごとに既定の 10 秒を待ち、1 ページ 5 分かかっていた。"""

    URL = "https://www.pref.fukuoka.dbsr.jp/index.php/100000?Template=list"

    def test_rows_with_modifier_classes_are_read(self) -> None:
        page = _Page(self.URL, {
            "ul.result-document li.result-document__item": [_fukuoka_item(1, "2005-05-23"), _fukuoka_item(2, "2005-05-24")],
        })

        rows = dbsr.extract_document_rows_from_page(page)

        self.assertEqual([row.held_on for row in rows], ["2005-05-23", "2005-05-24"])
        self.assertEqual(rows[0].title, "第1回定例会 本文")
        self.assertIn("Id=1", rows[0].url)

    def test_missing_href_does_not_wait_the_page_default(self) -> None:
        log: list = []
        dbsr.safe_href(_Locator([], log))
        self.assertEqual(log, [("get_attribute", 1_500)])

    def test_last_page_is_read_from_page_buttons(self) -> None:
        # ページ送りが <a> ではなく <button name="Page" value="766"> で組まれている。
        buttons = [_Node(attrs={"value": value}) for value in ("2", "3", "766", "")]
        page = _Page(self.URL, {".pagination button[name='Page']": buttons})

        self.assertEqual(dbsr.last_page_number(page), 766)


class ListWalkCheckpointTest(unittest.TestCase):
    """歩いた位置は、そこまでに集めた行と対で残す。"""

    def setUp(self) -> None:
        import tempfile

        self.work_dir = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.work_dir, True)
        for name in ("LIST_WALK_RESUME", "LIST_WALK_REACHED", "LIST_WALK_ROWS", "LIST_WALK_CACHED_ROWS"):
            getattr(dbsr, name).clear()
            self.addCleanup(getattr(dbsr, name).clear)
        mock.patch.object(dbsr, "LIST_WALK_PROGRESS_DIR", self.work_dir).start()
        self.addCleanup(mock.patch.stopall)

    def test_rows_are_saved_with_the_position(self) -> None:
        row = dbsr.DocumentRow(title="本文", url="https://x.dbsr.jp/index.php/1?Id=1", held_on="2005-05-23")
        dbsr.LIST_WALK_RESUME["k"] = 100
        dbsr.LIST_WALK_REACHED["k"] = 50
        dbsr.LIST_WALK_ROWS["k"] = [row]

        dbsr.save_list_walk_checkpoint()

        # 前回より手前の位置へは戻さない。
        self.assertEqual(dbsr.gijiroku_storage.load_list_walk_progress(self.work_dir), {"k": 100})
        self.assertEqual(
            dbsr.gijiroku_storage.load_list_walk_rows(self.work_dir),
            {"k": [{"title": "本文", "url": row.url, "held_on": "2005-05-23"}]},
        )

    def test_cached_rows_of_walks_not_visited_are_kept(self) -> None:
        cached = dbsr.DocumentRow(title="前回", url="https://x.dbsr.jp/index.php/1?Id=9", held_on="2001-01-01")
        dbsr.LIST_WALK_CACHED_ROWS["old"] = [cached]
        dbsr.LIST_WALK_REACHED["new"] = 25
        dbsr.LIST_WALK_ROWS["new"] = []

        dbsr.save_list_walk_checkpoint()

        self.assertEqual(list(dbsr.gijiroku_storage.load_list_walk_rows(self.work_dir)), ["old"])

    def test_clearing_rows_keeps_the_position(self) -> None:
        dbsr.LIST_WALK_REACHED["k"] = 30
        dbsr.LIST_WALK_ROWS["k"] = [dbsr.DocumentRow(title="t", url="u", held_on="2005-05-23")]
        dbsr.save_list_walk_checkpoint()

        dbsr.gijiroku_storage.clear_list_walk_rows(self.work_dir)

        self.assertEqual(dbsr.gijiroku_storage.load_list_walk_rows(self.work_dir), {})
        self.assertEqual(dbsr.gijiroku_storage.load_list_walk_progress(self.work_dir), {"k": 30})


class _WalkPage:
    """Page= で開くページが変わる一覧。最後のページはボタンの value で申告する。"""

    def __init__(self, url: str, rows_per_page: dict[int, list]) -> None:
        self.rows_per_page = rows_per_page
        self.opened: list[int] = []
        self.goto(url)

    def goto(self, url: str, **_kwargs) -> None:
        from urllib.parse import parse_qs, urlsplit

        self.url = url
        self.current = int(parse_qs(urlsplit(url).query).get("Page", ["1"])[0])
        self.opened.append(self.current)

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def inner_text(self, *_args, **_kwargs) -> str:
        return ""

    def locator(self, selector: str) -> _Locator:
        if selector == "ul.result-document li.result-document__item":
            return _Locator(self.rows_per_page.get(self.current, []))
        if selector == ".pagination button[name='Page']":
            return _Locator([_Node(attrs={"value": str(max(self.rows_per_page))})])
        return _Locator([])


class ListWalkResumeTest(unittest.TestCase):
    URL = "https://www.pref.fukuoka.dbsr.jp/index.php/100000?Template=list&ListOrder=Asc"

    def setUp(self) -> None:
        for name in (
            "LIST_WALK_RESUME", "LIST_WALK_REACHED", "LIST_WALK_ROWS", "LIST_WALK_CACHED_ROWS",
            "LIST_WALK_RESUMED", "LIST_WALK_TIMED_OUT", "ABANDONED_LIST_PAGES", "REPEATED_LIST_PAGES",
            "DECLARED_DOCUMENT_TOTALS",
        ):
            getattr(dbsr, name).clear()
            self.addCleanup(getattr(dbsr, name).clear)

    def test_resumed_walk_keeps_rows_the_killed_run_had_not_written(self) -> None:
        # 前回は 3 ページ目まで歩いたが、meetings_index へ書く前に止まった。
        key = dbsr.list_walk_key(self.URL)
        dbsr.LIST_WALK_RESUME[key] = 3
        dbsr.LIST_WALK_CACHED_ROWS[key] = [
            dbsr.DocumentRow(title="第1回定例会 本文", url=f"https://www.pref.fukuoka.dbsr.jp/index.php/1?Template=document&Id={n}", held_on="2005-05-23")
            for n in (1, 2)
        ]
        page = _WalkPage(self.URL, {1: [_fukuoka_item(1, "2005-05-23")], 2: [_fukuoka_item(2, "2005-05-24")], 3: [_fukuoka_item(3, "2005-05-25")]})

        rows = dbsr.collect_document_rows_from_open_list(page, 1_000)

        self.assertEqual(page.opened, [1, 3])  # 1・2 ページ目は開き直さない
        self.assertEqual(sorted(row.url.split("Id=")[1] for row in rows), ["1", "2", "3"])
        self.assertEqual(dbsr.LIST_WALK_RESUMED, [key])
        self.assertIs(dbsr.LIST_WALK_ROWS[key], rows)

    def test_walk_from_the_start_ignores_cached_rows(self) -> None:
        key = dbsr.list_walk_key(self.URL)
        dbsr.LIST_WALK_CACHED_ROWS[key] = [dbsr.DocumentRow(title="古い行", url="https://x/old", held_on="2001-01-01")]
        page = _WalkPage(self.URL, {1: [_fukuoka_item(1, "2005-05-23")], 2: [_fukuoka_item(2, "2005-05-24")]})

        rows = dbsr.collect_document_rows_from_open_list(page, 1_000)

        self.assertEqual(page.opened, [1, 2])
        self.assertNotIn("https://x/old", [row.url for row in rows])


if __name__ == "__main__":
    unittest.main()
