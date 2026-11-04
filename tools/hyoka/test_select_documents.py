"""集める文書を選ぶ処理の試験。Codex は呼ばない。"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import select_documents as s  # noqa: E402


class NumbersTest(unittest.TestCase):
    def test_ranges(self) -> None:
        self.assertEqual(s.parse_numbers("1-3,5"), {1, 2, 3, 5})
        self.assertEqual(s.parse_numbers(""), set())

    def test_bad_text_raises(self) -> None:
        with self.assertRaises(ValueError):
            s.parse_numbers("1〜3")


class RolesTest(unittest.TestCase):
    def test_every_number_once(self) -> None:
        roles = s.row_roles({"sheet": "1-2", "other": "3"}, 3)
        self.assertEqual(roles, {1: "sheet", 2: "sheet", 3: "other"})

    def test_missing_number_is_rejected(self) -> None:
        self.assertIsNone(s.row_roles({"sheet": "1-2"}, 3))

    def test_number_in_two_roles_is_rejected(self) -> None:
        self.assertIsNone(s.row_roles({"sheet": "1-2", "other": "2-3"}, 3))

    def test_broken_range_is_rejected(self) -> None:
        self.assertIsNone(s.row_roles({"sheet": "全部"}, 3))


class PackTest(unittest.TestCase):
    def test_keeps_under_limit(self) -> None:
        self.assertEqual(s.pack([("a", 3), ("b", 3), ("c", 3)], 6), [["a", "b"], ["c"]])

    def test_big_target_gets_own_batch(self) -> None:
        self.assertEqual(s.pack([("a", 1), ("b", 100)], 10), [["a"], ["b"]])


class BatchRolesTest(unittest.TestCase):
    def test_only_complete_targets_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with open(folder / "candidates.tsv", "w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
                writer.writerow(s.CANDIDATE_COLUMNS)
                writer.writerows([["01000", 1, "", "", "pdf", "", "", "", ""], ["01000", 2, "", "", "pdf", "", "", "", ""], ["02000", 1, "", "", "pdf", "", "", "", ""]])
            lines = [json.dumps({"jis_code": "01000", "sheet": "1", "other": "2"}), json.dumps({"jis_code": "02000", "sheet": ""}), "壊れた行"]
            (folder / "roles.jsonl").write_text("\n".join(lines), encoding="utf-8")
            self.assertEqual(list(s.batch_roles(folder)), ["01000"])


class FileNameTest(unittest.TestCase):
    def test_japanese_name_is_decoded(self) -> None:
        self.assertEqual(s.file_name("https://a.jp/%E4%BA%8B%E6%A5%AD.pdf?x=1"), "事業.pdf")


if __name__ == "__main__":
    unittest.main()
