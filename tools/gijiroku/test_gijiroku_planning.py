import unittest

from tools.gijiroku import gijiroku_planning


class InferSortDateTest(unittest.TestCase):
    def test_prefers_later_complete_era_date_over_year_label(self) -> None:
        item = {
            "year_label": "令和 ７年",
            "title": "令和 ７年１２月定例会 11月28日-01号",
        }

        self.assertEqual(gijiroku_planning.infer_sort_date(item), "2025-11-28")


class RetitlePlanTest(unittest.TestCase):
    def test_weak_title_changes_the_output_stem(self) -> None:
        import tempfile
        from pathlib import Path

        downloads = Path(tempfile.mkdtemp()) / "downloads"
        downloads.mkdir()
        item = {
            "title": "開議",
            "year_label": "令和7年",
            "url": "https://example.test/a.pdf",
        }
        plan = gijiroku_planning.build_base_plans([item], downloads, use_group_dir=False)[0]
        gijiroku_planning.attach_text_output(plan, key="text_base")
        self.assertTrue(str(plan["stem"]).startswith("開議"))
        gijiroku_planning.retitle_plan(plan, "決算特別委員会記録（第２号）", output_key="text_base")
        self.assertIn("決算特別委員会記録", plan["stem"])
        self.assertEqual(plan["item"]["title"], "決算特別委員会記録（第２号）")


if __name__ == "__main__":
    unittest.main()
