"""一部だけ作った索引を、公開の alias に切り替えさせない。

`--mode rebuild --slug X` や `--limit N` で作った索引を alias に切り替えると、
残りの自治体がまるごと検索から消える。取得側をいくら直しても、
再構築コマンド単体でここが成立してしまう。
"""

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "build_opensearch_index.py"
BLOCKED = 2


def run(*argv: str) -> int:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode


class PartialAliasGuardTest(unittest.TestCase):
    def test_partial_rebuild_by_slug_is_refused(self):
        self.assertEqual(run("--mode", "rebuild", "--slug", "13101-chiyoda-ku"), BLOCKED)

    def test_limited_rebuild_is_refused(self):
        self.assertEqual(run("--mode", "rebuild", "--limit", "5"), BLOCKED)

    def test_building_without_switching_is_allowed(self):
        self.assertNotEqual(
            run("--mode", "rebuild", "--slug", "13101-chiyoda-ku", "--no-switch-alias"),
            BLOCKED,
        )

    def test_explicit_override_is_allowed(self):
        self.assertNotEqual(
            run("--mode", "rebuild", "--slug", "13101-chiyoda-ku", "--allow-partial-alias"),
            BLOCKED,
        )

    def test_per_municipality_update_is_not_a_partial_rebuild(self):
        # update は自治体ごとの差し替えで、alias は作り直さない。
        self.assertNotEqual(run("--mode", "update", "--slug", "13101-chiyoda-ku"), BLOCKED)

    def test_full_rebuild_is_allowed(self):
        self.assertNotEqual(run("--mode", "rebuild"), BLOCKED)


if __name__ == "__main__":
    unittest.main()
