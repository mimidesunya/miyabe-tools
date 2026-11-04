"""AI の結果を、人が読んだ正解（truth/）と突き合わせる。

点数を付けるためではなく、どの値を取り違えたかを人が読むために、違いを具体的に並べる。
"""

from __future__ import annotations

from extract import norm

# 検索で同じに扱う範囲と方向性。細かい分け方の違いは誤りに数えない。
SCOPE_GROUP = {"personnel_regular": "personnel", "personnel_other": "personnel", "breakdown": "part", "sub_project": "part"}
DIRECTION_GROUP = {"maintain": "continue"}


def truth_amounts(truth: dict) -> list[dict]:
    """正解の金額の表を、1 値 1 行にする。"""
    rows = []
    for table in truth.get("amount_tables") or []:
        for row in table["rows"]:
            for column, value in zip(table["columns"], row["values"]):
                if isinstance(value, str) and norm(value) != "0":
                    rows.append({"label": row["label"], "fiscal_year": column["fiscal_year"], "stage": column["stage"],
                                 "scope": row["scope"], "raw": value, "unit": table["unit"]})
    return rows


def truth_funding(truth: dict) -> list[dict]:
    rows = []
    amount_columns = {c["key"]: c for t in truth.get("amount_tables") or [] for c in t["columns"]}
    for table in truth.get("funding_tables") or []:
        for row in table["rows"]:
            for key, value in zip(table["columns"], row["values"]):
                if isinstance(value, str) and norm(value) != "0":
                    rows.append({"source": row["source"], "fiscal_year": amount_columns[key]["fiscal_year"], "raw": value})
    return rows


def take(rows: list[dict], want: dict) -> dict | None:
    """条件に合う行を 1 つ取り出す（同じ値を二度数えない）。"""
    for row in rows:
        if all((norm(row.get(key)) == norm(value)) for key, value in want.items()):
            rows.remove(row)
            return row
    return None


def score_sheet(truth: dict, record: dict) -> dict:
    out = {"miss": [], "wrong": []}
    # 金額: 年度と値で探し、種類と範囲と単位を見る
    got = [r for r in record.get("amounts") or []]
    staff = list(record.get("staff") or [])
    amounts = truth_amounts(truth)
    matched = 0
    for want in amounts:
        row = take(got, {"fiscal_year": want["fiscal_year"], "raw": want["raw"]})
        if row is None and want["scope"] in ("personnel_regular", "personnel_other"):  # 人件費の内訳は人員の欄でもよい
            row = take(staff, {"fiscal_year": want["fiscal_year"], "raw": want["raw"]})
            if row is not None:
                matched += 1
                continue
        if row is None:
            out["miss"].append(f"金額 {want['fiscal_year']} {want['label']} {want['raw']}")
            continue
        matched += 1
        if SCOPE_GROUP.get(want["scope"], want["scope"]) != SCOPE_GROUP.get(row.get("scope"), row.get("scope")):
            out["wrong"].append(f"金額のscope {want['fiscal_year']} {want['label']}: 正 {want['scope']} 誤 {row.get('scope')}")
        if norm(row.get("unit")) != norm(want["unit"]):
            out["wrong"].append(f"金額のunit {want['fiscal_year']} {want['label']}: 正 {want['unit']} 誤 {row.get('unit')}")
        if want["stage"] != "unstated" and want["stage"] != row.get("stage") and not (want["stage"] == "budget" and row.get("stage") == "plan"):
            out["wrong"].append(f"金額のstage {want['fiscal_year']} {want['label']}: 正 {want['stage']} 誤 {row.get('stage')}")
    out["amounts"] = (matched, len(amounts))
    out["amount_extra"] = [f"{r.get('fiscal_year')} {r.get('label')} {r.get('raw')}" for r in got if norm(r.get("raw")) not in ("0", "－", "-")]
    # 財源
    got = list(record.get("funding") or [])
    funding = truth_funding(truth)
    matched = 0
    for want in funding:
        row = take(got, {"fiscal_year": want["fiscal_year"], "source": want["source"], "raw": want["raw"]})
        if row is None:
            out["miss"].append(f"財源 {want['fiscal_year']} {want['source']} {want['raw']}")
        else:
            matched += 1
    out["funding"] = (matched, len(funding))
    # 指標: 名前で対応づけ、年度ごとの目標と実績を見る
    indicators = record.get("indicators") or []
    total = hit = 0
    for want in truth.get("indicators") or []:
        name = norm(want["name"])
        row = next((i for i in indicators if name[:8] in norm(i.get("name")) or norm(i.get("name"))[:8] in name), None)
        if row is None:
            out["miss"].append(f"指標 {want['name']}")
            total += sum(isinstance(v, str) for k in ("target", "actual") for v in want.get(k, []))
            continue
        if row.get("kind") != want["kind"]:
            out["wrong"].append(f"指標の種類 {want['name']}: 正 {want['kind']} 誤 {row.get('kind')}")
        values = {v.get("fiscal_year"): v for v in row.get("values") or []}
        for key, field in (("target", "target_raw"), ("actual", "actual_raw")):
            for year, value in zip(want["columns"], want.get(key, [])):
                if not isinstance(value, str):
                    continue
                total += 1
                if norm(values.get(year, {}).get(field)).rstrip(".") == norm(value).rstrip("."):
                    hit += 1
                else:
                    out["wrong"].append(f"指標 {want['name'][:14]} {year} {key}: 正 {value} 誤 {values.get(year, {}).get(field)}")
    out["indicators"] = (hit, total)
    # 評価: 段階の数と、方向性
    evaluations = record.get("evaluations") or []
    total = hit = 0
    for want in truth.get("evaluations") or []:
        total += 1
        head = norm(want["stage_raw"]).split("（")[0][:4]
        row = next((e for e in evaluations if head in norm(e.get("stage_raw")) or norm(e.get("stage_raw"))[:3] in norm(want["stage_raw"])), None)
        if row is None:
            out["miss"].append(f"評価の段階 {want['stage_raw']}")
            continue
        ok = True
        if want["status"] != row.get("status"):
            out["wrong"].append(f"評価の状態 {want['stage_raw']}: 正 {want['status']} 誤 {row.get('status')}")
            ok = False
        if "direction" in want and DIRECTION_GROUP.get(want["direction"], want["direction"]) != DIRECTION_GROUP.get(row.get("direction"), row.get("direction")):
            out["wrong"].append(f"方向性 {want['stage_raw']}: 正 {want['direction']}（{want.get('direction_raw')}） 誤 {row.get('direction')}（{row.get('direction_raw')}）")
            ok = False
        if want.get("reason_contains") and norm(want["reason_contains"]) not in norm(row.get("reason")):
            out["wrong"].append(f"評価の理由 {want['stage_raw']}: 「{want['reason_contains'][:20]}」が番地から取れない")
            ok = False
        hit += ok
    out["evaluations"] = (hit, total)
    # 事業の識別
    project = record.get("project") or {}
    total = hit = 0
    for key in ("name", "department"):
        want = (truth.get("project") or {}).get(key)
        if want:
            total += 1
            if norm(want)[:6] in norm(project.get(key)):
                hit += 1
            else:
                out["wrong"].append(f"事業の{key}: 正 {want} 誤 {project.get(key)}")
    out["project"] = (hit, total)
    return out


def number(raw: object) -> float | None:
    try:
        return float(norm(raw).rstrip("%.").replace("％", ""))
    except ValueError:
        return None


def arithmetic(record: dict) -> list[str]:
    """答えの中の値で検算する。合わないものは、列や行の読み違いを疑う。"""
    def value(raw: object) -> int | None:
        text = norm(raw).replace("▲", "-")
        return int(text) if text.lstrip("-").isdigit() else None

    by_year: dict = {}
    for row in record.get("amounts") or []:
        scopes = by_year.setdefault((row.get("fiscal_year"), row.get("stage")), {})
        amount = value(row.get("raw"))
        if amount is not None:  # 人件費の内訳が並ぶ様式があるので、同じ範囲は大きい値（合計）を使う
            scopes[row.get("scope")] = max(amount, scopes.get(row.get("scope"), amount))
    problems = []
    for (year, stage), scopes in by_year.items():
        direct, personnel, total = scopes.get("direct"), scopes.get("personnel"), scopes.get("total")
        if None not in (direct, personnel, total) and direct + personnel != total:
            problems.append(f"{year} {stage}: 事業費 {direct} + 人件費 {personnel} が総費用 {total} と合わない")
    for indicator in record.get("indicators") or []:
        for row in indicator.get("values") or []:
            target, actual, rate = (number(row.get(key)) for key in ("target_raw", "actual_raw", "rate_raw"))
            if target and actual is not None and rate is not None and abs(actual / target * 100 - rate) > 1:
                problems.append(f"{indicator.get('name')} {row.get('fiscal_year')}: 実績 {actual} / 目標 {target} が達成率 {rate} と合わない")
    return problems
