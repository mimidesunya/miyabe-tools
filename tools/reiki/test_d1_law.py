#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRAPER_DIR = Path(__file__).resolve().parent / "scrapers"
sys.path.insert(0, str(SCRAPER_DIR))

import d1_law  # noqa: E402


class D1LawBaseUrlTest(unittest.TestCase):
    def test_discovers_static_reiki_root_from_landing_page(self) -> None:
        source_url = "https://en3-jg.d1-law.com/kagawa-ken/index.htm"
        source_html = '<frame src="d1w_reiki/mokuji_bunya.html">'

        self.assertEqual(
            d1_law.discover_d1_law_base_url(source_url, source_html),
            "https://en3-jg.d1-law.com/kagawa-ken/d1w_reiki/",
        )

    def test_keeps_direct_static_reiki_url(self) -> None:
        source_url = "https://example.d1-law.com/city/d1w_reiki/reiki.html"

        self.assertEqual(
            d1_law.discover_d1_law_base_url(source_url, "<html></html>"),
            "https://example.d1-law.com/city/d1w_reiki/",
        )


class D1LawParserGenerationTest(unittest.TestCase):
    def build_complete_plan(self, root: Path, parser_version: int) -> dict:
        source_dir = root / "source"
        html_dir = root / "html"
        markdown_dir = root / "markdown"
        source_dir.mkdir()
        html_dir.mkdir()
        markdown_dir.mkdir()

        filename = "H123456789_j.html"
        source_body = b"<html><body>saved source</body></html>"
        (source_dir / filename).write_bytes(source_body)
        (html_dir / filename).write_text("<main>old clean html</main>", encoding="utf-8")
        (markdown_dir / "H123456789_j.md").write_text("old markdown", encoding="utf-8")
        plans, _ = d1_law.build_source_plan(
            source_items=["H123456789"],
            base_url="https://example.test/d1w_reiki/",
            source_dir=source_dir,
            html_dir=html_dir,
            markdown_dir=markdown_dir,
            opensearch_session=None,
            previous_manifest_by_source={
                filename: {
                    "source_file": filename,
                    "source_sha256": hashlib.sha256(source_body).hexdigest(),
                    "parser_version": parser_version,
                }
            },
        )
        return plans[0]

    def test_outdated_parser_rebuilds_saved_source_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.build_complete_plan(Path(temporary), d1_law.PARSER_VERSION - 1)

            work_mode = d1_law.assign_work_mode(
                [plan],
                force=False,
                check_updates=True,
                catalog_changed=False,
            )

            self.assertTrue(plan["parser_outdated"])
            self.assertTrue(plan["should_work"])
            self.assertFalse(plan["should_fetch"])
            self.assertEqual(work_mode["parser_outdated_count"], 1)
            self.assertEqual(work_mode["parser_reparse_only_count"], 1)
            with mock.patch.object(d1_law, "download_file") as download:
                downloaded, source_path, source_hash, _ = d1_law.fetch_source_for_plan(
                    plan,
                    force=False,
                    update_mode=False,
                )
            download.assert_not_called()
            self.assertFalse(downloaded)
            self.assertEqual(source_path, plan["source_file_path"])
            self.assertEqual(source_hash, plan["previous_manifest"]["source_sha256"])

    def test_current_parser_and_unchanged_catalog_need_no_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.build_complete_plan(Path(temporary), d1_law.PARSER_VERSION)

            work_mode = d1_law.assign_work_mode(
                [plan],
                force=False,
                check_updates=True,
                catalog_changed=False,
            )

            self.assertFalse(plan["parser_outdated"])
            self.assertFalse(plan["should_fetch"])
            self.assertFalse(plan["should_work"])
            self.assertEqual(work_mode["work_count"], 0)

    def test_outdated_parser_forces_reparse_and_advances_generation_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.build_complete_plan(Path(temporary), d1_law.PARSER_VERSION - 1)
            with mock.patch.object(d1_law.d1_parser, "process_file", return_value=True) as process:
                parse_required, parse_succeeded = d1_law.parse_source_for_plan(
                    plan,
                    plan["source_file_path"],
                    downloaded=False,
                    force=False,
                    markdown_dir=Path(temporary) / "markdown",
                    html_dir=Path(temporary) / "html",
                    base_url="https://example.test/d1w_reiki/",
                    images_dir=Path(temporary) / "images",
                    image_public_url="/reiki/example/images",
                )

            self.assertTrue(parse_required)
            self.assertTrue(parse_succeeded)
            self.assertTrue(process.call_args.kwargs["force"])
            self.assertEqual(
                d1_law.parser_version_for_manifest(
                    plan["previous_manifest"],
                    parse_required=parse_required,
                    parse_succeeded=parse_succeeded,
                ),
                d1_law.PARSER_VERSION,
            )

    def test_failed_reparse_does_not_mark_manifest_as_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = self.build_complete_plan(Path(temporary), d1_law.PARSER_VERSION - 1)
            with mock.patch.object(d1_law.d1_parser, "process_file", return_value=False):
                parse_required, parse_succeeded = d1_law.parse_source_for_plan(
                    plan,
                    plan["source_file_path"],
                    downloaded=False,
                    force=False,
                    markdown_dir=Path(temporary) / "markdown",
                    html_dir=Path(temporary) / "html",
                    base_url="https://example.test/d1w_reiki/",
                    images_dir=Path(temporary) / "images",
                    image_public_url="/reiki/example/images",
                )

            self.assertTrue(parse_required)
            self.assertFalse(parse_succeeded)
            self.assertEqual(
                d1_law.parser_version_for_manifest(
                    plan["previous_manifest"],
                    parse_required=parse_required,
                    parse_succeeded=parse_succeeded,
                ),
                d1_law.PARSER_VERSION - 1,
            )
        # source 更新後の変換に失敗した場合、前回の現行世代を残すと
        # 新しい source と古い成果物の組合せを次周期が見逃してしまう。
        self.assertNotEqual(
            d1_law.parser_version_for_manifest(
                {"parser_version": d1_law.PARSER_VERSION},
                parse_required=True,
                parse_succeeded=False,
            ),
            d1_law.PARSER_VERSION,
        )


if __name__ == "__main__":
    unittest.main()


class D1LawGoneAtSourceTest(unittest.TestCase):
    """取得元が本文を出さなくなった分を、取り損ねと分けて数える。

    一緒に数えると、何度巡回しても消えない失敗が残り続け、直せる失敗が
    その中に埋もれる。d1-law では 80 自治体がこの形だった。
    """

    def setUp(self) -> None:
        d1_law.DOWNLOAD_FAILURES.clear()
        d1_law.DOWNLOAD_MISSING.clear()

    tearDown = setUp

    @staticmethod
    def http_error(status_code: int) -> Exception:
        import requests

        response = requests.Response()
        response.status_code = status_code
        return requests.HTTPError(f"HTTP {status_code}", response=response)

    def test_reads_status_code_from_error(self) -> None:
        self.assertEqual(d1_law._gone_status_code(self.http_error(404)), 404)
        self.assertEqual(d1_law._gone_status_code(self.http_error(410)), 410)
        # 5xx と 403 は取り損ねなので、再試行の対象に残す。
        self.assertEqual(d1_law._gone_status_code(self.http_error(503)), 0)
        self.assertEqual(d1_law._gone_status_code(self.http_error(403)), 0)
        self.assertEqual(d1_law._gone_status_code(RuntimeError("connection reset")), 0)

    def download_with_error(self, error: Exception) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = mock.Mock()
            session.get.side_effect = error
            d1_law.download_file(
                "https://example.test/a/a_j.html",
                Path(directory) / "a_j.html",
                session=session,
            )

    def test_missing_body_is_not_a_failure(self) -> None:
        self.download_with_error(self.http_error(404))
        self.assertEqual(d1_law.DOWNLOAD_FAILURES, [])
        self.assertEqual(d1_law.DOWNLOAD_MISSING, ["https://example.test/a/a_j.html"])

    def test_server_error_stays_a_failure(self) -> None:
        self.download_with_error(self.http_error(503))
        self.assertEqual(d1_law.DOWNLOAD_FAILURES, ["https://example.test/a/a_j.html"])
        self.assertEqual(d1_law.DOWNLOAD_MISSING, [])

    def test_forgetting_clears_both_lists(self) -> None:
        url = "https://example.test/a/a_j.html"
        d1_law.DOWNLOAD_FAILURES.append(url)
        d1_law.DOWNLOAD_MISSING.append(url)
        d1_law._forget_download_failure(url)
        self.assertEqual(d1_law.DOWNLOAD_FAILURES, [])
        self.assertEqual(d1_law.DOWNLOAD_MISSING, [])
