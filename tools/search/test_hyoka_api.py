"""実際のPHP APIをローカルHTTP応答で試す。OpenSearchには接続しない。"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest


ROOT = Path(__file__).resolve().parents[2]
PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP CLI が無い")


@pytest.fixture
def api(tmp_path):
    # PHPの自治体名解決とクエリ前処理はキャッシュを書く。ソースを小さな一時配置へ
    # コピーし、既存config・work・background_tasksには触れない。
    for name in (
        "app/api/search.php", "app/api/document.php", "lib/opensearch_search.php",
        "lib/japanese_search.php", "lib/municipalities.php", "lib/hyoka_display.php",
        "lib/python/japanese_search_tokenizer.py",
        "data/municipalities/municipality_master.tsv",
        "data/municipalities/assembly_minutes_system_urls.tsv", "data/municipalities/reiki_system_urls.tsv",
    ):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    requests = []
    source = {
        "doc_type": "hyoka", "slug": "14130-kawasaki-shi",
        "municipality_code": "14130", "pref_code": "14", "pref_name": "神奈川県",
        "municipality_name": "川崎市", "title": "評価表", "body": "保存された本文",
        "fiscal_year": 2025, "year_label": "令和7年度", "kind": "project_evaluation",
        "link_text": "事業評価表", "heading": "評価結果",
        "source_url": "https://example.invalid/report.pdf",
        "source_page_url": "https://example.invalid/evaluation/",
        "source_page_link": "https://example.invalid/report.pdf#page=3", "page": 3,
        "sha256": "a" * 64, "fetched_at": "2026-10-01T00:00:00+09:00",
        "status": "gone", "text_status": "ok", "page_split_reason": "form_feed",
    }
    response = {"status": 200, "hits": [{"_id": "hyoka:14130:test:p3", "_source": source}]}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            self.send_response(response["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "hits": {"hits": response["hits"], "total": {"value": len(response["hits"]), "relation": "eq"}},
                "took": 1,
            }).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(endpoint="search", **params):
        env = os.environ.copy()
        env.update(OPENSEARCH_URL=f"http://127.0.0.1:{server.server_port}",
                   OPENSEARCH_USER="", OPENSEARCH_PASSWORD="", MIYABE_HYOKA_ALIAS="test-hyoka")
        script = (
            '$_GET = json_decode(base64_decode($argv[1]), true);'
            'register_shutdown_function(function(){fwrite(STDERR, "STATUS=".(http_response_code() ?: 200));});'
            'require $argv[2];'
        )
        result = subprocess.run(
            [PHP, "-r", script, base64.b64encode(json.dumps(params).encode()).decode(),
             str(tmp_path / "app" / "api" / f"{endpoint}.php")],
            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return int(result.stderr.rsplit("STATUS=", 1)[1]), json.loads(result.stdout)

    yield call, requests, response, source
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_hyoka_filters_and_source_document_roundtrip(api):
    call, requests, response, source = api
    status, found = call(q="評価", doc_type="hyoka", fiscal_year="2025", kind="project_evaluation",
                         slug="14130-kawasaki-shi", municipality_code="14130", pref_code="14")
    assert status == 200
    path, body = requests[-1]
    assert path == "/test-hyoka/_search"
    filters = body["query"]["bool"]["filter"]
    for field, value in {"doc_type": "hyoka", "fiscal_year": 2025, "kind": "project_evaluation",
                         "slug": "14130-kawasaki-shi", "municipality_code": "14130", "pref_code": "14"}.items():
        assert {"term": {field: value}} in filters
    assert list(body["sort"][0]) == ["fiscal_year"]
    item = found["items"][0]
    assert item["status"] == "gone"
    assert item["page"] == 3
    assert "doc_type=hyoka" in item["api_document_url"]
    assert "doc_type=hyoka" in item["detail_url"]
    assert item["source_url"] == source["source_url"]
    status, detail = call("document", id=item["id"], doc_type="hyoka")
    assert status == 200
    assert detail["document"]["body"] == source["body"]
    for field in ("fiscal_year", "page", "source_page_url", "source_page_link", "sha256", "fetched_at", "status"):
        assert detail["document"][field] == source[field]
    assert {"ids": {"values": [item["id"]]}} in requests[-1][1]["query"]["bool"]["filter"]


@pytest.mark.parametrize("params", [{"fiscal_year": "令和7"}, {"fiscal_year": "2025x"}, {"kind": "report"}])
def test_hyoka_invalid_filter_is_422_before_http(api, params):
    call, requests, _, _ = api
    status, payload = call(q="評価", doc_type="hyoka", **params)
    assert status == 422 and payload["status"] == "query_error"
    assert requests == []


def test_hyoka_whole_document_and_missing_year_are_null(api):
    call, _, _, source = api
    source.pop("page")
    source.pop("fiscal_year")
    source["page_split_reason"] = "saved_text_no_page_boundaries"
    source["source_page_link"] = source["source_url"]
    status, payload = call("document", id="hyoka:whole", doc_type="hyoka")
    assert status == 200
    assert payload["document"]["page"] is None
    assert payload["document"]["fiscal_year"] is None
    assert payload["document"]["page_split_reason"] == "saved_text_no_page_boundaries"


@pytest.mark.parametrize("doc_type", ["minutes", "reiki"])
def test_existing_types_keep_fields_and_date_filter(api, doc_type):
    call, requests, _, source = api
    source["doc_type"] = doc_type
    status, payload = call(q="評価", doc_type=doc_type, start_year="2020", end_year="2025",
                           fiscal_year="bad", kind="bad")
    assert status == 200
    path, body = requests[-1]
    assert doc_type in path
    assert {"range": {"sort_date": {"gte": "2020-01-01", "lte": "2025-12-31"}}} in body["query"]["bool"]["filter"]
    assert list(body["sort"][0]) == ["sort_date"]
    for field in ("fiscal_year", "kind", "page", "status", "sha256"):
        assert field not in payload["items"][0]


def test_hyoka_empty_not_found_and_unavailable(api):
    call, requests, response, _ = api
    status, payload = call(q="", doc_type="hyoka")
    assert status == 422 and requests == []
    response["hits"] = []
    status, payload = call(q="評価", doc_type="hyoka")
    assert status == 200 and payload["total"] == 0 and payload["items"] == []
    status, payload = call("document", id="missing", doc_type="hyoka")
    assert status == 404
    response["status"] = 503
    status, payload = call(q="評価", doc_type="hyoka")
    assert status == 503 and payload["status"] == "opensearch_error"
