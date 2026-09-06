#!/usr/bin/env python3
"""走査記録が公開画面から読める権限で置かれることを確かめる。

`tempfile.mkstemp` は 0600 で作る。そのまま置き換えると、公開画面の PHP
（www-data）が走査記録を読めない。読めないと「記録が無い」として扱われ、
取り切れているのに「取得範囲未判定」のまま固定される。2026-09-06 の点検では
会議録 1,400 自治体すべてがこの形で、`source_coverage.json` 1,511 本が
0600 のまま置かれていた。例規側は別の書き方をしていて 0644 だった。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

import gijiroku_storage


@unittest.skipIf(os.name == "nt", "POSIX の権限ビットが無い環境では確かめられない")
class WebReadableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def mode_of(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_bytes_are_group_and_other_readable(self) -> None:
        path = gijiroku_storage.write_bytes(self.root / "source_coverage.json", b"{}")
        self.assertTrue(self.mode_of(path) & stat.S_IRGRP, oct(self.mode_of(path)))
        self.assertTrue(self.mode_of(path) & stat.S_IROTH, oct(self.mode_of(path)))

    def test_text_is_group_readable(self) -> None:
        path = gijiroku_storage.write_text(self.root / "meetings_index.json", "[]")
        self.assertTrue(self.mode_of(path) & stat.S_IRGRP, oct(self.mode_of(path)))

    def test_compressed_output_is_group_readable(self) -> None:
        path = gijiroku_storage.write_bytes(self.root / "body.txt", b"honbun", compress=True)
        self.assertTrue(self.mode_of(path) & stat.S_IRGRP, oct(self.mode_of(path)))


class ModeConstantTest(unittest.TestCase):
    def test_the_mode_is_readable_but_not_group_writable(self) -> None:
        # 書き込みは取得側だけが行う。読める必要があるのは公開側。
        mode = gijiroku_storage.WEB_READABLE_FILE_MODE
        self.assertTrue(mode & stat.S_IRGRP)
        self.assertTrue(mode & stat.S_IROTH)
        self.assertFalse(mode & stat.S_IWGRP)
        self.assertFalse(mode & stat.S_IWOTH)


if __name__ == "__main__":
    unittest.main()
