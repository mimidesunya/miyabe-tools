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


class BuildFullPeriodDayGroupsTest(unittest.TestCase):
    def test_same_day_different_meetings_are_not_merged(self) -> None:
        rows = [
            dbsr.DocumentRow(title="平成９年第４回定例会（第５日目）　本文", url="u1", held_on="1997-12-18"),
            dbsr.DocumentRow(title="平成９年第４回定例会（第５日目）　名簿", url="u2", held_on="1997-12-18"),
            dbsr.DocumentRow(title="平成９年福祉委員会　本文", url="u3", held_on="1997-12-18"),
        ]
        groups = dbsr.build_full_period_day_groups("list", rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].meeting_group, "平成９年第４回定例会（第５日目）")
        self.assertEqual(groups[0].doc_urls, ["u1"])  # 本文がある日は本文だけを採る
        self.assertEqual(groups[1].meeting_group, "平成９年福祉委員会")
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


if __name__ == "__main__":
    unittest.main()
