from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from domains.election_poster_boards.tools.init_db import (
    init_boards_db,
    init_tasks_db,
)


class DatabaseToolsTest(unittest.TestCase):
    def test_initializes_domain_databases_from_tsv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tsv_path = root / "source.tsv"
            tsv_path.write_text(
                "code\taddress\tplace\tlat\tlon\n"
                "1\tテスト市1丁目\t公園前\t35.0\t139.0\n",
                encoding="utf-8",
            )

            init_boards_db("99999-test-shi", root, tsv_path)
            init_tasks_db("99999-test-shi", root)

            board_db = root / "data" / "boards" / "99999-test-shi" / "boards.sqlite"
            task_db = root / "data" / "boards" / "99999-test-shi" / "tasks.sqlite"
            with closing(sqlite3.connect(board_db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM boards_rtree").fetchone()[0], 1)
            with closing(sqlite3.connect(task_db)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertIn("task_status", tables)
                self.assertIn("status_history", tables)


if __name__ == "__main__":
    unittest.main()
