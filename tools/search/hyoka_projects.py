#!/usr/bin/env python3
"""事務事業評価の事業ごとの索引（miyabe-hyoka-projects-current）の文書を作る。

入力は画面用の控え `data/hyoka/records/<slug>/<doc_id>.json`（tools/hyoka/build_display_data.py が書く）。
控えの 1 事業を索引の 1 件にし、金額は円にそろえ、方向性は区分に、指標は目標に対する実績の比にする。
値は控えの原文から作り、推定で補わない（単位の分からない金額は円にしない）。

集計で同じ事業を二重に数えないよう、自治体ごとに「事業・年度・費目」が同じ金額から 1 つを選んで
`best` の印を付ける。選ぶ順は、総費用の行がある記録（事業ごとのシート）を先に、次に 決算 → 見込 → 予算 → 計画、
同じ段階なら新しい年度の文書（後のシート）。総括や中間評価の施策の表の行には、同じ事業名でもシートと意味の違う
金額（施策の計画事業費など）が入ることがある（川崎市の総括で、シートの 928 万円に対して 2.1 億円）ため。
段階を指定して集計するときのために、段階ごとにも 1 つ選んで `stage_best` の印を付ける。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Iterator

ALIAS = "miyabe-hyoka-projects-current"
INDEX_PREFIX = "miyabe-hyoka-projects-v"
SCHEMA_VERSION = 1

# build_display_data.py の STAGE_NAMES・SCOPE_NAMES・SOURCE_NAMES・DIRECTION_NAMES・KIND_NAMES の逆引き。
# 控えには日本語の名前で入っている。名前を変えたら両方を直す（test_hyoka_projects.py が突き合わせる）。
STAGE_NAMES = {"initial_budget": "当初予算", "revised_budget": "補正後予算", "budget": "予算", "plan": "計画",
               "settlement": "決算", "estimate": "見込", "unstated": ""}
SCOPE_NAMES = {"direct": "事業費（人件費を除く）", "personnel": "人件費", "total": "総費用（人件費を含む）",
               "sub_project": "細事業", "breakdown": "内訳"}
SOURCE_NAMES = {"national": "国", "prefecture": "都道府県", "bond": "地方債", "fund": "基金", "user_fee": "受益者負担",
                "other_specific": "その他の特定財源", "general": "一般財源"}
DIRECTION_NAMES = {"expand": "拡充", "continue": "継続", "maintain": "現状維持", "improve": "改善・見直し", "reduce": "縮小",
                   "integrate": "統合", "suspend": "休止", "abolish": "廃止", "complete": "完了"}
KIND_NAMES = {"target": "対象", "activity": "活動", "outcome": "成果", "unknown": ""}

STAGE_CODES = {name: code for code, name in STAGE_NAMES.items()}
SCOPE_CODES = {name: code for code, name in SCOPE_NAMES.items()}
FUNDING_CODES = {name: "funding_" + code for code, name in SOURCE_NAMES.items()}
DIRECTION_CODES = {name: code for code, name in DIRECTION_NAMES.items()}
KIND_CODES = {name: code for code, name in KIND_NAMES.items() if name}

# 集計に使う値の順（形 2.0 の金額）: 決算 → 見込 → 補正後予算 → 予算（不明） → 当初予算 → 計画。
STAGE_RANK = {"settlement": 6, "estimate": 5, "revised_budget": 4, "budget": 3, "initial_budget": 2, "plan": 1, "unstated": 0}
# 集計に使う費目。細事業・内訳は親の事業の金額と重なるので、索引には載せるが既定の集計には使わない。
COST_SCOPES = ("total", "direct", "personnel")

UNIT_FACTORS = {"円": 1, "千円": 1_000, "万円": 10_000, "百万円": 1_000_000, "億円": 100_000_000}
EMPTY_VALUES = {"", "-", "－", "―", "—", "‐", "−", "…", "・", "ー", "*", "＊", "x", "X"}
NUMBER_RE = re.compile(r"([△▲\-−]?)(\d+(?:\.\d+)?)")
YEAR_RE = re.compile(r"(\d{4})\s*年度")

# 方向性の短い記載の言葉。長い文は区分を決めない（「縮小は考えていない」を縮小と読まない）。
DIRECTION_WORDS = (
    ("improve", ("改善", "見直")),
    ("expand", ("拡充", "拡大", "拡張")),
    ("reduce", ("縮小", "削減", "縮減")),
    ("abolish", ("廃止",)),
    ("suspend", ("休止", "凍結")),
    ("integrate", ("統合", "統廃合")),
    ("complete", ("完了", "終了")),
    ("maintain", ("現状維持", "現状のまま")),
    ("continue", ("継続",)),
)
DIRECTION_SHORT_CHARS = 16


def nfkc(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def parse_number(raw: object) -> float | None:
    """升目の数（「1,234」「△500」「7.0以上」「85.8%」）。数の後ろに数字以外が付くのは許す。数が無ければ None。"""
    text = nfkc(raw).replace(",", "").replace(" ", "")
    if text in EMPTY_VALUES:
        return None
    match = NUMBER_RE.match(text)
    if match is None or re.search(r"\d", text[match.end():]):
        return None
    value = float(match.group(2))
    return -value if match.group(1) else value


def parse_yen(raw: object, unit: str) -> int | None:
    """金額を円にする。単位が分からなければ None（推定しない）。値の中に単位が書いてあればそちらを使う。"""
    text = nfkc(raw).replace(",", "").replace(" ", "")
    for name in sorted(UNIT_FACTORS, key=len, reverse=True):
        if text.endswith(name):
            unit = name
            text = text[: -len(name)]
            break
    factor = UNIT_FACTORS.get(nfkc(unit))
    if factor is None or text in EMPTY_VALUES:
        return None
    match = NUMBER_RE.fullmatch(text)
    if match is None:
        return None
    value = round(float(match.group(2)) * factor)
    return -value if match.group(1) else value


def year_of(text: object) -> int:
    match = YEAR_RE.search(nfkc(text))
    return int(match.group(1)) if match else 0


def field_map(record: dict) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in record.get("fields") or []:
        if isinstance(field, dict) and field.get("key") and str(field.get("text") or "").strip():
            fields.setdefault(str(field["key"]), str(field["text"]).strip())
    return fields


def direction_code(evaluation_code: str, text: str) -> str:
    """方向性の区分。読み取りが決めた区分があればそれ、無ければ記載の括弧の名前か短い記載の言葉から。"""
    if evaluation_code in DIRECTION_NAMES or evaluation_code == "other":
        return evaluation_code
    text = nfkc(text)
    if text == "":
        return ""
    paren = re.search(r"[（(]([^（）()]+)[）)]\s*$", text)
    if paren and paren.group(1) in DIRECTION_CODES:
        return DIRECTION_CODES[paren.group(1)]
    if len(re.sub(r"\s", "", text)) <= DIRECTION_SHORT_CHARS:
        for code, words in DIRECTION_WORDS:
            if any(word in text for word in words):
                return code
    return "other"


def name_key(name: str) -> str:
    """同じ事業を見分ける名前。字形と空白だけそろえる。"""
    return re.sub(r"\s+", "", nfkc(name)).lower()


def amount_facts(table: object, own_year: int, *, funding: bool = False) -> list[dict]:
    """控えの金額の表（年度と段階の列、欄の行）を、1 値 1 件の円の金額にする。

    評価した年度より後の年度の決算・見込は様式の空欄（川崎市は 0 と刷る）なので落とす。
    """
    if not isinstance(table, dict):
        return []
    columns = [column for column in table.get("columns") or [] if isinstance(column, dict)]
    common_unit = str(table.get("unit") or "")
    facts = []
    for row in table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        kind = str(row.get("kind") or "")
        if funding:
            scope = FUNDING_CODES.get(kind, "funding_other")
        else:
            scope = SCOPE_CODES.get(kind, "other")
        unit = str(row.get("unit") or "") or common_unit
        for column, raw in zip(columns, row.get("values") or []):
            year = int(column.get("year") or 0)
            stage = STAGE_CODES.get(str(column.get("stage") or ""), "unstated")
            if year <= 0:
                continue
            if own_year and year > own_year and stage in ("settlement", "estimate"):
                continue
            yen = parse_yen(raw, unit)
            if yen is None:
                continue
            facts.append({"year": year, "stage": stage, "scope": scope, "label": label, "raw": str(raw), "unit": unit, "yen": yen})
    return facts


def pick_cost(facts: list[dict], year: int) -> dict | None:
    """その年度の事業の費用を 1 つ。総費用、無ければ事業費。段階は決算 → 見込 → 予算 → 計画。"""
    for scope in ("total", "direct"):
        candidates = [fact for fact in facts if fact["year"] == year and fact["scope"] == scope]
        if candidates:
            return max(candidates, key=lambda fact: STAGE_RANK.get(fact["stage"], 0))
    return None


def staff_value(table: object, year: int) -> float | None:
    if not isinstance(table, dict):
        return None
    columns = [column for column in table.get("columns") or [] if isinstance(column, dict)]
    for row in table.get("rows") or []:
        if not isinstance(row, dict) or "単価" in str(row.get("label") or ""):
            continue
        for column, raw in zip(columns, row.get("values") or []):
            if int(column.get("year") or 0) == year:
                value = parse_number(raw)
                if value is not None:
                    return value
    return None


def indicator_rows(indicators: object, own_year: int) -> list[dict]:
    """指標ごとに、評価した年度（無ければ目標と実績の両方がある最後の年度）の目標・実績・比。

    比は実績 ÷ 目標。少ない方が良い指標（未収金など）もあるので、比が 1 未満でも未達とは限らない。
    """
    rows = []
    for indicator in indicators if isinstance(indicators, list) else []:
        if not isinstance(indicator, dict):
            continue
        values = [value for value in indicator.get("values") or [] if isinstance(value, dict)]
        chosen = None
        for value in values:
            year = int(value.get("year") or 0)
            if own_year and year == own_year:
                chosen = value
        if chosen is None or parse_number(chosen.get("actual")) is None:
            usable = [value for value in values if parse_number(value.get("target")) is not None
                      and parse_number(value.get("actual")) is not None
                      and (not own_year or int(value.get("year") or 0) <= own_year)]
            chosen = usable[-1] if usable else chosen
        if chosen is None:
            continue
        target = parse_number(chosen.get("target"))
        actual = parse_number(chosen.get("actual"))
        ratio = round(actual / target, 4) if target not in (None, 0) and actual is not None and target > 0 else None
        kind = str(indicator.get("kind") or "")
        rows.append({
            "name": str(indicator.get("name") or ""), "kind": KIND_CODES.get(kind, kind), "unit": str(indicator.get("unit") or ""),
            "year": int(chosen.get("year") or 0), "target": str(chosen.get("target") or ""), "actual": str(chosen.get("actual") or ""),
            "ratio": ratio,
        })
    return rows


# 1 単位あたりの費用を出さない単位（割合・点数・倍率は「1 単位」が意味を持たない）。
RATIO_UNITS = {"%", "％", "点", "倍", "割", "ポイント", "pt", "位", "段階"}


# 単位の欄が空でも、名前から割合・点数と分かる指標（川崎市の「重要な管路の耐震化率」は単位が空で 98.5）。
RATIO_NAME_RE = re.compile(r"率|割合|満足度|指数|度合|平均値|得点|評価点|順位|倍率")


def cost_per_unit(cost_yen: int | None, row: dict) -> int | None:
    """費用 ÷ 指標の実績（参加者 1 人あたりなど）。実績が 0 以下か、単位や名前から割合・点数と分かれば出さない。"""
    actual = parse_number(row.get("actual"))
    unit = nfkc(row.get("unit"))
    if cost_yen is None or actual is None or actual <= 0 or unit in RATIO_UNITS or nfkc(row.get("actual")).endswith(("%", "点")):
        return None
    if unit == "" and RATIO_NAME_RE.search(nfkc(row.get("name"))):
        return None
    return round(cost_yen / actual)


# 評価の文として前の年度と比べる欄。目的・対象・根拠法令は毎年同じでよいので比べない。
EVALUATION_FIELD_KEYS = ("performance_result", "evaluation_reason", "future_direction", "issues")
EVALUATION_LABEL_RE = re.compile(r"評価|理由|所見|課題|方向|成果|実績|改善|見直")
SENTENCE_MIN_CHARS = 12


def evaluation_sentences(fields: dict[str, str], record: dict) -> list[str]:
    """評価の文を、字形と空白をそろえた文（12 字以上）の一覧にする。"""
    texts = [fields[key] for key in EVALUATION_FIELD_KEYS if fields.get(key)]
    texts += [str(field.get("text") or "") for field in record.get("raw_fields") or []
              if isinstance(field, dict) and EVALUATION_LABEL_RE.search(str(field.get("label") or ""))]
    sentences = []
    for text in texts:
        for sentence in re.split(r"[。\n]", re.sub(r"[ \t　]+", "", nfkc(text))):
            if len(sentence) >= SENTENCE_MIN_CHARS and sentence not in sentences:
                sentences.append(sentence)
    return sentences


# 分析の印（flags）。どれも原典の値から機械で付けたもので、問題があると決めたものではない。
FLAG_NAMES = {
    "outcome_below_half": "成果指標が目標の半分以下",
    "expand_below_target": "成果指標が目標割れなのに方向性が拡充",
    "cost_up_below_target": "成果指標が目標割れなのに費用が前年度より 1 割以上増えた",
    "no_outcome_target": "費用があるのに、目標の数のある成果指標が無い",
    "repeated_text": "評価の文が前の年度とほぼ同じ",
    "renamed": "事業名が年度によって違う",
    "not_in_latest": "その自治体の最新の評価の年度に載っていない",
}
REPEAT_THRESHOLD = 0.9
REPEAT_MIN_SENTENCES = 2


def record_method(record: dict) -> str:
    layout = str(record.get("layout") or "")
    return "ai" if layout == "ai_transcription" else "program" if layout == "form_reader" else "machine"


def location_label(record: dict) -> str:
    page = int(record.get("page") or 0)
    if page > 0:
        return f"原典 {page}頁"
    sheet = str(record.get("sheet") or "").strip()
    cells = str(record.get("cells") or "").strip()
    return f"原典 {sheet}{(' ' + cells) if cells else ''}" if sheet else ""


def page_link(source_url: str, page: int) -> str:
    if source_url == "" or page <= 0 or re.search(r"\.html?$", source_url.split("#")[0].split("?")[0], re.I):
        return source_url
    return re.sub(r"#.*$", "", source_url) + f"#page={page}"


def project_document(slug: str, doc: dict, record: dict, meta: dict, municipality: dict) -> dict:
    """控えの 1 事業を索引の 1 件にする。meta は文書の情報（年度・種別・題名・検索の ID）。"""
    fields = field_map(record)
    doc_id = str(doc.get("doc_id") or "")
    record_id = str(record.get("id") or "")
    page = int(record.get("page") or 0)
    document_year = int(meta.get("fiscal_year") or 0)
    evaluation_year = year_of(fields.get("evaluation_year")) or year_of(fields.get("target_year")) or document_year
    facts = amount_facts(record.get("amounts"), evaluation_year)
    facts += amount_facts(record.get("funding"), evaluation_year, funding=True)
    cost = pick_cost(facts, evaluation_year)
    evaluations = [row for row in record.get("evaluations") or [] if isinstance(row, dict)]
    final = next((row for row in reversed(evaluations) if str(row.get("direction") or "").strip()), {})
    direction_text = fields.get("future_direction", "") or str(final.get("direction") or "")
    rating = fields.get("evaluation_rating", "") or next(
        (str(row.get("rating")) for row in reversed(evaluations) if str(row.get("rating") or "").strip()), "")
    aspects = sorted({re.sub(r"\s+", "", nfkc(aspect)) for row in evaluations for aspect in row.get("aspects") or []
                      if isinstance(aspect, str) and ":" in aspect})
    indicators = indicator_rows(record.get("indicators"), evaluation_year)
    outcome = [row["ratio"] for row in indicators if row["kind"] == "outcome" and row["ratio"] is not None]
    every = [row["ratio"] for row in indicators if row["ratio"] is not None]
    texts = [str(field.get("text") or "") for field in record.get("fields") or [] if isinstance(field, dict)]
    texts += [f"{field.get('label') or ''} {field.get('text') or ''}" for field in record.get("raw_fields") or [] if isinstance(field, dict)]
    texts += [row["name"] for row in indicators]
    # 形 2.0 の文章の項目、評価の理由、そのほかの欄も事業の語で探せるようにする
    texts += [str(item.get("text") or "") for item in record.get("texts") or [] if isinstance(item, dict)]
    texts += [str(reason.get("text") or "") for row in evaluations for reason in row.get("reasons") or [] if isinstance(reason, dict)]
    texts += [f"{item.get('label') or ''} {item.get('text') or ''}" for item in record.get("extra_fields") or [] if isinstance(item, dict)]
    path = [item for item in record.get("policy_path") or [] if isinstance(item, dict) and item.get("name")]
    achievement = next((row["achievement"]["level"] for row in reversed(evaluations) if isinstance(row.get("achievement"), dict)), "")
    overall = next((row["overall"]["level"] for row in reversed(evaluations) if isinstance(row.get("overall"), dict)), "")
    variant = record.get("variant") if isinstance(record.get("variant"), dict) else {}
    policy_top = f"{path[0].get('code') or ''} {path[0]['name']}".strip() if path else ""
    policy_leaf = f"{path[-1].get('code') or ''} {path[-1]['name']}".strip() if path else ""
    variant_base = str(variant.get("base_name") or "")
    source_url = str(doc.get("source_url") or meta.get("source_url") or "")
    project_name = fields.get("project_name", "")
    department = fields.get("department", "")
    direction = direction_code(str(final.get("direction_code") or ""), direction_text)
    municipality_name = str(municipality.get("name") or meta.get("municipality_name") or "")
    pref_code = str(municipality.get("pref_code") or meta.get("pref_code") or slug[:2])
    for row in indicators:
        row["cost_per_unit_yen"] = cost_per_unit(cost["yen"] if cost else None, row)
    return {
        "project_id": f"hyoka-project:{slug}:{doc_id}:{record_id}",
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "doc_id": doc_id,
        # 文書の索引に無い文書（本文が無い・OCR 待ち）は空。文書全体の ID を作って当てずっぽうにしない。
        "document_id": meta.get("document_id") or "",
        "document_title": str(meta.get("title") or ""),
        "document_fiscal_year": document_year or None,
        "document_kind": str(meta.get("kind") or ""),
        "slug": slug,
        "municipality_code": slug[:5],
        "municipality_name": municipality_name,
        "pref_code": pref_code,
        "pref_name": str(municipality.get("pref_name") or meta.get("pref_name") or ""),
        "project_name": project_name,
        "project_key": name_key(project_name) or f"record:{record_id}",
        "project_code": fields.get("project_code", ""),
        "department": department,
        "purpose": fields.get("purpose", ""),
        "evaluation_year": evaluation_year or None,
        # 形 2.0 の体系の道筋（上の段から。段の呼び名は自治体の原文）と、事業の階層・分け・主な事業か
        "policy_path": [{key: str(item.get(key) or "") for key in ("level", "code", "name", "goal") if item.get(key)} for item in path],
        "policy_names": [f"{item.get('code') or ''} {item.get('name')}".strip() for item in path],
        "policy_top": policy_top or None,
        "policy_leaf": policy_leaf or None,
        "level": str(record.get("level") or "") or None,
        "variant_base": variant_base or None,
        "variant_label": str(variant.get("label") or "") or None,
        "is_main": record.get("is_main") if isinstance(record.get("is_main"), bool) else None,
        "achievement": achievement or None,
        "overall": overall or None,
        "rating": rating,
        "direction": direction,
        "direction_text": direction_text,
        "aspects": aspects,
        "method": record_method(record),
        "page": page or None,
        "location": location_label(record),
        "source_url": source_url,
        "source_page_link": page_link(source_url, page),
        "cost_yen": cost["yen"] if cost else None,
        "cost_year": cost["year"] if cost else None,
        "cost_stage": cost["stage"] if cost else "",
        "cost_scope": cost["scope"] if cost else "",
        "cost_raw": cost["raw"] if cost else "",
        "cost_unit": cost["unit"] if cost else "",
        "staff_fte": staff_value(record.get("staff"), evaluation_year),
        "has_amounts": bool(facts),
        "outcome_ratio_min": min(outcome) if outcome else None,
        "indicator_ratio_min": min(every) if every else None,
        "indicators": indicators,
        # 集計の内訳（自治体・部署・方向性ごと）と上位の事業を明細だけで出せるよう、事業の側の値も写す。
        "amounts": [{**fact, "best": False, "stage_best": False, "project_id": f"hyoka-project:{slug}:{doc_id}:{record_id}",
                     "project_name": project_name, "slug": slug, "pref_code": pref_code, "municipality_name": municipality_name,
                     "department": department, "direction": direction,
                     # 空の文字は集計で「空」の組になるので、値が無ければ欄ごと置かない
                     **{key: value for key, value in (("policy_top", policy_top), ("policy_leaf", policy_leaf),
                                                       ("variant_base", variant_base)) if value}}
                    for fact in facts],
        "search_text": "\n".join(text for text in texts if text.strip()),
        # 前の年度の評価の文と比べるための文。mark_best が使って消す（索引には入れない）。
        "_evaluation_sentences": evaluation_sentences(fields, record),
    }


def link_projects(documents: list[dict]) -> None:
    """1 自治体の中で同じ事業の記録に同じ project_key を付ける（その場で書き換える）。

    事業番号があれば番号で、無ければ名前でまとめる。名前が変わっても番号が同じなら同じ事業、
    番号が振り直されても、違う年度の文書で名前が同じなら同じ事業とみなす。同じ年度の文書で名前が同じで
    番号の違う記録（別の部署の同名の事業）はまとめない。
    """
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    nodes = []
    by_name: dict[str, list[tuple[str, int]]] = {}
    for document in documents:
        name = document["project_key"]
        code = nfkc(document.get("project_code"))
        node = f"code:{code}" if code else name
        nodes.append(node)
        find(node)
        if not name.startswith("record:"):
            by_name.setdefault(name, []).append((node, int(document.get("document_fiscal_year") or 0)))
    for name, members in by_name.items():
        uncoded = [node for node, _ in members if not node.startswith("code:")]
        # 違う年度の文書どうしを全部つなぐと、年度が 2 つ以上あれば同じ名前の記録はすべて 1 つにまとまる。
        # 年度が 1 つだけなら、番号の無い記録とだけつなぐ。
        linked = uncoded[:1] if len({year for _, year in members}) < 2 else [members[0][0]]
        for node, _ in members:
            for other in linked:
                union(node, other)
    for document, node in zip(documents, nodes):
        document["project_key"] = find(node)


def mark_best(documents: list[dict]) -> None:
    """1 自治体の事業から、事業・年度・費目ごとに集計に使う金額を 1 つ選んで印を付ける（その場で書き換える）。"""
    link_projects(documents)
    best: dict[tuple, tuple] = {}
    stage_best: dict[tuple, tuple] = {}
    for doc_index, document in enumerate(documents):
        sheet = any(fact["scope"] == "total" for fact in document["amounts"])
        order = (document.get("document_fiscal_year") or 0, document.get("evaluation_year") or 0, document["project_id"])
        for fact_index, fact in enumerate(document["amounts"]):
            key = (document["project_key"], fact["year"], fact["scope"])
            rank = (sheet, STAGE_RANK.get(fact["stage"], 0), *order)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, doc_index, fact_index)
            stage_key = (*key, fact["stage"])
            if stage_key not in stage_best or (sheet, *order) > stage_best[stage_key][0]:
                stage_best[stage_key] = ((sheet, *order), doc_index, fact_index)
    for _, doc_index, fact_index in best.values():
        documents[doc_index]["amounts"][fact_index]["best"] = True
    for _, doc_index, fact_index in stage_best.values():
        documents[doc_index]["amounts"][fact_index]["stage_best"] = True
    # 前の年度からの増減。どちらの年度も集計に使う値（best）で比べる。比は今年 ÷ 前年。
    def best_yen(key: tuple) -> int | None:
        found = best.get(key)
        return documents[found[1]]["amounts"][found[2]]["yen"] if found else None

    for document in documents:
        document["cost_prev_yen"] = None
        document["cost_change_ratio"] = None
        if document["cost_yen"] is None:
            continue
        current = best_yen((document["project_key"], document["cost_year"], document["cost_scope"]))
        previous = best_yen((document["project_key"], document["cost_year"] - 1, document["cost_scope"]))
        if current is None or previous is None:
            continue
        document["cost_prev_yen"] = previous
        document["cost_change_ratio"] = round(current / previous, 4) if previous > 0 else None
    add_history_and_flags(documents)


def add_history_and_flags(documents: list[dict]) -> None:
    """同じ事業の年度の並び（名前の履歴、最初と最後の年度）、前の年度と同じ評価の文、分析の印を付ける。"""
    latest = max((document.get("evaluation_year") or 0 for document in documents), default=0)
    groups: dict[str, list[dict]] = {}
    for document in documents:
        groups.setdefault(document["project_key"], []).append(document)
    for members in groups.values():
        members.sort(key=lambda document: (document.get("evaluation_year") or 0, document["project_id"]))
        names: list[str] = []
        for document in members:
            if document["project_name"] and document["project_name"] not in names:
                names.append(document["project_name"])
        years = [document["evaluation_year"] for document in members if document.get("evaluation_year")]
        by_year: dict[int, list[dict]] = {}
        for document in members:
            by_year.setdefault(document.get("evaluation_year") or 0, []).append(document)
        for document in members:
            document["project_names"] = names[:10]
            document["project_first_year"] = min(years) if years else None
            document["project_last_year"] = max(years) if years else None
            mine = document.pop("_evaluation_sentences", [])
            ratio = None
            year = document.get("evaluation_year") or 0
            if year and len(mine) >= REPEAT_MIN_SENTENCES:
                for previous in by_year.get(year - 1, []):
                    theirs = set(previous.get("_evaluation_sentences") or previous.get("_sentences_done") or [])
                    if theirs:
                        same = sum(1 for sentence in mine if sentence in theirs) / len(mine)
                        ratio = same if ratio is None else max(ratio, same)
            document["_sentences_done"] = mine
            document["text_repeat_ratio"] = round(ratio, 4) if ratio is not None else None
    for document in documents:
        document.pop("_sentences_done", None)
        outcome = document["outcome_ratio_min"]
        flags = []
        if outcome is not None and outcome <= 0.5:
            flags.append("outcome_below_half")
        if outcome is not None and outcome < 1 and document["direction"] == "expand":
            flags.append("expand_below_target")
        if outcome is not None and outcome < 1 and (document["cost_change_ratio"] or 0) >= 1.1:
            flags.append("cost_up_below_target")
        # 指標を活動と成果に分ける様式でだけ見る（分けない様式では成果指標の有無が分からない）。
        kinds = {row["kind"] for row in document["indicators"]}
        if document["cost_yen"] and kinds & {"activity", "outcome"} and not any(
                row["kind"] == "outcome" and parse_number(row["target"]) is not None for row in document["indicators"]):
            flags.append("no_outcome_target")
        if (document["text_repeat_ratio"] or 0) >= REPEAT_THRESHOLD:
            flags.append("repeated_text")
        if len(document["project_names"]) > 1:
            flags.append("renamed")
        if latest and document["project_last_year"] and document["project_last_year"] < latest:
            flags.append("not_in_latest")
        document["flags"] = flags


def read_municipalities(path: Path) -> dict[str, dict]:
    """全国地方公共団体コード（5 桁）→ 名前と都道府県。municipality_master.tsv から。"""
    rows: dict[str, dict] = {}
    if not path.is_file():
        return rows
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t") if lines else []
    for line in lines[1:]:
        values = dict(zip(header, line.split("\t")))
        code = values.get("jis_code", "")
        if code:
            rows[code] = {"name": values.get("name", ""), "pref_code": values.get("pref_code", ""), "pref_name": values.get("pref_name", "")}
    return rows


def iter_record_files(display_dir: Path, slugs: set[str] | None = None) -> Iterator[tuple[str, Path]]:
    records_dir = display_dir / "records"
    for slug_dir in sorted(path for path in records_dir.iterdir() if path.is_dir()) if records_dir.is_dir() else []:
        if slugs and slug_dir.name not in slugs:
            continue
        for path in sorted(slug_dir.glob("*.json")):
            yield slug_dir.name, path


ID_PAGES_RE = re.compile(r":p(\d+)(?:-(\d+))?$")


def document_meta(metadata: dict[str, dict], slug: str, doc_id: str, page: int) -> dict:
    """文書の情報。検索の ID は、その頁を含む 1 件（頁、または事業のシートの頁の範囲「:p21-22」）、
    無ければ文書全体の ID、どちらも無ければ最初の 1 件。"""
    entry = metadata.get(f"{slug}:{doc_id}") or {}
    ids = entry.get("ids") or []
    document_id = ""
    if page > 0:
        for candidate in ids:
            match = ID_PAGES_RE.search(candidate)
            if match and int(match.group(1)) <= page <= int(match.group(2) or match.group(1)):
                document_id = candidate
                break
    if not document_id:
        whole = f"hyoka:{slug}:{doc_id}"
        document_id = whole if whole in ids else ids[0] if ids else ""
    return {**entry, "document_id": document_id}


def build_documents(display_dir: Path, metadata: dict[str, dict], municipalities: dict[str, dict],
                    slugs: set[str] | None = None) -> Iterator[list[dict]]:
    """自治体ごとに、索引に入れる事業の一覧を返す（best の印を付けたあと）。"""
    current = ""
    batch: list[dict] = []
    for slug, path in iter_record_files(display_dir, slugs):
        if slug != current and batch:
            mark_best(batch)
            yield batch
            batch = []
        current = slug
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("slug") != slug or doc.get("doc_id") != path.stem:
            continue
        municipality = municipalities.get(slug[:5], {})
        for record in doc.get("records") or []:
            if not isinstance(record, dict) or not str(record.get("id") or ""):
                continue
            meta = document_meta(metadata, slug, path.stem, int(record.get("page") or 0))
            batch.append(project_document(slug, doc, record, meta, municipality))
    if batch:
        mark_best(batch)
        yield batch


def metadata_from_manifests(roots: Iterable[Path]) -> dict[str, dict]:
    """手元で試すときの文書の情報（取得の manifest から）。検索の ID は文書全体の形にする。"""
    metadata: dict[str, dict] = {}
    for root in roots:
        for manifest_path in sorted(root.glob("*/manifest.json")):
            slug = manifest_path.parent.name
            documents = json.loads(manifest_path.read_text(encoding="utf-8")).get("documents") or {}
            for doc_id, entry in documents.items():
                if isinstance(entry, dict):
                    metadata[f"{slug}:{doc_id}"] = {
                        "fiscal_year": entry.get("fiscal_year"), "kind": entry.get("kind") or "",
                        "title": entry.get("title") or entry.get("link_text") or "", "source_url": entry.get("source_url") or "",
                        "ids": [f"hyoka:{slug}:{doc_id}"],
                    }
    return metadata


def index_body(settings: dict[str, Any]) -> dict[str, Any]:
    """索引の設定（文書検索と同じ分かち書き）と項目の型。"""
    text = {"type": "text", "analyzer": "ja_cjk", "fields": {
        "keyword": {"type": "keyword", "ignore_above": 512},
        "ngram": {"type": "text", "analyzer": "ja_ngram", "search_analyzer": "ja_cjk"},
    }}
    keyword = {"type": "keyword"}
    return {
        "settings": settings,
        "mappings": {
            "dynamic": False,
            "_source": {"excludes": ["search_text"]},
            "properties": {
                "project_id": keyword, "schema_version": {"type": "integer"}, "record_id": keyword, "doc_id": keyword,
                "document_id": keyword, "document_title": {"type": "text", "analyzer": "ja_cjk"},
                "document_fiscal_year": {"type": "integer"}, "document_kind": keyword,
                "slug": keyword, "municipality_code": keyword, "municipality_name": keyword, "pref_code": keyword, "pref_name": keyword,
                "project_name": text, "project_key": keyword, "project_code": keyword, "department": text,
                "purpose": {"type": "text", "analyzer": "ja_cjk"},
                # 評価区分は機械の抽出だと文が入ることがある。長い値で投入を止めない
                "evaluation_year": {"type": "integer"}, "rating": {"type": "keyword", "ignore_above": 256}, "direction": keyword,
                "policy_path": {"type": "object", "enabled": False},
                "policy_names": {"type": "text", "analyzer": "ja_cjk", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
                "policy_top": {"type": "keyword", "ignore_above": 512}, "policy_leaf": {"type": "keyword", "ignore_above": 512},
                "level": keyword, "variant_base": {"type": "keyword", "ignore_above": 512}, "variant_label": keyword,
                "is_main": {"type": "boolean"}, "achievement": keyword, "overall": keyword,
                "direction_text": {"type": "text", "analyzer": "ja_cjk"}, "aspects": {"type": "keyword", "ignore_above": 256}, "method": keyword,
                "page": {"type": "integer"}, "location": {"type": "keyword", "index": False},
                "source_url": {"type": "keyword", "index": False}, "source_page_link": {"type": "keyword", "index": False},
                "cost_yen": {"type": "long"}, "cost_year": {"type": "integer"}, "cost_stage": keyword, "cost_scope": keyword,
                "cost_raw": {"type": "keyword", "index": False}, "cost_unit": {"type": "keyword", "index": False},
                "cost_prev_yen": {"type": "long"}, "cost_change_ratio": {"type": "float"},
                "staff_fte": {"type": "float"}, "has_amounts": {"type": "boolean"},
                "outcome_ratio_min": {"type": "float"}, "indicator_ratio_min": {"type": "float"},
                "flags": keyword, "project_names": {"type": "keyword", "ignore_above": 256},
                "project_first_year": {"type": "integer"}, "project_last_year": {"type": "integer"},
                "text_repeat_ratio": {"type": "float"},
                "indicators": {"type": "object", "enabled": False},
                "amounts": {"type": "nested", "properties": {
                    "year": {"type": "integer"}, "stage": keyword, "scope": keyword, "label": {"type": "keyword", "ignore_above": 256},
                    "raw": {"type": "keyword", "index": False}, "unit": keyword, "yen": {"type": "long"},
                    "best": {"type": "boolean"}, "stage_best": {"type": "boolean"},
                    "project_id": {"type": "keyword", "index": False}, "project_name": {"type": "keyword", "index": False},
                    "slug": keyword, "pref_code": keyword, "municipality_name": keyword,
                    "department": {"type": "keyword", "ignore_above": 512}, "direction": keyword,
                    "policy_top": {"type": "keyword", "ignore_above": 512}, "policy_leaf": {"type": "keyword", "ignore_above": 512},
                    "variant_base": {"type": "keyword", "ignore_above": 512},
                }},
                "search_text": {"type": "text", "analyzer": "ja_cjk"},
            },
        },
    }
