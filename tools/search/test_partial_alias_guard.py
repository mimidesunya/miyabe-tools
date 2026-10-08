"""一部だけ作った索引を、公開の alias に切り替えさせない。

`--mode rebuild --slug X` や `--limit N` で作った索引を alias に切り替えると、
残りの自治体がまるごと検索から消える。取得側をいくら直しても、
再構築コマンド単体でここが成立してしまう。

判定だけをプロセスの中で確かめる。以前は子プロセスで本物のコマンドを
起動していたので、許される側の指定では実際に再構築が始まり、手元で
OpenSearch が動いていれば索引を作り直して alias を切り替えていた。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_opensearch_index as indexer  # noqa: E402


def refused(*argv: str) -> bool:
    args = indexer.parse_args(list(argv))
    slugs = indexer.parse_slug_filter(args.slug)
    return indexer.partial_alias_refusal(args, indexer.resolve_mode(args, slugs), slugs) != ""


class PartialAliasGuardTest(unittest.TestCase):
    def test_partial_rebuild_by_slug_is_refused(self):
        self.assertTrue(refused("--mode", "rebuild", "--slug", "13101-chiyoda-ku"))

    def test_limited_rebuild_is_refused(self):
        self.assertTrue(refused("--mode", "rebuild", "--limit", "5"))

    def test_building_without_switching_is_allowed(self):
        self.assertFalse(refused("--mode", "rebuild", "--slug", "13101-chiyoda-ku", "--no-switch-alias"))

    def test_explicit_override_is_allowed(self):
        self.assertFalse(refused("--mode", "rebuild", "--slug", "13101-chiyoda-ku", "--allow-partial-alias"))

    def test_per_municipality_update_is_not_a_partial_rebuild(self):
        # update は自治体ごとの差し替えで、alias は作り直さない。
        self.assertFalse(refused("--mode", "update", "--slug", "13101-chiyoda-ku"))

    def test_limited_update_is_refused(self):
        # 差分更新はその自治体の文書を全部消してから入れ直す。--limit で
        # 切ると、消したあと一部しか戻らず、生きている検索から大半が消える。
        self.assertTrue(refused("--mode", "update", "--slug", "13101-chiyoda-ku", "--limit", "5"))

    def test_update_needs_a_slug(self):
        self.assertTrue(refused("--mode", "update"))

    def test_auto_with_a_slug_is_an_update(self):
        args = indexer.parse_args(["--slug", "13101-chiyoda-ku"])
        self.assertEqual(indexer.resolve_mode(args, indexer.parse_slug_filter(args.slug)), "update")

    def test_partial_resume_is_refused(self):
        # resume も途中から作り直すので、slug で絞れば部分索引になる。
        self.assertTrue(
            refused(
                "--mode", "resume", "--resume-index", "miyabe-reiki-x",
                "--doc-type", "reiki", "--slug", "13101-chiyoda-ku",
            )
        )

    def test_full_rebuild_is_allowed(self):
        self.assertFalse(refused("--mode", "rebuild"))


if __name__ == "__main__":
    unittest.main()
