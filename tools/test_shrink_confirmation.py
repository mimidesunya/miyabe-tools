#!/usr/bin/env python3
"""縮んだ一覧を、繰り返しの観測で自動確定できることを確かめる。

正本を守るために、大きく減った一覧は上書きしない設計になっている。
待つ人がいないとそこで永久に止まる。同じ縮み方が日をまたいで再現するなら
一時的な不調ではないので、繰り返しの観測を人の確認の代わりにする。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent / "reiki"))

import shrink_confirmation
import reiki_io


class SignatureTest(unittest.TestCase):
    def test_same_contents_give_the_same_signature(self) -> None:
        a = [{"source_file": "b.html"}, {"source_file": "a.html"}]
        b = [{"source_file": "a.html"}, {"source_file": "b.html"}]
        self.assertEqual(
            shrink_confirmation.manifest_signature(a),
            shrink_confirmation.manifest_signature(b),
        )

    def test_swapped_contents_differ_even_at_the_same_count(self) -> None:
        # 件数だけ見ていると、入れ替わった一覧を同じ縮みとして数えてしまう。
        a = [{"source_file": "a.html"}, {"source_file": "b.html"}]
        b = [{"source_file": "a.html"}, {"source_file": "c.html"}]
        self.assertNotEqual(
            shrink_confirmation.manifest_signature(a),
            shrink_confirmation.manifest_signature(b),
        )

    def test_falls_back_through_the_id_keys(self) -> None:
        rows = [{"detail_url": "https://example.test/a"}, {"url": "https://example.test/b"}]
        self.assertTrue(shrink_confirmation.manifest_signature(rows).startswith("2:"))


class ObserveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.manifest = Path(self.directory.name) / "source_manifest.json.gz"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def observe(self, signature: str, now: str) -> dict:
        return shrink_confirmation.observe(self.manifest, signature, now=now)

    def test_three_observations_across_days_confirm(self) -> None:
        self.assertFalse(self.observe("sig", "2026-09-01 10:00:00")["confirmed"])
        self.assertFalse(self.observe("sig", "2026-09-02 10:00:00")["confirmed"])
        result = self.observe("sig", "2026-09-03 10:00:00")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["seen"], 3)

    def test_quick_retries_do_not_count(self) -> None:
        # 同じ日に何度も落ちて再試行しただけでは確定させない。
        self.observe("sig", "2026-09-01 10:00:00")
        for minute in (5, 20, 55):
            result = self.observe("sig", f"2026-09-01 10:{minute:02d}:00")
            self.assertFalse(result["confirmed"])
            self.assertEqual(result["seen"], 1)

    def test_a_different_shrink_restarts_the_count(self) -> None:
        self.observe("sig", "2026-09-01 10:00:00")
        self.observe("sig", "2026-09-02 10:00:00")
        result = self.observe("other", "2026-09-03 10:00:00")
        self.assertEqual(result["seen"], 1)
        self.assertFalse(result["confirmed"])

    def test_stale_observations_expire(self) -> None:
        self.observe("sig", "2026-01-01 10:00:00")
        self.observe("sig", "2026-01-02 10:00:00")
        # 期限を過ぎた観測は数え直す。取得元が戻ったあとの記録を持ち越さない。
        result = self.observe("sig", "2026-09-01 10:00:00")
        self.assertEqual(result["seen"], 1)

    def test_broken_observation_file_is_ignored(self) -> None:
        path = shrink_confirmation.observation_path(self.manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.observe("sig", "2026-09-01 10:00:00")["seen"], 1)


class ManifestGuardTest(unittest.TestCase):
    """write_manifest_guarded から見た振る舞い。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "source_manifest.json.gz"
        previous = [{"source_file": f"{index}.html"} for index in range(100)]
        reiki_io.write_json(self.path, previous, compress=True)
        self.small = [{"source_file": f"{index}.html"} for index in range(10)]

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write(self, manifest: list, walk_complete: bool) -> dict:
        return reiki_io.write_manifest_guarded(
            self.path, manifest, label="試験", walk_complete=walk_complete
        )

    def test_incomplete_walks_never_confirm(self) -> None:
        # 取り切れていない走査は、何度繰り返しても取り切れていない。
        for _ in range(5):
            result = self.write(self.small, walk_complete=False)
            self.assertFalse(result["written"])

    def test_a_complete_walk_confirms_after_repeated_observations(self) -> None:
        stamps = ["2026-09-01 10:00:00", "2026-09-02 10:00:00", "2026-09-03 10:00:00"]
        signature = shrink_confirmation.manifest_signature(self.small)
        for stamp in stamps[:2]:
            shrink_confirmation.observe(self.path, signature, now=stamp)
        # 3 回目の観測で確定し、正本が置き換わる。
        result = self.write(self.small, walk_complete=True)
        self.assertTrue(result["written"])
        self.assertEqual(result["previous"], 100)
        self.assertEqual(result["current"], 10)

    def test_observation_is_cleared_after_writing(self) -> None:
        self.write(self.small, walk_complete=True)
        self.assertTrue(shrink_confirmation.observation_path(self.path).exists())
        # 減らない一覧を書けたら観測は用済み。
        grown = [{"source_file": f"{index}.html"} for index in range(120)]
        self.write(grown, walk_complete=True)
        self.assertFalse(shrink_confirmation.observation_path(self.path).exists())


if __name__ == "__main__":
    unittest.main()
