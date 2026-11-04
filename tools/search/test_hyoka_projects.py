"""事務事業評価の事業ごとの索引の文書づくり。OpenSearch には繋がない。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SEARCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SEARCH_DIR))
sys.path.insert(0, str(SEARCH_DIR.parents[1] / "tools" / "hyoka"))

import build_hyoka_projects_index  # noqa: E402
import hyoka_projects as hp  # noqa: E402

SLUG = "14130-kawasaki-shi"


def amounts(unit, columns, rows):
    return {"unit": unit, "columns": [{"year": year, "stage": stage} for year, stage in columns],
            "rows": [{"label": label, "kind": kind, "unit": "", "values": values} for label, kind, values in rows]}


def record(record_id, name, *, code="", year=2024, table=None, direction="Ⅱ（改善・見直し）", indicators=None, page=2):
    fields = [{"key": "project_name", "name": "事業名", "label": "", "text": name},
              {"key": "department", "name": "担当部署", "label": "", "text": "市民文化局"},
              {"key": "evaluation_year", "name": "評価年度", "label": "", "text": f"{year}年度"},
              {"key": "future_direction", "name": "今後の方向性", "label": "", "text": direction}]
    if code:
        fields.append({"key": "project_code", "name": "事業番号", "label": "", "text": code})
    return {"id": record_id, "layout": "form_reader", "page": page, "fields": fields, "raw_fields": [],
            "amounts": table, "indicators": indicators or [],
            "evaluations": [{"stage": "今後の方向性", "rating": "4", "direction": direction, "aspects": ["成果: c", "民間の活用: b"]}]}


def sheet(year, values, stages=("決算", "見込")):
    """前年の決算と今年の見込・翌年の決算（様式の空欄の 0）を持つ金額の表。"""
    columns = [(year - 1, stages[0]), (year, stages[1]), (year + 1, "決算")]
    return amounts("千円", columns, [("総コスト(A+B)", "総費用（人件費を含む）", [*values, "0"])])


@pytest.mark.parametrize("raw, unit, expected", [
    ("1,234", "千円", 1_234_000), ("△500", "千円", -500_000), ("１２３", "円", 123), ("2.5", "百万円", 2_500_000),
    ("300千円", "", 300_000), ("-", "千円", None), ("", "千円", None), ("12", "", None), ("1,2a", "千円", None),
])
def test_parse_yen(raw, unit, expected):
    assert hp.parse_yen(raw, unit) == expected


@pytest.mark.parametrize("raw, expected", [("7.0以上", 7.0), ("85.8%", 85.8), ("－", None), ("1,460", 1460.0), ("R5 12", None)])
def test_parse_number(raw, expected):
    assert hp.parse_number(raw) == expected


@pytest.mark.parametrize("code, text, expected", [
    ("abolish", "長い文", "abolish"), ("", "Ⅱ（改善・見直し）", "improve"), ("", "拡充", "expand"),
    ("", "改善しながら継続", "improve"), ("", "事業規模縮小は考えておらず、引き続き着実に事業を進めていきます。", "other"), ("", "", ""),
])
def test_direction_code(code, text, expected):
    assert hp.direction_code(code, text) == expected


def test_names_match_display_builder():
    """控えの日本語の名前は build_display_data.py が書く。逆引きの元が同じであること。"""
    build_display_data = pytest.importorskip("build_display_data")
    for name in ("STAGE_NAMES", "SCOPE_NAMES", "SOURCE_NAMES", "DIRECTION_NAMES", "KIND_NAMES"):
        assert getattr(hp, name) == getattr(build_display_data, name), name


def test_future_settlement_blank_is_dropped_and_cost_is_own_year():
    document = hp.project_document(SLUG, {"doc_id": "a" * 16, "source_url": "https://example.jp/s.pdf"},
                                   record("r1", "広報事業", table=sheet(2024, ["100", "120"])), {"fiscal_year": 2024}, {})
    assert [(fact["year"], fact["stage"], fact["yen"]) for fact in document["amounts"]] == [(2023, "settlement", 100_000), (2024, "estimate", 120_000)]
    assert (document["cost_yen"], document["cost_stage"], document["cost_scope"]) == (120_000, "estimate", "total")
    assert document["direction"] == "improve" and document["aspects"] == ["成果:c", "民間の活用:b"]
    assert document["source_page_link"] == "https://example.jp/s.pdf#page=2"
    assert document["project_id"] == f"hyoka-project:{SLUG}:{'a' * 16}:r1"


def test_indicator_ratio_uses_evaluation_year():
    indicators = [{"name": "参加者数", "kind": "成果", "unit": "人", "values": [
        {"year": 2023, "target": "100", "actual": "90"}, {"year": 2024, "target": "200", "actual": "50"}, {"year": 2025, "target": "200", "actual": ""}]}]
    document = hp.project_document(SLUG, {"doc_id": "a" * 16}, record("r1", "広報事業", indicators=indicators), {}, {})
    assert document["indicators"][0]["year"] == 2024 and document["indicators"][0]["ratio"] == 0.25
    assert document["outcome_ratio_min"] == 0.25


def build(documents):
    hp.mark_best(documents)
    return {(doc["record_id"], fact["year"], fact["stage"]): (fact["best"], fact["stage_best"])
            for doc in documents for fact in doc["amounts"]}


def test_best_prefers_settlement_then_newer_sheet():
    older = hp.project_document(SLUG, {"doc_id": "a" * 16}, record("old", "広報事業", year=2024, table=sheet(2024, ["100", "120"])),
                                {"fiscal_year": 2024}, {})
    newer = hp.project_document(SLUG, {"doc_id": "b" * 16}, record("new", "広報事業", year=2025, table=sheet(2025, ["118", "130"])),
                                {"fiscal_year": 2025}, {})
    marks = build([older, newer])
    assert marks[("new", 2024, "settlement")] == (True, True)
    assert marks[("old", 2024, "estimate")] == (False, True)
    assert marks[("old", 2023, "settlement")] == (True, True)
    assert marks[("new", 2025, "estimate")] == (True, True)
    assert (newer["cost_prev_yen"], newer["cost_change_ratio"]) == (118_000, round(130 / 118, 4))


def test_full_sheet_wins_over_summary_row_with_same_name():
    table = amounts("千円", [(2024, "見込")], [("事業費A", "事業費（人件費を除く）", ["50"]), ("総コスト", "総費用（人件費を含む）", ["60"])])
    summary = amounts("千円", [(2024, "決算")], [("事業費", "事業費（人件費を除く）", ["9999"])])
    full = hp.project_document(SLUG, {"doc_id": "a" * 16}, record("sheet", "バス事業", table=table), {"fiscal_year": 2024}, {})
    row = hp.project_document(SLUG, {"doc_id": "b" * 16}, record("summary", "バス事業", table=summary), {"fiscal_year": 2025}, {})
    marks = build([full, row])
    assert marks[("sheet", 2024, "estimate")] == (True, True)
    assert marks[("summary", 2024, "settlement")] == (False, True)


def test_projects_link_by_code_and_by_name_across_years_only():
    renamed = [hp.project_document(SLUG, {"doc_id": "a" * 16}, record("r1", "難病対策事業", code="105", table=sheet(2024, ["1", "2"])),
                                   {"fiscal_year": 2024}, {}),
               hp.project_document(SLUG, {"doc_id": "b" * 16}, record("r2", "指定難病対策事業", code="105", table=sheet(2025, ["2", "3"])),
                                   {"fiscal_year": 2025}, {})]
    renumbered = [hp.project_document(SLUG, {"doc_id": "a" * 16}, record("r3", "児童虐待防止対策事業", code="201", table=sheet(2024, ["1", "2"])),
                                      {"fiscal_year": 2024}, {}),
                  hp.project_document(SLUG, {"doc_id": "b" * 16}, record("r4", "児童虐待防止対策事業", code="210", table=sheet(2025, ["2", "3"])),
                                      {"fiscal_year": 2025}, {})]
    same_year = [hp.project_document(SLUG, {"doc_id": "a" * 16}, record("r5", "広報事業", code="301", table=sheet(2024, ["1", "2"])),
                                     {"fiscal_year": 2024}, {}),
                 hp.project_document(SLUG, {"doc_id": "c" * 16}, record("r6", "広報事業", code="302", table=sheet(2024, ["5", "6"])),
                                     {"fiscal_year": 2024}, {})]
    documents = renamed + renumbered + same_year
    hp.mark_best(documents)
    keys = {doc["record_id"]: doc["project_key"] for doc in documents}
    assert keys["r1"] == keys["r2"]
    assert keys["r3"] == keys["r4"]
    assert keys["r5"] != keys["r6"]
    best_2024 = sum(fact["yen"] for doc in same_year for fact in doc["amounts"] if fact["best"] and fact["year"] == 2024)
    assert best_2024 == 8_000


def write_display(root: Path, docs: dict[str, list[dict]]):
    for doc_id, records in docs.items():
        path = root / "records" / SLUG / f"{doc_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"slug": SLUG, "doc_id": doc_id, "source_url": "https://example.jp/a.pdf", "records": records},
                                   ensure_ascii=False), encoding="utf-8")


def test_build_documents_picks_page_ids_and_municipality(tmp_path):
    write_display(tmp_path, {"a" * 16: [record("r1", "広報事業", table=sheet(2024, ["1", "2"]), page=3)]})
    metadata = {f"{SLUG}:{'a' * 16}": {"fiscal_year": 2024, "kind": "project_evaluation", "title": "政策 4-9",
                                       "ids": [f"hyoka:{SLUG}:{'a' * 16}:p2", f"hyoka:{SLUG}:{'a' * 16}:p3"]}}
    municipalities = {"14130": {"name": "川崎市", "pref_code": "14", "pref_name": "神奈川県"}}
    [batch] = list(hp.build_documents(tmp_path, metadata, municipalities))
    [document] = batch
    assert document["document_id"] == f"hyoka:{SLUG}:{'a' * 16}:p3"
    assert (document["municipality_name"], document["pref_name"], document["document_title"]) == ("川崎市", "神奈川県", "政策 4-9")
    assert document["amounts"][0]["municipality_name"] == "川崎市" and document["amounts"][0]["project_name"] == "広報事業"
    properties = hp.index_body({})["mappings"]["properties"]
    assert set(document) - {"search_text"} <= set(properties)
    assert set(document["amounts"][0]) <= set(properties["amounts"]["properties"])


def test_cli_writes_ndjson_from_manifests(tmp_path):
    display = tmp_path / "display"
    write_display(display, {"a" * 16: [record("r1", "広報事業", table=sheet(2024, ["1", "2"]))]})
    manifest = tmp_path / "work" / SLUG / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"documents": {"a" * 16: {"fiscal_year": 2024, "kind": "project_evaluation", "title": "評価"}}}),
                        encoding="utf-8")
    out = tmp_path / "out.ndjson"
    assert build_hyoka_projects_index.main(["--display-dir", str(display), "--manifest-root", str(tmp_path / "work"), "--out", str(out)]) == 0
    [line] = out.read_text(encoding="utf-8").splitlines()
    document = json.loads(line)
    assert document["document_id"] == f"hyoka:{SLUG}:{'a' * 16}" and document["document_fiscal_year"] == 2024


def test_cli_refuses_partial_index(tmp_path):
    with pytest.raises(SystemExit):
        build_hyoka_projects_index.main(["--display-dir", str(tmp_path), "--slug", SLUG])


def test_cost_per_unit_skips_ratios_and_zero():
    assert hp.cost_per_unit(1_000_000, {"actual": "10", "unit": "回"}) == 100_000
    assert hp.cost_per_unit(1_000_000, {"actual": "85.8", "unit": "%"}) is None
    assert hp.cost_per_unit(1_000_000, {"actual": "0", "unit": "人"}) is None
    assert hp.cost_per_unit(None, {"actual": "10", "unit": "回"}) is None
    assert hp.cost_per_unit(1_000_000, {"name": "重要な管路の耐震化率", "actual": "98.5", "unit": ""}) is None
    assert hp.cost_per_unit(1_000_000, {"name": "工業用水道管路の更新延長", "actual": "0.9", "unit": ""}) == 1_111_111


def with_reason(base: dict, text: str) -> dict:
    base["raw_fields"] = [{"label": "評価の理由", "text": text}, {"label": "根拠法令", "text": "毎年同じでよい長さのある根拠法令の記載です。"}]
    return base


def test_flags_history_and_repeated_text():
    reason = "参加者は目標に届かなかったが一定の成果があった。引き続き周知に努めて事業を継続していく。"
    indicators = [{"name": "参加者数", "kind": "成果", "unit": "人", "values": [
        {"year": 2023, "target": "100", "actual": "90"}, {"year": 2024, "target": "100", "actual": "40"}]}]
    first = hp.project_document(SLUG, {"doc_id": "a" * 16}, with_reason(
        record("r1", "広報事業", code="1", year=2023, table=sheet(2023, ["100", "100"]), indicators=indicators), reason), {"fiscal_year": 2023}, {})
    second = hp.project_document(SLUG, {"doc_id": "b" * 16}, with_reason(
        record("r2", "広報推進事業", code="1", year=2024, table=sheet(2024, ["100", "150"]), indicators=indicators, direction="拡充"), reason),
        {"fiscal_year": 2024}, {})
    activity_only = hp.project_document(SLUG, {"doc_id": "c" * 16}, record(
        "r3", "博物館事業", year=2024, table=sheet(2024, ["5", "5"]),
        indicators=[{"name": "展覧会の回数", "kind": "活動", "unit": "回", "values": [{"year": 2024, "target": "4", "actual": "10"}]}]),
        {"fiscal_year": 2024}, {})
    hp.mark_best([first, second, activity_only])
    assert second["text_repeat_ratio"] == 1.0 and first["text_repeat_ratio"] is None
    assert set(second["flags"]) == {"outcome_below_half", "expand_below_target", "cost_up_below_target", "repeated_text", "renamed"}
    assert second["project_names"] == ["広報事業", "広報推進事業"] and (second["project_first_year"], second["project_last_year"]) == (2023, 2024)
    assert "not_in_latest" not in first["flags"] and first["flags"] == ["renamed"]
    assert activity_only["flags"] == ["no_outcome_target"]
    assert activity_only["indicators"][0]["cost_per_unit_yen"] == 500  # 5 千円 ÷ 10 回
    assert all(not key.startswith("_") for document in (first, second, activity_only) for key in document)


def test_not_in_latest():
    old = hp.project_document(SLUG, {"doc_id": "a" * 16}, record("r1", "廃止した事業", year=2022), {"fiscal_year": 2022}, {})
    new = hp.project_document(SLUG, {"doc_id": "b" * 16}, record("r2", "続く事業", year=2024), {"fiscal_year": 2024}, {})
    hp.mark_best([old, new])
    assert old["flags"] == ["not_in_latest"] and new["flags"] == []


def test_flag_names_match_php_and_mcp():
    root = SEARCH_DIR.parents[1]
    php = (root / "lib" / "hyoka_projects.php").read_text(encoding="utf-8")
    mcp = (root / "docker" / "mcp" / "src" / "server.ts").read_text(encoding="utf-8")
    for flag in hp.FLAG_NAMES:
        assert f"'{flag}'" in php and f'"{flag}"' in mcp, flag
