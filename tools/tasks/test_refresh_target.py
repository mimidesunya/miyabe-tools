"""起動の直前に登録簿を引き直す差し込み口を確かめる。

全国一巡は数日かかる。対象一覧は実行の始めに一度だけ作るので、その間に
登録簿(TSV)が変わると、取得をやめた自治体を数日後に起動してしまう。
子スクレイパは方針判定で落ち、結果は「取得失敗」に数えられていた
(実測 2026-09-05: 会議録 239件の失敗のうち、除外済み4件・system_type が
変わって別のスクレイパへ回っていた3件)。

放っておいても治るように、起動の直前で今の登録簿を引き直す。
"""

from __future__ import annotations

import unittest

from tools.gijiroku import scrape_all_minutes
from tools.reiki import scrape_all_reiki


class RefreshGijirokuTargetTest(unittest.TestCase):
    def test_取得をやめた自治体は見送る(self):
        original = scrape_all_minutes.gijiroku_targets.load_gijiroku_target

        def blocked(slug, expected_system=None, **kwargs):
            raise scrape_all_minutes.gijiroku_targets.CrawlPolicyBlockedError(
                f"Municipality is not enabled for crawling: {slug} / not_published (未公開)"
            )

        scrape_all_minutes.gijiroku_targets.load_gijiroku_target = blocked
        try:
            target, reason = scrape_all_minutes.refresh_gijiroku_target({"slug": "x-cho"})
        finally:
            scrape_all_minutes.gijiroku_targets.load_gijiroku_target = original
        self.assertIsNone(target)
        self.assertIn("取得ポリシー", reason)
        self.assertIn("not_published", reason)

    def test_登録簿から消えた自治体も見送る(self):
        original = scrape_all_minutes.gijiroku_targets.load_gijiroku_target

        def missing(slug, expected_system=None, **kwargs):
            raise ValueError(f"Municipality slug not found: {slug}")

        scrape_all_minutes.gijiroku_targets.load_gijiroku_target = missing
        try:
            target, reason = scrape_all_minutes.refresh_gijiroku_target({"slug": "x-cho"})
        finally:
            scrape_all_minutes.gijiroku_targets.load_gijiroku_target = original
        self.assertIsNone(target)
        self.assertEqual(reason, "登録簿から外れました")

    def test_system_type_が変わっていたら今の値で走らせる(self):
        original = scrape_all_minutes.gijiroku_targets.load_gijiroku_target

        def changed(slug, expected_system=None, **kwargs):
            # 鮮度の推定は成果物を読むので、実在しない道でも壊れない値を入れる
            return {
                "slug": slug,
                "code": "47314",
                "system_type": "kin-jsp",
                "source_url": "https://example/",
                "index_json_path": "work/gijiroku/47314-kin-cho/meetings_index.json",
                "downloads_dir": "work/gijiroku/47314-kin-cho/downloads",
                "data_dir": "work/gijiroku/47314-kin-cho",
            }

        scrape_all_minutes.gijiroku_targets.load_gijiroku_target = changed
        try:
            target, reason = scrape_all_minutes.refresh_gijiroku_target(
                {"slug": "47314-kin-cho", "system_type": "独自"}
            )
        finally:
            scrape_all_minutes.gijiroku_targets.load_gijiroku_target = original
        self.assertEqual(reason, "")
        self.assertIsNotNone(target)
        self.assertEqual(target["system_type"], "kin-jsp")

    def test_slug_が空なら何もしない(self):
        target, reason = scrape_all_minutes.refresh_gijiroku_target({"slug": ""})
        self.assertEqual(reason, "")
        self.assertEqual(target, {"slug": ""})


class RefreshReikiTargetTest(unittest.TestCase):
    def test_登録簿から消えた自治体は見送る(self):
        original = scrape_all_reiki.reiki_targets.load_reiki_target

        def missing(slug, expected_system=None, **kwargs):
            raise ValueError(f"Municipality slug not found: {slug}")

        scrape_all_reiki.reiki_targets.load_reiki_target = missing
        try:
            target, reason = scrape_all_reiki.refresh_reiki_target({"slug": "x-shi"})
        finally:
            scrape_all_reiki.reiki_targets.load_reiki_target = original
        self.assertIsNone(target)
        self.assertEqual(reason, "登録簿から外れました")

    def test_残っている自治体は今の姿で走らせる(self):
        original = scrape_all_reiki.reiki_targets.load_reiki_target

        def found(slug, expected_system=None, **kwargs):
            return {
                "slug": slug,
                "code": "22223",
                "system_type": "d1-law",
                "source_url": "https://example/",
                "manifest_path": "work/reiki/22223-omaezaki-shi/source_manifest.json.gz",
                "data_dir": "work/reiki/22223-omaezaki-shi",
            }

        scrape_all_reiki.reiki_targets.load_reiki_target = found
        try:
            target, reason = scrape_all_reiki.refresh_reiki_target({"slug": "22223-omaezaki-shi"})
        finally:
            scrape_all_reiki.reiki_targets.load_reiki_target = original
        self.assertEqual(reason, "")
        self.assertEqual(target["system_type"], "d1-law")


class RecordSkippedIsNotCountedTest(unittest.TestCase):
    """見送りは成功にも失敗にも数えない。"""

    def test_counted_false_なら数えない(self):
        from tools.tasks import batch

        before = dict(batch.TARGET_OUTCOMES)
        try:
            batch.TARGET_OUTCOMES["succeeded"] = 0
            batch.TARGET_OUTCOMES["failed"] = 0

            class _Writer:
                def writerow(self, row):
                    pass

            class _Handle:
                def flush(self):
                    pass

            spec = scrape_all_minutes.BATCH_SPEC
            state = {"items": {}}
            try:
                batch.record_target_result(
                    spec,
                    _Writer(),
                    _Handle(),
                    status_state=state,
                    target={"slug": "x-cho", "code": "0", "name": "x", "full_name": "x",
                            "system_type": "独自", "source_url": ""},
                    host="example",
                    overall_status="skipped",
                    overall_returncode=0,
                    scrape_returncode=0,
                    index_status="skipped",
                    index_returncode="",
                    started_at="",
                    finished_at="",
                    stdout_log="",
                    stderr_log="",
                    index_stdout_log="",
                    index_stderr_log="",
                    message="登録簿から外れました",
                    counted=False,
                )
            except Exception:
                # 記録先の都合で落ちても、数えていないことだけは確かめたい
                pass
            self.assertEqual(batch.TARGET_OUTCOMES["succeeded"], 0)
            self.assertEqual(batch.TARGET_OUTCOMES["failed"], 0)
        finally:
            batch.TARGET_OUTCOMES.update(before)


if __name__ == "__main__":
    unittest.main()
