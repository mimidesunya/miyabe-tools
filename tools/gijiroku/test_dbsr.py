import unittest

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

    def test_no_pages_when_every_cabinet_is_present(self) -> None:
        options = [{"key": "Cabinet", "value": "1", "text": "本会議"}]
        self.assertEqual(dbsr.missing_cabinet_list_pages(self._items(), options), [])

    def test_no_pages_without_options(self) -> None:
        self.assertEqual(dbsr.missing_cabinet_list_pages(self._items(), []), [])


if __name__ == "__main__":
    unittest.main()
