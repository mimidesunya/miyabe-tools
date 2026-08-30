import unittest
import tempfile
from pathlib import Path
from unittest import mock

from tools.gijiroku import audit_minutes_robots, crawl_policy, gijiroku_targets
from tools.gijiroku.robots_rules import robots_can_fetch
from tools.gijiroku.scrapers.static_kaigiroku_dir import should_follow_related_minutes_page


class MinutesRobotsPolicyTest(unittest.TestCase):
    def test_registry_rewrite_keeps_web_readable_permissions(self) -> None:
        row = {field: "" for field in audit_minutes_robots.FIELDNAMES}
        row.update({"jis_code": "00000", "crawl_status": "unresolved"})
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "assembly_minutes_system_urls.tsv"
            path.write_text("before\n", encoding="utf-8")
            with mock.patch.object(audit_minutes_robots.os, "chmod") as chmod:
                audit_minutes_robots.write_rows(path, [row])

        chmod.assert_called_once()
        self.assertEqual(chmod.call_args.args[1], 0o644)

    def test_longer_allow_rule_wins_even_when_written_after_disallow(self) -> None:
        robots = "User-agent: *\nDisallow: /\nAllow: /tenant/\n"

        self.assertTrue(
            robots_can_fetch(robots, audit_minutes_robots.USER_AGENT, "https://example.test/tenant/sample/index.html")
        )
        self.assertFalse(
            robots_can_fetch(robots, audit_minutes_robots.USER_AGENT, "https://example.test/dnp/search/")
        )

    def test_kaigiroku_net_checks_required_api(self) -> None:
        row = {
            "url": "https://ssp.kaigiroku.net/tenant/example/pg/index.html",
            "system_type": "kaigiroku.net",
        }

        required = audit_minutes_robots.required_crawl_urls(row)

        self.assertIn("https://ssp.kaigiroku.net/dnp/search/", required)

    def test_dbsr_checks_search_library_below_allowed_root(self) -> None:
        row = {
            "url": "https://example.dbsr.jp/index.php/",
            "system_type": "dbsr",
        }

        required = audit_minutes_robots.required_crawl_urls(row)

        self.assertIn("https://example.dbsr.jp/index.php/100000?Template=search-library", required)

    def test_explicit_disallow_no_longer_excludes(self) -> None:
        # robots.txt を根拠に取得を止めない方針（ENFORCE_ROBOTS=False）にしたので、
        # Disallow が書かれていても除外にはしない。過去の robots 由来の除外も解除する。
        row = {
            "jis_code": "00000",
            "url": "https://ssp.kaigiroku.net/tenant/example/pg/index.html",
            "system_type": "kaigiroku.net",
            "crawl_status": "excluded",
            "exclusion_reason": "robots_disallowed",
        }
        robots = audit_minutes_robots.RobotsResult(
            url="https://ssp.kaigiroku.net/robots.txt",
            status_code=200,
            body="User-agent: *\nDisallow: /\nAllow: /tenant/\n",
        )

        classified = audit_minutes_robots.classify_row(row, robots, checked_at="2026-08-02")

        self.assertEqual(classified["crawl_status"], "enabled")
        self.assertEqual(classified["exclusion_reason"], "")
        self.assertEqual(classified["exclusion_detail"], "")
        self.assertEqual(classified["policy_fingerprint"], crawl_policy.policy_fingerprint(row))

    def test_enabled_registry_change_remains_operator_enabled(self) -> None:
        row = {
            "url": "https://example.test/old/",
            "system_type": "独自",
            "crawl_status": "enabled",
        }
        row["policy_fingerprint"] = crawl_policy.policy_fingerprint(row)
        row["url"] = "https://example.test/new/"

        effective = gijiroku_targets.effective_crawl_policy(row)

        self.assertEqual(effective["crawl_status"], "enabled")
        self.assertEqual(effective["exclusion_reason"], "")

    def test_stale_only_audit_enables_changed_row_and_requests_immediate_cycle(self) -> None:
        row = {
            "jis_code": "00000",
            "url": "https://example.test/old/",
            "system_type": "独自",
            "crawl_status": "review_required",
            "policy_checked_at": "2026-08-01",
        }
        row["policy_fingerprint"] = crawl_policy.policy_fingerprint(row)
        row["url"] = "https://example.test/new/"
        robots = audit_minutes_robots.RobotsResult(
            url="https://example.test/robots.txt",
            status_code=404,
            body="",
        )

        with (
            mock.patch.object(audit_minutes_robots, "file_digest", return_value="source-digest"),
            mock.patch.object(audit_minutes_robots, "read_rows", return_value=[row]),
            mock.patch.object(audit_minutes_robots, "fetch_robots", return_value=robots),
            mock.patch.object(audit_minutes_robots, "write_rows") as write_rows,
        ):
            summary = audit_minutes_robots.audit_registry(
                Path("registry.tsv"),
                write=True,
                stale_only=True,
                workers=1,
                cache_path=None,
            )

        self.assertEqual(summary.selected_rows, 1)
        self.assertTrue(summary.enabled_targets_changed)
        self.assertTrue(summary.wrote)
        write_rows.assert_called_once()

    def test_runtime_cache_restores_previous_audit_after_redeploy(self) -> None:
        source = {
            "jis_code": "00000",
            "url": "https://example.test/new/",
            "system_type": "独自",
            "crawl_status": "review_required",
            "policy_checked_at": "2026-08-01",
            "policy_fingerprint": "old-deployment-value",
        }
        current_fingerprint = crawl_policy.policy_fingerprint(source)
        cached = {
            "00000": {
                "crawl_status": "enabled",
                "exclusion_reason": "",
                "exclusion_detail": "",
                "policy_checked_at": "2026-08-02",
                "policy_fingerprint": current_fingerprint,
            }
        }

        with (
            mock.patch.object(audit_minutes_robots, "file_digest", return_value="source-digest"),
            mock.patch.object(audit_minutes_robots, "read_rows", return_value=[source]),
            mock.patch.object(audit_minutes_robots, "load_policy_cache", return_value=cached),
            mock.patch.object(audit_minutes_robots, "fetch_robots") as fetch_robots,
            mock.patch.object(audit_minutes_robots, "write_rows") as write_rows,
        ):
            summary = audit_minutes_robots.audit_registry(
                Path("registry.tsv"),
                write=True,
                stale_only=True,
                workers=1,
                cache_path=Path("cache.json"),
            )

        self.assertEqual(summary.selected_rows, 0)
        self.assertFalse(summary.enabled_targets_changed)
        self.assertTrue(summary.wrote)
        fetch_robots.assert_not_called()
        write_rows.assert_called_once()

    def test_enabled_override_skips_robots_and_requests_immediate_cycle(self) -> None:
        source = {
            "jis_code": "00000",
            "url": "https://example.test/new/",
            "system_type": "独自",
            "crawl_status": "enabled",
            "exclusion_reason": "robots_disallowed",
            "exclusion_detail": "old robots result",
            "policy_checked_at": "2026-08-01",
            "policy_fingerprint": "old-deployment-value",
        }
        cached = {
            "00000": {
                "crawl_status": "excluded",
                "exclusion_reason": "robots_disallowed",
                "exclusion_detail": "old robots result",
                "policy_checked_at": "2026-08-01",
                "policy_fingerprint": crawl_policy.policy_fingerprint(source),
            }
        }

        with (
            mock.patch.object(audit_minutes_robots, "file_digest", return_value="source-digest"),
            mock.patch.object(audit_minutes_robots, "read_rows", return_value=[source]),
            mock.patch.object(audit_minutes_robots, "load_policy_cache", return_value=cached),
            mock.patch.object(audit_minutes_robots, "fetch_robots") as fetch_robots,
            mock.patch.object(audit_minutes_robots, "write_rows") as write_rows,
            mock.patch.object(audit_minutes_robots, "write_policy_cache") as write_policy_cache,
        ):
            summary = audit_minutes_robots.audit_registry(
                Path("registry.tsv"),
                write=True,
                stale_only=True,
                workers=1,
                cache_path=Path("cache.json"),
            )

        self.assertEqual(summary.selected_rows, 0)
        self.assertTrue(summary.enabled_targets_changed)
        self.assertTrue(summary.wrote)
        fetch_robots.assert_not_called()
        write_rows.assert_called_once()
        write_policy_cache.assert_called_once()

    def test_initial_cache_seed_does_not_request_duplicate_cycle(self) -> None:
        source = {
            "jis_code": "00000",
            "url": "https://example.test/minutes/",
            "system_type": "独自",
            "crawl_status": "enabled",
            "exclusion_reason": "",
            "exclusion_detail": "",
            "policy_checked_at": "",
        }
        source["policy_fingerprint"] = crawl_policy.policy_fingerprint(source)

        with (
            mock.patch.object(audit_minutes_robots, "file_digest", return_value="source-digest"),
            mock.patch.object(audit_minutes_robots, "read_rows", return_value=[source]),
            mock.patch.object(audit_minutes_robots, "load_policy_cache", return_value={}),
            mock.patch.object(audit_minutes_robots, "fetch_robots") as fetch_robots,
            mock.patch.object(audit_minutes_robots, "write_rows") as write_rows,
            mock.patch.object(audit_minutes_robots, "write_policy_cache") as write_policy_cache,
        ):
            summary = audit_minutes_robots.audit_registry(
                Path("registry.tsv"),
                write=True,
                stale_only=True,
                workers=1,
                cache_path=Path("cache.json"),
            )

        self.assertEqual(summary.selected_rows, 0)
        self.assertFalse(summary.enabled_targets_changed)
        self.assertFalse(summary.wrote)
        fetch_robots.assert_not_called()
        write_rows.assert_not_called()
        write_policy_cache.assert_called_once()

    def test_legacy_rows_keep_backward_compatible_statuses(self) -> None:
        self.assertIn(gijiroku_targets.CRAWL_STATUS_ENABLED, gijiroku_targets.VALID_CRAWL_STATUSES)
        self.assertIn(gijiroku_targets.CRAWL_STATUS_UNRESOLVED, gijiroku_targets.VALID_CRAWL_STATUSES)

    def test_static_minutes_category_can_follow_cms_document_page(self) -> None:
        self.assertTrue(
            should_follow_related_minutes_page(
                "https://www.example.jp/gikai/kaigiroku/",
                "https://www.example.jp/docs/5338.html",
                "令和8年村議会会議録",
            )
        )

    def test_registered_review_required_target_is_not_scrapeable(self) -> None:
        review_target = {
            "url": "https://example.test/minutes/",
            "system_type": "独自",
            "crawl_status": gijiroku_targets.CRAWL_STATUS_REVIEW_REQUIRED,
            "exclusion_reason": "robots_unreachable",
            "exclusion_detail": "robots.txt / HTTP 403",
            "policy_checked_at": "2026-08-02",
            "policy_fingerprint": "fingerprint",
        }
        with (
            mock.patch.object(
                gijiroku_targets,
                "load_local_minutes_url_index",
                return_value={"00000": review_target},
            ),
            mock.patch.object(gijiroku_targets, "load_municipality_master_index", return_value={}),
            mock.patch.object(gijiroku_targets, "load_municipality_homepage_index", return_value={}),
        ):
            all_targets = gijiroku_targets.iter_gijiroku_targets()
            scrapeable = gijiroku_targets.iter_scrapeable_gijiroku_targets()

            self.assertIn("00000", {target["code"] for target in all_targets})
            self.assertNotIn("00000", {target["code"] for target in scrapeable})
            with self.assertRaises(gijiroku_targets.CrawlPolicyBlockedError):
                gijiroku_targets.load_gijiroku_target("00000")



class NonRobotsExclusionTest(unittest.TestCase):
    """robots を根拠にしない設定でも、robots 由来でない除外は解除しない。

    動画しか公開していない（video_only）自治体が 7 件ある。fingerprint が
    古くなると再監査され、一律 enabled に戻して取れないものを取りに行く。
    """

    ROW = {
        "jis_code": "06367",
        "url": "https://smart.discussvision.net/smart/tenant/example",
        "system_type": "discussvision",
        "policy_checked_at": "20260101",
        "policy_fingerprint": "stale",
    }

    def _classify(self, reason: str) -> dict:
        row = dict(
            self.ROW,
            crawl_status="excluded" if reason else "enabled",
            exclusion_reason=reason,
            exclusion_detail="d" if reason else "",
        )
        return audit_minutes_robots.classify_row(row, None, checked_at="20260830")

    def test_video_only_stays_excluded(self):
        result = self._classify("video_only")
        self.assertEqual(result["crawl_status"], "excluded")
        self.assertEqual(result["exclusion_reason"], "video_only")

    def test_robots_exclusion_is_lifted(self):
        result = self._classify("robots_disallowed")
        self.assertEqual(result["crawl_status"], "enabled")
        self.assertEqual(result["exclusion_reason"], "")

    def test_enabled_row_stays_enabled(self):
        result = self._classify("")
        self.assertEqual(result["crawl_status"], "enabled")

if __name__ == "__main__":
    unittest.main()
