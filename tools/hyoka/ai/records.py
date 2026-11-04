"""AI の答えを、共通形式 1.2.0 に沿った事業の記録にする。

- 事業名の無い記録は捨てる。
- 値はその回に渡した升目の文字と照合し、空白で区切った各部分が原典（空白・印・升目の番号を除いたもの）に
  無ければ画面に出さない（`unverified` に残す）。AI の言い換えや作り話を載せないための機械の照合。
- 同じ文書で、事業名が同じで頁が 2 頁以内の記録は 1 件にまとめる（区切りをまたいだ事業）。
- 方向性の欄名が評価の欄（「総合評価」「判定」など）なら、共通形式の決まり（評価区分が方針の意味を兼ねるだけなら
  方向性は文中の記載）に従い、方向性の専用欄としては出さない。
- 記録には AI が書き写したこと（型・指示文の版・升目の版・凡例の有無）を残す。人の確認はしていない。
  型は区切りごとに実際に使ったもの（区切りに型が無ければ extraction の型）。別の型の区切りをまとめた記録は両方の名を残す。
"""

from __future__ import annotations

import re
import unicodedata

from prompts import FIELDS

SCHEMA_VERSION = "1.2.0"
GRID_VERSION = "g5"
MARKS = set("●○◎■□☑☐✓✔◯◉")
WS = re.compile(r"\s+")
LINE_PREFIX = re.compile(r"^(?:\d+,\d+(?:\+\d+x\d+)?: |\[\d\d,\d\d\] |\[縦\] )")
DIRECTION_LABEL = re.compile(r"方向|方針|今後|次年度|来年度|改善|取組|取り組み|見直し|対応")
RATING_LABEL = re.compile(r"評価|判定|区分|結果")
LABELS = {"project_name": "事業名", "department": "担当部署", "future_direction": "今後の方向性",
          "evaluation_rating": "評価区分・点数", "purpose": "目的"}


def norm(text: object) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    return WS.sub("", "".join(c for c in text if c not in MARKS))


def source_norm(text: str) -> str:
    """升目の番号・位置・見出しの行を除いた原典の文字。隣り合う升目にまたがる値も照合できる。"""
    lines = []
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("=== 頁"):
            continue
        lines.append(LINE_PREFIX.sub("", line))
    return norm("".join(lines))


def verified(value: str, source: str) -> bool:
    parts = [norm(p) for p in str(value).split()]
    parts = [p for p in parts if p]
    return all(p in source for p in parts) if parts else False


def clean(value: object) -> str:
    return str(value or "").strip()


def direction_is_dedicated(label: str) -> bool:
    """方向性を写した欄が方向性の専用欄か。欄名が分からなければ専用欄とみなす。"""
    label = norm(label)
    if not label or DIRECTION_LABEL.search(label):
        return True
    return not RATING_LABEL.search(label)


def assemble_document(meta: dict, chunks: list[dict], extraction: dict) -> tuple[list[dict], dict]:
    """1 文書の記録と集計。

    meta: slug・code・name・doc_id・source_url・page_url
    chunks: [{"index", "pages", "text"（渡した升目の一覧）, "parsed"（答え）か "error"}]
    extraction: 記録に残す方法（model・prompt_version・legend など）。区切りの "model" があればそちらを記録に残す
    """
    candidates: list[dict] = []
    summary = {"doc_id": meta["doc_id"], "chunks": len(chunks), "errors": 0, "kinds": [], "records": 0,
               "dropped_no_name": 0, "unverified_values": 0}
    for chunk in sorted(chunks, key=lambda c: c["index"]):
        parsed = chunk.get("parsed")
        if not isinstance(parsed, dict):
            summary["errors"] += 1
            continue
        summary["kinds"].append(str(parsed.get("document_kind") or ""))
        source = source_norm(chunk.get("text") or "")
        pages = [int(p) for p in chunk.get("pages") or []]
        for item in parsed.get("records") or []:
            if not isinstance(item, dict):
                continue
            name = clean(item.get("project_name"))
            if not name:
                summary["dropped_no_name"] += 1
                continue
            page = item.get("page") if isinstance(item.get("page"), int) else None
            if pages and page not in pages:
                page = pages[0]
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            values, unverified = {}, {}
            for key in FIELDS:
                value = clean(item.get(key))
                if not value:
                    continue
                if verified(value, source):
                    values[key] = (value, clean(labels.get(key)))
                else:
                    unverified[key] = value
            if "project_name" not in values:
                summary["unverified_values"] += len(unverified)
                summary["dropped_no_name"] += 1
                continue
            candidates.append({"page": page, "values": values, "unverified": unverified,
                               "models": {chunk.get("model") or extraction.get("model") or ""}})
    # 区切りをまたいだ同じ事業をまとめる
    merged: list[dict] = []
    for cand in candidates:
        key = norm(cand["values"]["project_name"][0])
        for prev in merged:
            if norm(prev["values"]["project_name"][0]) == key and abs((prev["page"] or 0) - (cand["page"] or 0)) <= 2:
                for field, pair in cand["values"].items():
                    prev["values"].setdefault(field, pair)
                for field, value in cand["unverified"].items():
                    if field not in prev["values"]:
                        prev["unverified"].setdefault(field, value)
                pages = [p for p in (prev["page"], cand["page"]) if p]
                prev["page"] = min(pages) if pages else None
                prev["models"] |= cand["models"]
                break
        else:
            merged.append(cand)
    records = []
    counters: dict[int, int] = {}
    for cand in sorted(merged, key=lambda c: (c["page"] or 0)):
        page = cand["page"] or 0
        counters[page] = counters.get(page, 0) + 1
        fields = {}
        raw_fields = []
        for key, (value, label) in cand["values"].items():
            field = {"status": "present", "field_kind": "dedicated", "value": value, "raw_text": value,
                     "raw_label": label or None, "verified": True}
            if key == "year_text":
                raw_fields.append({"label": label or "年度の記載", "standard_key": "year_text", "content": field})
                continue
            if key == "future_direction":
                field["value"] = {"raw_term": value, "mapping_status": "unmapped"}
                if not direction_is_dedicated(label):
                    # 評価の欄が方向性を兼ねるだけ: 方向性は文中の記載として扱い、欄は原文の欄に残す
                    raw_fields.append({"label": label, "standard_key": "future_direction", "content": dict(field, value=value)})
                    field = {"status": "represented_elsewhere", "field_kind": "embedded", "value": None,
                             "raw_text": None, "raw_label": label, "related_fields": [label]}
            fields[key] = field
        summary["unverified_values"] += len(cand["unverified"])
        model = " + ".join(sorted(m for m in cand["models"] if m))
        records.append({
            "schema_version": SCHEMA_VERSION,
            "record_id": f"ai-{meta['doc_id']}-p{page:04d}-{counters[page]:02d}",
            "record_status": "ai_transcribed",
            "layout": "ai_transcription",
            "municipality": {"code": meta.get("code") or "", "name": meta.get("name") or "", "slug": meta["slug"]},
            "source_document_ids": [meta["doc_id"]],
            "source_url": meta.get("source_url") or "",
            "page_url": meta.get("page_url") or "",
            "record_evidence": [{"doc_id": meta["doc_id"], "locator": {"type": "pdf", "page": page}}],
            "fields": fields,
            "raw_fields": raw_fields,
            "unverified": cand["unverified"],
            "extraction": {"method": "ai_transcription", "grid_version": GRID_VERSION, "human_reviewed": False, **extraction,
                           **({"model": model} if model else {})},
        })
    summary["records"] = len(records)
    return records, summary
