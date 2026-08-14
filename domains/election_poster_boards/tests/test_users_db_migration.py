from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from domains.election_poster_boards.tools.migrate_legacy_users_db import (
    validate_users_db,
)


class UsersDbValidationTest(unittest.TestCase):
    def test_accepts_database_with_users_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "users.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
                conn.commit()

            validate_users_db(db_path)

    def test_rejects_unrelated_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "other.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE something_else (id INTEGER PRIMARY KEY)")
                conn.commit()

            with self.assertRaises(RuntimeError):
                validate_users_db(db_path)


if __name__ == "__main__":
    unittest.main()
