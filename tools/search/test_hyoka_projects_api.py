"""事務事業評価の事業ごとの API（PHP）をローカルの偽の OpenSearch で試す。本物の索引には繋がない。"""
from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP CLI が無い")

SLUG = "14130-kawasaki-shi"
DOC = "a" * 16
PROJECT_ID = f"hyoka-project:{SLUG}:{DOC}:{DOC}-1"


@pytest.fixture
def api(tmp_path):
    for name in (
        "app/api/hyoka-projects.php", "app/api/hyoka-aggregate.php", "app/api/hyoka-project.php",
        "lib/hyoka_projects.php", "lib/opensearch_search.php", "lib/japanese_search.php", "lib/municipalities.php",
        "lib/hyoka_display.php", "lib/python/japanese_search_tokenizer.py",
        "data/municipalities/municipality_master.tsv",
        "data/municipalities/assembly_minutes_system_urls.tsv", "data/municipalities/reiki_system_urls.tsv",
    ):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    display = tmp_path / "display"
    (display / "records" / SLUG).mkdir(parents=True)
    (display / "records" / SLUG / f"{DOC}.json").write_text(json.dumps({
        "slug": SLUG, "doc_id": DOC, "source_url": "https://example.invalid/4-8.pdf", "records": [{
            "id": f"{DOC}-1", "layout": "form_reader", "page": 21, "pages": [21, 22],
            "fields": [{"key": "project_name", "name": "事業名", "label": "", "text": "市民ミュージアム管理運営事業"}],
            "raw_fields": [{"label": "対象", "text": "市民"}],
            "amounts": {"unit": "千円", "columns": [{"year": 2024, "stage": "決算"}],
                        "rows": [{"label": "総コスト", "kind": "総費用（人件費を含む）", "unit": "", "values": ["1,008,569"]}]},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (display / "sheets" / SLUG).mkdir(parents=True)
    with gzip.open(display / "sheets" / SLUG / f"{DOC}.json.gz", "wt", encoding="utf-8") as stream:
        # 本物のシートの控えと同じく詰めて書く（頁の行を「{"page":21」で見分けるため）。
        stream.write(json.dumps({"doc_id": DOC, "pages": [21, 22]}, separators=(",", ":")) + "\n")
        for page, text in ((21, "事務事業名"), (22, "今後の方向性")):
            stream.write(json.dumps({"page": page, "tables": [{"top": 1, "cells": [
                {"r": 0, "c": 0, "rs": 1, "cs": 1, "text": text}, {"r": 0, "c": 1, "rs": 1, "cs": 1, "text": "値"},
                {"r": 1, "c": 0, "rs": 1, "cs": 2, "text": ""}]}], "lines": [{"y": 0, "x": 0, "text": "令和７年度 事務事業評価シート"}]},
                ensure_ascii=False, separators=(",", ":")) + "\n")

    requests = []
    source = {
        "project_id": PROJECT_ID, "record_id": f"{DOC}-1", "doc_id": DOC, "document_id": f"hyoka:{SLUG}:{DOC}",
        "slug": SLUG, "municipality_name": "川崎市", "pref_name": "神奈川県", "project_name": "市民ミュージアム管理運営事業",
        "department": "市民文化局", "evaluation_year": 2024, "cost_yen": 1_008_569_000, "cost_year": 2024, "cost_stage": "settlement",
        "cost_scope": "total", "cost_raw": "1,008,569", "cost_unit": "千円", "direction": "improve", "aspects": ["成果:b"],
        "indicators": [{"name": "展覧会等の開催回数", "kind": "activity", "target": "4", "actual": "10", "ratio": 2.5}],
        "amounts": [{"year": 2024, "stage": "settlement", "scope": "total", "label": "総コスト", "raw": "1,008,569", "unit": "千円",
                     "yen": 1_008_569_000, "best": True, "stage_best": True}],
    }
    response = {"status": 200, "body": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            body = response["body"] if response["body"] is not None else {
                "took": 2, "hits": {"total": {"value": 1, "relation": "eq"}, "hits": [{"_id": PROJECT_ID, "_score": 1.5, "_source": source}]}}
            self.send_response(response["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body, ensure_ascii=False).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(endpoint, **params):
        env = os.environ.copy()
        env.update(OPENSEARCH_URL=f"http://127.0.0.1:{server.server_port}", OPENSEARCH_USER="", OPENSEARCH_PASSWORD="",
                   MIYABE_HYOKA_PROJECTS_ALIAS="test-projects", MIYABE_HYOKA_DISPLAY_DIR=str(display))
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

    yield call, requests, response
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_search_filters_sort_and_items(api):
    call, requests, _ = api
    status, payload = call("hyoka-projects", q="ミュージアム", slug=SLUG, pref_code="14", evaluation_year="2024",
                           direction="improve,expand", rating="4", aspect="成果: c", method="program", has_amounts="1",
                           min_cost="100000000", below_target="1", min_cost_change_ratio="1.5", department="市民文化",
                           flag="outcome_below_half,cost_up_below_target", sort="cost_change_desc", page="2", per_page="5")
    assert status == 200, payload
    path, body = requests[-1]
    assert path == "/test-projects/_search"
    assert (body["from"], body["size"]) == (5, 5)
    scored = body["query"]["function_score"]
    assert {"filter": {"term": {"document_kind": "project_evaluation"}}, "weight": 2} in scored["functions"]
    assert scored["boost_mode"] == "multiply"
    filters = scored["query"]["bool"]["filter"]
    for clause in ({"term": {"slug": SLUG}}, {"term": {"pref_code": "14"}}, {"term": {"evaluation_year": 2024}},
                   {"terms": {"direction": ["improve", "expand"]}}, {"terms": {"rating": ["4"]}}, {"terms": {"aspects": ["成果:c"]}},
                   {"terms": {"method": ["program"]}}, {"term": {"has_amounts": True}}, {"range": {"cost_yen": {"gte": 100000000}}},
                   {"range": {"outcome_ratio_min": {"lte": 0.9999}}}, {"range": {"cost_change_ratio": {"gte": 1.5}}},
                   {"term": {"flags": "outcome_below_half"}}, {"term": {"flags": "cost_up_below_target"}}):
        assert clause in filters, clause
    assert scored["query"]["bool"]["must"][0]["simple_query_string"]["query"] == "ミュージアム"
    assert list(body["sort"][0]) == ["cost_change_ratio"]
    assert "amounts" not in body["_source"]
    item = payload["items"][0]
    assert item["id"] == PROJECT_ID and item["cost_yen"] == 1_008_569_000
    assert item["api_project_url"].startswith("/api/hyoka-project.php?id=")
    assert "doc_type=hyoka" in item["detail_url"]


def test_search_without_query_lists_by_cost(api):
    call, requests, _ = api
    status, _ = call("hyoka-projects")
    assert status == 200
    body = requests[-1][1]
    assert body["query"] == {"match_all": {}}
    assert list(body["sort"][0]) == ["cost_yen"] and body["sort"][0]["cost_yen"]["order"] == "desc"


def test_search_form_2_filters(api):
    call, requests, _ = api
    status, payload = call("hyoka-projects", policy="市民自治 5-2", achievement="below,on_target", overall="low", is_main="1")
    assert status == 200, payload
    filters = requests[-1][1]["query"]["bool"]["filter"]
    for clause in ({"match": {"policy_names": {"query": "市民自治 5-2", "operator": "and"}}},
                   {"terms": {"achievement": ["below", "on_target"]}}, {"terms": {"overall": ["low"]}}, {"term": {"is_main": True}}):
        assert clause in filters, clause


def test_aggregate_groups_by_policy_and_variant(api):
    call, requests, response = api
    response["body"] = {"hits": {"total": {"value": 0}}, "aggregations": {"facts": {"picked": {"doc_count": 0, "sum": {"value": 0},
                                                                                               "municipalities": {"value": 0}}}}}
    for group_by, field in (("policy_top", "amounts.policy_top"), ("policy_leaf", "amounts.policy_leaf"), ("variant", "amounts.variant_base")):
        status, payload = call("hyoka-aggregate", year="2024", group_by=group_by, stage="initial_budget")
        assert status == 200, payload
        picked = requests[-1][1]["aggs"]["facts"]["aggs"]["picked"]
        assert picked["aggs"]["groups"]["terms"]["field"] == field
        assert {"term": {"amounts.stage": "initial_budget"}} in picked["filter"]["bool"]["filter"]


@pytest.mark.parametrize("params", [{"direction": "bigger"}, {"flag": "wasteful"}, {"min_cost": "-1"}, {"evaluation_year": "令和6"},
                                    {"sort": "random"}, {"per_page": "500"}, {"slug": "../x"}, {"max_outcome_ratio": "x"},
                                    {"achievement": "good"}, {"overall": "A"}, {"policy": "x" * 101}])
def test_search_rejects_bad_params_before_http(api, params):
    call, requests, _ = api
    status, payload = call("hyoka-projects", **params)
    assert status == 422 and payload["status"] == "query_error"
    assert requests == []


def test_aggregate_body_and_groups(api):
    call, requests, response = api
    response["body"] = {"took": 3, "hits": {"total": {"value": 40, "relation": "eq"}}, "aggregations": {"facts": {"picked": {
        "doc_count": 3, "sum": {"value": 3.5e9}, "municipalities": {"value": 2},
        "groups": {"buckets": [{"key": SLUG, "doc_count": 2, "sum": {"value": 3e9}, "label": {"buckets": [{"key": "川崎市"}]}}]},
        "top": {"hits": {"hits": [{"_id": PROJECT_ID, "_source": {"project_id": PROJECT_ID, "project_name": "市民ミュージアム管理運営事業",
                                                                  "municipality_name": "川崎市", "year": 2024, "stage": "settlement",
                                                                  "raw": "1,008,569", "unit": "千円", "yen": 1008569000}}]}},
    }}}}
    status, payload = call("hyoka-aggregate", year="2024", group_by="municipality", pref_code="14", direction="improve", top="3")
    assert status == 200, payload
    body = requests[-1][1]
    assert body["size"] == 0
    picked = body["aggs"]["facts"]["aggs"]["picked"]
    assert {"term": {"amounts.best": True}} in picked["filter"]["bool"]["filter"]
    assert {"term": {"amounts.scope": "total"}} in picked["filter"]["bool"]["filter"]
    assert {"term": {"amounts.year": 2024}} in picked["filter"]["bool"]["filter"]
    assert picked["aggs"]["groups"]["terms"]["field"] == "amounts.slug"
    assert picked["aggs"]["top"]["top_hits"]["size"] == 3
    assert {"terms": {"direction": ["improve"]}} in body["query"]["bool"]["filter"]
    assert payload["total"] == {"yen": 3_500_000_000, "projects": 3, "municipalities": 2}
    assert payload["groups"] == [{"key": SLUG, "label": "川崎市", "yen": 3_000_000_000, "projects": 2}]
    assert payload["other_groups_yen"] == 500_000_000
    assert payload["top_projects"][0]["yen"] == 1_008_569_000 and payload["matched_records"] == 40


def test_aggregate_stage_and_year_range(api):
    call, requests, response = api
    response["body"] = {"hits": {"total": {"value": 0}}, "aggregations": {"facts": {"picked": {"doc_count": 0, "sum": {"value": 0},
                                                                                               "municipalities": {"value": 0}}}}}
    status, payload = call("hyoka-aggregate", group_by="year", from_year="2020", to_year="2024", stage="budget", scope="funding_general",
                           group_by_unused="x")
    assert status == 200, payload
    filters = requests[-1][1]["aggs"]["facts"]["aggs"]["picked"]["filter"]["bool"]["filter"]
    assert {"term": {"amounts.stage": "budget"}} in filters and {"term": {"amounts.stage_best": True}} in filters
    assert {"range": {"amounts.year": {"gte": 2020, "lte": 2024}}} in filters
    assert requests[-1][1]["aggs"]["facts"]["aggs"]["picked"]["aggs"]["groups"]["terms"]["order"] == {"_key": "asc"}


@pytest.mark.parametrize("params", [{}, {"year": "2024", "scope": "all"}, {"year": "2024", "group_by": "colour"},
                                    {"group_by": "year", "from_year": "2025", "to_year": "2020"}, {"year": "2024", "top": "50"}])
def test_aggregate_rejects_bad_params(api, params):
    call, requests, _ = api
    status, payload = call("hyoka-aggregate", **params)
    assert status == 422 and payload["status"] == "query_error"
    assert requests == []


def test_project_detail_with_sheet_text(api):
    call, requests, _ = api
    status, payload = call("hyoka-project", id=PROJECT_ID, max_sheet_chars="1000")
    assert status == 200, payload
    assert requests[-1][1]["query"] == {"ids": {"values": [PROJECT_ID]}}
    assert payload["project"]["amounts"][0]["yen"] == 1_008_569_000 and payload["project"]["amounts"][0]["best"] is True
    assert payload["record"]["raw_fields"] == [{"label": "対象", "text": "市民"}]
    assert payload["record"]["amounts"]["unit"] == "千円"
    assert [page["page"] for page in payload["sheet"]] == [21, 22]
    assert payload["sheet"][0]["text"] == "令和７年度 事務事業評価シート\n[表 1]\n事務事業名 | 値"
    assert payload["sheet_truncated"] is False
    status, payload = call("hyoka-project", id=PROJECT_ID, max_sheet_chars="10")
    assert payload["sheet_truncated"] is True and len(payload["sheet"]) == 1
    status, payload = call("hyoka-project", id=PROJECT_ID, include_sheet="0")
    assert payload["sheet"] == []


def test_project_detail_accepts_machine_record_ids(api):
    call, requests, _ = api
    machine_id = f"hyoka-project:{SLUG}:{DOC}:hyoka:{'0' * 24}"
    status, payload = call("hyoka-project", id=machine_id)
    assert status == 200, payload
    assert requests[-1][1]["query"] == {"ids": {"values": [machine_id]}}


def test_project_detail_errors(api):
    call, requests, response = api
    status, payload = call("hyoka-project", id="hyoka:14130-kawasaki-shi:aaaa")
    assert status == 422 and requests == []
    response["body"] = {"hits": {"hits": []}}
    status, payload = call("hyoka-project", id=PROJECT_ID)
    assert status == 404 and payload["status"] == "not_found"
    response["status"] = 503
    status, payload = call("hyoka-projects", q="広報")
    assert status == 503 and payload["status"] == "opensearch_error"
