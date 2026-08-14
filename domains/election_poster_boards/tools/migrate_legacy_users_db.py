#!/usr/bin/env python3
"""Copy the legacy shared users DB into the poster-boards data hierarchy."""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def validate_users_db(path: Path) -> None:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
    if row is None:
        raise RuntimeError(f"users テーブルがありません: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="data/users.sqlite を data/boards/users.sqlite へ非破壊コピーします"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にコピーする（省略時は確認のみ）",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    source = root / "data" / "users.sqlite"
    destination = root / "data" / "boards" / "users.sqlite"

    if not source.is_file():
        print(f"旧ユーザーDBはありません: {source}")
        return 0
    validate_users_db(source)

    if destination.exists():
        print(f"移行先が既に存在するため変更しません: {destination}")
        return 0

    print(f"移行元: {source}")
    print(f"移行先: {destination}")
    if not args.apply:
        print("確認のみです。コピーするには --apply を指定してください。")
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    validate_users_db(destination)
    print("コピーと検証が完了しました。旧DBは削除していません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
