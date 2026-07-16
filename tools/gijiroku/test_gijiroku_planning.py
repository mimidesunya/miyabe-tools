import unittest

from tools.gijiroku import gijiroku_planning


class InferSortDateTest(unittest.TestCase):
    def test_prefers_later_complete_era_date_over_year_label(self) -> None:
        item = {
            "year_label": "令和 ７年",
            "title": "令和 ７年１２月定例会 11月28日-01号",
        }

        self.assertEqual(gijiroku_planning.infer_sort_date(item), "2025-11-28")


if __name__ == "__main__":
    unittest.main()
