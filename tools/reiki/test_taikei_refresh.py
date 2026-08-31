#!/usr/bin/env python3
"""taikei / g-reiki の保存済み個票を周期的に自己修復できることを確かめる。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PHP_PATH = Path(
    os.environ.get(
        "TAIKEI_PHP_PATH",
        Path(__file__).resolve().parent / "scrapers" / "taikei.php",
    )
)
PHP = shutil.which("php")


class _ConditionalHandler(BaseHTTPRequestHandler):
    received_headers: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler の API 名
        type(self).received_headers = {
            "if-none-match": self.headers.get("If-None-Match", ""),
            "if-modified-since": self.headers.get("If-Modified-Since", ""),
        }
        if (
            type(self).received_headers["if-none-match"] == '"fixture-v1"'
            and type(self).received_headers["if-modified-since"]
            == "Wed, 01 Jan 2025 00:00:00 GMT"
        ):
            self.send_response(304)
            self.send_header("ETag", '"fixture-v1"')
            self.end_headers()
            return

        body = b"fixture body"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


@unittest.skipUnless(PHP, "PHP CLI が必要")
class TaikeiRefreshTest(unittest.TestCase):
    def run_php(self, body: str, payload: dict[str, object] | None = None) -> object:
        source = PHP_PATH.read_text(encoding="utf-8")
        marker = "\nmain($argv);\n"
        self.assertIn(marker, source)
        # 本番 main を起動せず、同じ関数本体を一時スクリプトから直接検証する。
        source = source.replace(marker, "\n", 1)
        harness = f"""
$input = json_decode(base64_decode($argv[1]), true, 512, JSON_THROW_ON_ERROR);
$result = (static function (array $input): mixed {{
{body}
}})($input);
echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
"""
        encoded = base64.b64encode(
            json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "taikei_harness.php"
            script.write_text(source + harness, encoding="utf-8")
            completed = subprocess.run(
                [str(PHP), str(script), encoded],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"PHP harness failed:\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return json.loads(completed.stdout)

    def test_parser_generation_rebuilds_from_saved_source_without_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            html_dir = root / "html"
            markdown_dir = root / "markdown"
            source_dir.mkdir()
            html_dir.mkdir()
            markdown_dir.mkdir()

            source_name = "g123RG00000001_j.html"
            (source_dir / source_name).write_text(
                """<html><body>
<p class="title-irregular">○fixture revised</p>
<div id="primaryInner2"><div class="eline">第一条 保存済み本文</div></div>
</body></html>""",
                encoding="utf-8",
            )
            (html_dir / source_name).write_text("old clean html", encoding="utf-8")
            (markdown_dir / "g123RG00000001_j.md").write_text(
                "old markdown", encoding="utf-8"
            )
            result = self.run_php(
                """
$record = [
    'code' => 'g123RG00000001',
    'title' => 'fixture',
    'date' => '令和7年1月1日',
    'number' => '条例第1号',
    'detail_url' => 'https://example.test/reiki/reiki_honbun/g123RG00000001.html',
];
$previous = [[
    'source_file' => 'g123RG00000001_j.html',
    'parser_version' => 'old-generation',
    'last_validated_at' => '2026-08-30T00:00:00+00:00',
    'title' => 'fixture',
    'date' => '令和7年1月1日',
    'number' => '条例第1号',
]];
$state = build_source_plan(
    [$record],
    (string)$input['source_dir'],
    (string)$input['html_dir'],
    (string)$input['markdown_dir'],
    index_manifest_by_source($previous)
);
$plans = $state['plans'];
$mode = assign_work_mode($plans, false, true, false);
$parsed = parse_and_store_taikei_source(
    $record,
    (string)$plans[0]['existing_source_path'],
    (string)$plans[0]['html_path'],
    (string)$plans[0]['markdown_path']
);
return [
    'parser_version' => defined('TAIKEI_PARSER_VERSION') ? TAIKEI_PARSER_VERSION : null,
    'needs_parser_refresh' => $plans[0]['needs_parser_refresh'] ?? null,
    'should_work' => $plans[0]['should_work'] ?? null,
    'should_fetch' => $plans[0]['should_fetch'] ?? null,
    'work_count' => $mode['work_count'] ?? null,
    'parsed_title' => $parsed['title'] ?? null,
    'clean_html' => read_text_file_auto((string)$plans[0]['html_path']),
    'markdown' => read_text_file_auto((string)existing_path((string)$plans[0]['markdown_path'])),
];
""",
                {
                    "source_dir": str(source_dir),
                    "html_dir": str(html_dir),
                    "markdown_dir": str(markdown_dir),
                },
            )

        self.assertIsInstance(result["parser_version"], int)
        self.assertGreater(result["parser_version"], 0)
        self.assertTrue(result["needs_parser_refresh"])
        self.assertTrue(result["should_work"])
        self.assertFalse(result["should_fetch"])
        self.assertEqual(result["work_count"], 1)
        self.assertEqual(result["parsed_title"], "fixture revised")
        self.assertIn("第一条 保存済み本文", result["clean_html"])
        self.assertIn("# fixture revised", result["markdown"])

    def test_catalog_unchanged_still_validates_stale_and_unseen_items(self) -> None:
        result = self.run_php(
            """
$now = strtotime('2026-08-31T00:00:00+00:00');
$plans = [
    [
        'record' => ['code' => 'stale'],
        'is_incomplete' => false,
        'needs_source' => false,
        'needs_parse' => false,
        'listed_metadata_changed' => false,
        'validation_due' => taikei_validation_due(
            ['last_validated_at' => '2026-05-01T00:00:00+00:00'],
            $now
        ),
    ],
    [
        'record' => ['code' => 'unseen'],
        'is_incomplete' => false,
        'needs_source' => false,
        'needs_parse' => false,
        'listed_metadata_changed' => false,
        'validation_due' => taikei_validation_due([], $now),
    ],
    [
        'record' => ['code' => 'fresh'],
        'is_incomplete' => false,
        'needs_source' => false,
        'needs_parse' => false,
        'listed_metadata_changed' => false,
        'validation_due' => taikei_validation_due(
            ['last_validated_at' => '2026-08-30T00:00:00+00:00'],
            $now
        ),
    ],
];
$mode = assign_work_mode($plans, false, true, false);
return [
    'interval_days' => intdiv(TAIKEI_VALIDATION_INTERVAL_SECONDS, 86400),
    'update_mode' => $mode['update_mode'] ?? null,
    'plans' => array_map(static fn(array $plan): array => [
        'code' => $plan['record']['code'],
        'should_validate' => $plan['should_validate'] ?? null,
        'should_fetch' => $plan['should_fetch'] ?? null,
    ], $plans),
];
"""
        )

        self.assertEqual(result["interval_days"], 90)
        self.assertTrue(result["update_mode"])
        by_code = {item["code"]: item for item in result["plans"]}
        self.assertTrue(by_code["stale"]["should_validate"])
        self.assertTrue(by_code["stale"]["should_fetch"])
        self.assertTrue(by_code["unseen"]["should_validate"])
        self.assertFalse(by_code["fresh"]["should_validate"])
        self.assertFalse(by_code["fresh"]["should_fetch"])

    def test_listing_change_is_immediate_and_prioritized(self) -> None:
        result = self.run_php(
            """
$plans = [
    [
        'record' => ['code' => 'stale'],
        'is_incomplete' => false,
        'needs_source' => false,
        'needs_parse' => false,
        'listed_metadata_changed' => false,
        'validation_due' => true,
    ],
    [
        'record' => ['code' => 'listed-change'],
        'is_incomplete' => false,
        'needs_source' => false,
        'needs_parse' => false,
        'listed_metadata_changed' => true,
        'validation_due' => false,
    ],
    [
        'record' => ['code' => 'fresh'],
        'is_incomplete' => false,
        'needs_source' => false,
        'needs_parse' => false,
        'listed_metadata_changed' => false,
        'validation_due' => false,
    ],
];
$plans = prioritize_source_plans($plans, true);
assign_work_mode($plans, false, true, false);
return array_map(static fn(array $plan): array => [
    'code' => $plan['record']['code'],
    'should_validate' => $plan['should_validate'] ?? null,
], $plans);
"""
        )

        self.assertEqual([item["code"] for item in result], ["listed-change", "stale", "fresh"])
        self.assertTrue(result[0]["should_validate"])

    def test_manifest_generation_does_not_advance_validation_on_parser_only_work(self) -> None:
        result = self.run_php(
            """
$old = [
    'parser_version' => 1,
    'last_validated_at' => '2026-01-02T03:04:05+00:00',
    'source_etag' => '"old"',
];
$parserOnly = finalize_taikei_manifest($old, str_repeat('a', 64), null, null);
$validated = finalize_taikei_manifest(
    $old,
    str_repeat('b', 64),
    [
        'etag' => '"new"',
        'last_modified' => 'Wed, 01 Jan 2025 00:00:00 GMT',
    ],
    '2026-08-31T00:00:00+00:00'
);
return ['parser_only' => $parserOnly, 'validated' => $validated];
"""
        )

        parser_only = result["parser_only"]
        validated = result["validated"]
        self.assertEqual(parser_only["last_validated_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(parser_only["parser_version"], result["validated"]["parser_version"])
        self.assertEqual(parser_only["source_sha256"], "a" * 64)
        self.assertEqual(validated["last_validated_at"], "2026-08-31T00:00:00+00:00")
        self.assertEqual(validated["source_sha256"], "b" * 64)
        self.assertEqual(validated["source_etag"], '"new"')
        self.assertEqual(
            validated["source_last_modified"], "Wed, 01 Jan 2025 00:00:00 GMT"
        )

    def test_hash_fallback_detects_change_and_rejects_empty_200(self) -> None:
        result = self.run_php(
            """
$saved = '<html><body>same ordinance</body></html>';
$savedHash = sha256_string($saved);
$emptyError = '';
try {
    taikei_source_changed($savedHash, "  \r\n\t  ", false);
} catch (RuntimeException $exception) {
    $emptyError = $exception->getMessage();
}
return [
    'same' => taikei_source_changed($savedHash, $saved, false),
    'changed' => taikei_source_changed(
        $savedHash,
        '<html><body>amended ordinance</body></html>',
        false
    ),
    'forced' => taikei_source_changed($savedHash, $saved, true),
    'empty_error' => $emptyError,
];
"""
        )

        self.assertFalse(result["same"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["forced"])
        self.assertIn("empty ordinance response", result["empty_error"])

    def test_conditional_fetch_sends_validators_and_accepts_304(self) -> None:
        _ConditionalHandler.received_headers = {}
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ConditionalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.run_php(
                """
$result = fetch_url_response((string)$input['url'], [
    'If-None-Match: "fixture-v1"',
    'If-Modified-Since: Wed, 01 Jan 2025 00:00:00 GMT',
]);
return $result;
""",
                {"url": f"http://127.0.0.1:{server.server_port}/ordinance"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(_ConditionalHandler.received_headers["if-none-match"], '"fixture-v1"')
        self.assertEqual(
            _ConditionalHandler.received_headers["if-modified-since"],
            "Wed, 01 Jan 2025 00:00:00 GMT",
        )
        self.assertEqual(result["status"], 304)
        self.assertTrue(result["not_modified"])
        self.assertIsNone(result["body"])
        self.assertEqual(result["etag"], '"fixture-v1"')


if __name__ == "__main__":
    unittest.main()
