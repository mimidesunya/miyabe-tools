#!/usr/bin/env python3
"""事務事業評価を画面に出すための控えを作る（入力は読むだけ、書くのは出力先だけ）。

画面（PHP）からは取得の保存先 `work/hyoka`・抽出の出力先 `work/hyoka-extracted` が
見えないので、要る分だけを縮めて `data/hyoka` に置く。

- `status.json`: 自治体ごとの取り込みの状態（取得・本文・OCR・検索・事業の記録の数）。
  収集・公開状況（/status/）と自治体一覧（/municipalities/）が読む。
- `records/<slug>/<doc_id>.json`: 抽出した事業の記録（共通形式 1.2.0）を、
  詳細画面に出す欄だけに縮めたもの。欄名は原典のまま、値は専用欄の原文だけを写す。
  欄の中の折り返し（表のセルの幅で切れた改行）だけは詰める。推定は使わない。
  様式の定義で機械が取り出した記録（`work/hyoka-extracted`）のある自治体はそれを、無い自治体は
  AI が書き写して原典と照合した記録（`work/hyoka-ai`、doc/hyoka-ai-extraction.md）を使う。

検索に載った本数は索引づくりのログ（`[HYOKA]` の監査行）から取る。全量の rebuild の
ログのあとに update のログを古い順に渡せば、後のもので自治体ごとに上書きする。

    python tools/hyoka/build_display_data.py --work-root work/hyoka \\
        --records-root work/hyoka-extracted --out data/hyoka \\
        --index-log work/hyoka-index-logs/rebuild-20261031071938.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT / "tools" / "gijiroku"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "hyoka" / "forms"))

import pdf_ocr  # noqa: E402
import check as form_check  # noqa: E402

JST = timezone(timedelta(hours=9))
OCR_SOURCE = "ocr:ndlocr-lite"
DOC_ID_RE = re.compile(r"[0-9a-f]{16}")
SLUG_RE = re.compile(r"[0-9]{5}-[a-z0-9-]+")

# 共通形式の標準の欄。画面に出す順と、標準の名前。
FIELD_ORDER = (
    ("project_name", "事業名"),
    ("project_code", "事業番号"),
    ("department", "担当部署"),
    ("evaluation_year", "評価年度"),
    ("target_year", "対象年度"),
    ("evaluation_actor", "評価主体・段階"),
    ("purpose", "目的"),
    ("description", "事業内容"),
    ("policy_alignment", "上位計画との関係"),
    ("implementation_period", "事業の期間"),
    ("cost", "事業費"),
    ("funding", "財源内訳"),
    ("staff", "人員・人件費"),
    ("indicators", "指標・目標と実績"),
    ("performance_result", "実施結果・成果"),
    ("evaluation_rating", "評価区分・点数"),
    ("evaluation_reason", "評価の理由・所見"),
    ("future_direction", "今後の方向性"),
    ("issues", "課題"),
)
# 一覧の 1 行に出す欄。
SUMMARY_FIELDS = ("department", "evaluation_year", "target_year", "evaluation_rating", "future_direction")

# この字で始まる行は箇条書き・番号の頭なので、前の行に詰めない。
# 長音「ー」は語の途中で折り返されることがあるので頭に数えない。
LIST_HEAD_RE = re.compile(r"^[○●◎◯・■□◆◇▼▽▲△※★☆\-－①-⑳⑴-⒇❶-❿(（「【〔\[<＜]|^[0-9０-９]+[.．)）、]|^[一二三四五六七八九十]+[、.．]")
# この字で終わる行のあとは文の切れ目なので、改行を残す。
SENTENCE_END = tuple("。．！？!?」』）)】〕")


def now_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def clean_label(value: object) -> str:
    """原典の欄名。縦書きの欄（1 字ずつ改行）や折り返しを詰めて 1 行にする。"""
    return re.sub(r"\s*\n\s*", "", str(value or "")).strip()


def join_wrapped_lines(value: object) -> str:
    """セルの幅で切れた改行を詰める。文の終わりと箇条書きの頭の改行は残す。"""
    lines = [line.strip() for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        if out and out[-1] != "" and not out[-1].endswith(SENTENCE_END) and not LIST_HEAD_RE.match(line):
            out[-1] += line
        else:
            out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def field_text(field: dict) -> str:
    """専用欄の原文。原文が無ければ文字・数の値だけを使い、組み立てた値（費用の配列など）からは作らない。"""
    raw = field.get("raw_text")
    if isinstance(raw, str) and raw.strip():
        return join_wrapped_lines(raw)
    value = field.get("value")
    if isinstance(value, bool):
        return ""
    if isinstance(value, (str, int)):
        return join_wrapped_lines(value)
    if isinstance(value, dict) and isinstance(value.get("raw_term"), str):
        return join_wrapped_lines(value["raw_term"])
    return ""


def record_location(record: dict) -> dict:
    """記録の場所。PDF は 1 始まりの物理頁、Excel はシートとセル。"""
    for evidence in record.get("record_evidence") or []:
        locator = evidence.get("locator") or {}
        if locator.get("type") == "pdf" and isinstance(locator.get("page"), int):
            return {"page": locator["page"]}
        if locator.get("type") == "spreadsheet":
            return {"sheet": str(locator.get("sheet") or ""), "cells": str(locator.get("cells") or "")}
    return {}


def compact_record(record: dict) -> dict:
    fields = record.get("fields") or {}
    shown = []
    for key, name in FIELD_ORDER:
        field = fields.get(key)
        if not isinstance(field, dict) or field.get("status") != "present":
            continue
        text = field_text(field)
        if text == "":
            continue
        shown.append({"key": key, "name": name, "label": clean_label(field.get("raw_label")), "text": text})
    raw = []
    for item in record.get("raw_fields") or []:
        content = item.get("content") or {}
        if content.get("status") != "present":
            continue
        text = field_text(content)
        if text == "":
            continue
        raw.append({"label": clean_label(item.get("label") or content.get("raw_label")), "text": text})
    model = str((record.get("extraction") or {}).get("model") or "")  # AI の記録の断り書きに出す型
    return {
        "id": str(record.get("record_id") or ""),
        "layout": str(record.get("layout") or ""),
        "status": str(record.get("record_status") or ""),
        **({"model": model} if model else {}),
        **record_location(record),
        "fields": shown,
        "raw_fields": raw,
    }


# 様式ごとの読み取り（tools/hyoka/forms）の記録の、事業の識別の項目。標準の欄に当たるものと、そのほかの欄。
FORM_PROJECT_FIELDS = (
    ("name", "project_name", "事業名"),
    ("code", "project_code", "事業番号"),
    ("department", "department", "担当部署"),
    ("purpose", "purpose", "目的"),
    ("plan_alignment", "policy_alignment", "上位計画との関係"),
    ("period", "implementation_period", "事業の期間"),
)
FORM_RAW_FIELDS = (("name_budget", "予算上の事業名"), ("account", "予算科目"), ("target", "対象"), ("legal_basis", "根拠法令"))
STAGE_NAMES = {"budget": "予算", "plan": "計画", "settlement": "決算", "estimate": "見込", "unstated": ""}
DIRECTION_NAMES = {"expand": "拡充", "continue": "継続", "maintain": "現状維持", "improve": "改善・見直し", "reduce": "縮小",
                   "integrate": "統合", "suspend": "休止", "abolish": "廃止", "complete": "完了"}
SCOPE_NAMES = {"direct": "事業費（人件費を除く）", "personnel": "人件費", "total": "総費用（人件費を含む）",
               "sub_project": "細事業", "breakdown": "内訳"}
SOURCE_NAMES = {"national": "国", "prefecture": "都道府県", "bond": "地方債", "fund": "基金", "user_fee": "受益者負担",
                "other_specific": "その他の特定財源", "general": "一般財源"}
KIND_NAMES = {"target": "対象", "activity": "活動", "outcome": "成果", "unknown": ""}


def direction_text(evaluation: dict) -> str:
    """方向性の原文。「Ⅰ」のような記号だけなら、読み取りが凡例から決めた区分を添える。"""
    raw = join_wrapped_lines(evaluation.get("direction_raw"))
    name = DIRECTION_NAMES.get(str(evaluation.get("direction") or ""), "")
    if name and len(re.sub(r"\s", "", raw)) <= 3 and name not in raw:
        return f"{raw}（{name}）"
    return raw


def form_values_text(values: object) -> str:
    items = values if isinstance(values, list) else [values]
    return join_wrapped_lines(" ".join(str(item.get("raw") or "") for item in items if isinstance(item, dict)).strip())


def pivot_table(rows: list[dict], kind_key: str, kind_names: dict) -> dict:
    """金額などの行（1 値 1 行）を、年度と段階の列、欄名の行の表にする。値は原文のまま。"""
    columns: list[tuple] = []
    labels: list[str] = []
    cells: dict[tuple, str] = {}
    units: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for row in rows:
        column = (int(row.get("fiscal_year") or 0), str(row.get("stage") or "unstated"))
        label = clean_label(row.get("label")) or kind_names.get(str(row.get(kind_key) or ""), "")
        if column not in columns:
            columns.append(column)
        if label not in labels:
            labels.append(label)
        cells.setdefault((label, column), str(row.get("raw") or ""))
        units.setdefault(label, str(row.get("unit") or ""))
        kinds.setdefault(label, kind_names.get(str(row.get(kind_key) or ""), ""))
    # 年度は古い順にそろえ、同じ年度の中の段階（計画、予算、決算など）は原典の列の並びのまま
    first_seen = {column: index for index, column in enumerate(columns)}
    columns.sort(key=lambda column: (column[0], first_seen[column]))
    common = {unit for unit in units.values()}
    return {
        "unit": common.pop() if len(common) == 1 else "",
        "columns": [{"year": year, "stage": STAGE_NAMES.get(stage, stage)} for year, stage in columns],
        "rows": [{"label": label, "kind": kinds[label], "unit": units[label] if len({*units.values()}) > 1 else "",
                  "values": [cells.get((label, column), "") for column in columns]} for label in labels],
    }


def compact_form_record(record: dict, doc_id: str, number: int) -> dict:
    """様式ごとの読み取りの記録（形 2.0）を、詳細画面に出す形にする。

    一覧の札に使う標準の欄（事業名、担当、年度、評価、方向性）は共通形式の控えと同じ形で持ち、
    金額、財源、人員、指標、評価の段階は表の形で添える。値は原典の升目の原文のまま。
    """
    project = record.get("project") or {}
    fields = []
    for key, standard, name in FORM_PROJECT_FIELDS:
        text = form_values_text(project.get(key))
        if text:
            fields.append({"key": standard, "name": name, "label": "", "text": text})
    for key, name in (("evaluation_year", "評価年度"), ("target_year", "対象年度")):
        year = record.get(key)
        if isinstance(year, int) and year > 0:
            fields.append({"key": key, "name": name, "label": "", "text": f"{year}年度"})
    evaluations = [row for row in record.get("evaluations") or [] if isinstance(row, dict)]
    # 評価が年度や段階で複数あるときは、最後（評価した年度、最後の段階）のものを札に出す
    rating = next((row.get("rating_raw") for row in reversed(evaluations) if row.get("rating_raw")), "")
    final = next((row for row in reversed(evaluations) if row.get("direction_raw")), {})
    if rating:
        fields.append({"key": "evaluation_rating", "name": "評価区分・点数", "label": "", "text": join_wrapped_lines(rating)})
    if final:
        fields.append({"key": "future_direction", "name": "今後の方向性", "label": "", "text": direction_text(final)})
    raw_fields = [{"label": name, "text": text} for key, name in FORM_RAW_FIELDS if (text := form_values_text(project.get(key)))]
    pairs, problems = form_check.checks(record)
    pages = [page for page in record.get("pages") or [] if isinstance(page, int)]
    return {
        "id": f"{doc_id}-{number}",
        "layout": "form_reader",
        "status": "",
        **({"page": pages[0]} if pages else {}),
        # 1 事業が何頁にもわたる様式（川崎市は 2〜3 頁）で、MCP がその頁の表をまとめて返すため
        **({"pages": pages} if len(pages) > 1 else {}),
        "fields": fields,
        "raw_fields": raw_fields,
        "amounts": pivot_table(record.get("amounts") or [], "scope", SCOPE_NAMES) if record.get("amounts") else None,
        "funding": pivot_table(record.get("funding") or [], "source", SOURCE_NAMES) if record.get("funding") else None,
        "staff": pivot_table(record.get("staff") or [], "label", {}) if record.get("staff") else None,
        "indicators": [{
            "name": clean_label(row.get("name")), "kind": KIND_NAMES.get(str(row.get("kind") or ""), "") or clean_label(row.get("kind_raw")),
            "unit": str(row.get("unit") or ""),
            "values": [{"year": int(value.get("fiscal_year") or 0), "target": str(value.get("target_raw") or ""),
                        "actual": str(value.get("actual_raw") or ""), "rate": str(value.get("rate_raw") or "")}
                       for value in row.get("values") or [] if isinstance(value, dict)],
        } for row in record.get("indicators") or [] if isinstance(row, dict)],
        "evaluations": [{
            "stage": clean_label(row.get("stage_raw")), "rating": join_wrapped_lines(row.get("rating_raw")),
            "direction": direction_text(row),
            # 読み取りが凡例から決めた区分（DIRECTION_NAMES の鍵か other）。事業ごとの索引の絞り込みに使う
            **({"direction_code": str(row.get("direction"))} if str(row.get("direction") or "") not in ("", "none") else {}),
            "aspects": [f"{clean_label(item.get('aspect_raw'))}: {clean_label(item.get('rating_raw'))}"
                        for item in row.get("aspects") or [] if isinstance(item, dict) and item.get("aspect_raw")],
        } for row in evaluations if row.get("status") != "blank" or row.get("direction_raw") or row.get("rating_raw")],
        "checks": {"pairs": pairs, "problems": problems[:10]},
    }


def copy_sheets(ai_records_root: Path, out: Path, slugs: set[str] | None = None) -> dict[str, int]:
    """シートの全部（頁の表の構造）を画面から読める置き場へ写す。自治体ごとの文書の数を返す。

    変わっていないファイルは写さない。AI の置き場に無い自治体の控えは消さない。slugs を渡すとその自治体だけ。
    """
    counts: dict[str, int] = {}
    keep: set[Path] = set()
    for source in sorted(ai_records_root.glob("*/layouts/*.json.gz")):
        slug = source.parent.parent.name
        if not SLUG_RE.fullmatch(slug) or (slugs is not None and slug not in slugs):
            continue
        target = out / "sheets" / slug / source.name
        keep.add(target)
        counts[slug] = counts.get(slug, 0) + 1
        stat = source.stat()
        if target.is_file() and (target.stat().st_size, target.stat().st_mtime_ns) == (stat.st_size, stat.st_mtime_ns):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        shutil.copy2(source, temporary)
        os.chmod(temporary, 0o644)
        temporary.replace(target)
    for stale in (out / "sheets").glob("*/*.json.gz"):
        if stale.parent.name in counts and stale not in keep:
            stale.unlink()
    return counts


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_index_logs(paths: list[Path]) -> dict:
    """索引づくりのログから、自治体ごとの監査（検索に載った本数）と世代を読む。後のログで上書きする。"""
    by_slug: dict[str, dict] = {}
    built = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("[HYOKA] "):
                try:
                    audit = json.loads(line[len("[HYOKA] "):])
                except json.JSONDecodeError:
                    continue
                slug = str(audit.get("slug") or "")
                if slug:
                    by_slug[slug] = {"indexed_documents": int(audit.get("yielded") or 0),
                                     "indexed_entries": int(audit.get("indexed_documents") or 0),
                                     "unexplained_drop": int(audit.get("unexplained_drop") or 0)}
            elif line.startswith("[DONE] "):
                match = re.search(r"index=(\S+)", line)
                if match:
                    built = {"index": match.group(1), "log": path.name,
                             "finished_at": datetime.fromtimestamp(path.stat().st_mtime, JST).strftime("%Y-%m-%d %H:%M")}
    return {"by_slug": by_slug, "last_build": built}


def municipality_status(work_dir: Path) -> dict:
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    documents = manifest.get("documents") or {}
    attempts = pdf_ocr.load_attempts(work_dir)
    last_run = manifest.get("last_run") if isinstance(manifest.get("last_run"), dict) else {}
    counts = {"documents": 0, "text_ok": 0, "ocr_done": 0, "ocr_waiting": 0, "ocr_failed": 0, "no_text": 0, "gone": 0, "pages": 0}
    kinds: dict[str, int] = {}
    years: list[int] = []
    for doc_id, entry in documents.items():
        if not isinstance(entry, dict):
            continue
        counts["documents"] += 1
        if entry.get("status") == "gone":
            counts["gone"] += 1
        if entry.get("file_type") == "pdf" and isinstance(entry.get("pages"), int):
            counts["pages"] += entry["pages"]
        kind = str(entry.get("kind") or "")
        kinds[kind] = kinds.get(kind, 0) + 1
        status = entry.get("text_status")
        if status == "ok":
            counts["text_ok"] += 1
            if entry.get("text_source") == OCR_SOURCE:
                counts["ocr_done"] += 1
            year = entry.get("fiscal_year")
            if isinstance(year, int) and not isinstance(year, bool):
                years.append(year)
        elif status == "needs_ocr":
            tried = attempts.get(str(doc_id)) or {}
            given_up = tried.get("status") == "failed" and int(tried.get("attempts") or 0) >= pdf_ocr.MAX_ATTEMPTS
            counts["ocr_failed" if given_up else "ocr_waiting"] += 1
        else:
            counts["no_text"] += 1
    return {
        "slug": str(manifest.get("slug") or work_dir.name),
        "code": str(manifest.get("jis_code") or ""),
        "name": str(manifest.get("name") or ""),
        "entry_url": str(manifest.get("entry_url") or ""),
        "fetch_status": str(last_run.get("status") or ""),
        "fetched_at": str(last_run.get("finished_at") or manifest.get("updated_at") or ""),
        **counts,
        "kinds": kinds,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
    }


def write_form_records(slug: str, path: Path, manifest_path: Path, out: Path, generated_at: str) -> tuple[int, set[Path]]:
    """様式ごとの読み取りの記録（records.jsonl、1 行に doc_id と 1 事業）から、文書ごとの控えを書く。"""
    by_doc: dict[str, list[dict]] = {}
    total = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            doc_id = str(record.get("doc_id") or "")
            if not DOC_ID_RE.fullmatch(doc_id):
                continue
            total += 1
            items = by_doc.setdefault(doc_id, [])
            items.append(compact_form_record(record, doc_id, len(items) + 1))
    documents = json.loads(manifest_path.read_text(encoding="utf-8")).get("documents") or {}
    written: set[Path] = set()
    for doc_id, items in by_doc.items():
        items.sort(key=lambda item: (item.get("page") or 0))
        target = out / "records" / slug / f"{doc_id}.json"
        write_json_atomic(target, {"slug": slug, "doc_id": doc_id, "source_url": str((documents.get(doc_id) or {}).get("source_url") or ""),
                                   "generated_at": generated_at, "schema_version": "2.0-form", "records": items})
        written.add(target)
    return total, written


def build(work_root: Path, records_root: Path | None, out: Path, index_logs: list[Path],
          ai_records_root: Path | None = None, overlay: dict | None = None) -> dict:
    """overlay を渡すと、その自治体（slugs）だけ別の保存先（root）から状態を作り、様式ごとの読み取りの記録
    （records_root）とシートの全部（layouts_root）を使う。調査から集め直した自治体を 1 つずつ公開するため。"""
    index = read_index_logs(index_logs)
    overlay_slugs = set((overlay or {}).get("slugs") or [])
    rows = []
    for work_dir in sorted(p for p in work_root.iterdir() if p.is_dir() and SLUG_RE.fullmatch(p.name)):
        if (work_dir / "manifest.json").is_file() and work_dir.name not in overlay_slugs:
            rows.append(municipality_status(work_dir))
    for slug in sorted(overlay_slugs):
        work_dir = Path(overlay["root"]) / slug
        if (work_dir / "manifest.json").is_file():
            rows.append(municipality_status(work_dir))
    rows.sort(key=lambda row: row["slug"])
    # 保存先を付け忘れた容器（空のディレクトリ）で走らせて、画面の控えを空で上書きしない。
    if not rows:
        raise ValueError(f"manifest が 1 つも無い: {work_root}")
    if records_root is not None and not any(records_root.glob("*/records.jsonl")):
        raise ValueError(f"抽出の出力（*/records.jsonl）が無い: {records_root}。記録なしで作るなら --no-records")

    record_counts: dict[str, int] = {}
    record_sources: dict[str, str] = {}
    written: set[Path] = set()
    generated_at = now_text()
    for slug in sorted(overlay_slugs):
        path = Path(overlay["records_root"]) / slug / "records.jsonl"
        if path.is_file():
            record_counts[slug], targets = write_form_records(slug, path, Path(overlay["root"]) / slug / "manifest.json", out, generated_at)
            record_sources[slug] = "form"
            written |= targets
    if records_root is not None:
        for row in rows:
            if row["slug"] in overlay_slugs:
                continue
            path = records_root / row["slug"] / "records.jsonl"
            source = "machine"
            # 機械の抽出で 1 件も取れなかった自治体には、空の records.jsonl が残っている
            if ai_records_root is not None and (not path.is_file() or path.stat().st_size == 0):
                path = ai_records_root / row["slug"] / "records.jsonl"
                source = "ai"
            if not path.is_file():
                continue
            record_sources[row["slug"]] = source
            by_doc: dict[str, list[dict]] = {}
            total = 0
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    total += 1
                    doc_ids = [d for d in record.get("source_document_ids") or [] if DOC_ID_RE.fullmatch(str(d))]
                    if not doc_ids:
                        continue  # ZIP の中の文書など、索引に詳細画面の無いもの。数には入れる
                    by_doc.setdefault(doc_ids[0], []).append(compact_record(record))
            record_counts[row["slug"]] = total
            # 事業を開いたときに読む API は索引を引かないので、原典の URL も控えに持たせる。
            documents = json.loads((work_root / row["slug"] / "manifest.json").read_text(encoding="utf-8")).get("documents") or {}
            for doc_id, items in by_doc.items():
                items.sort(key=lambda item: (item.get("page") or 0))
                target = out / "records" / row["slug"] / f"{doc_id}.json"
                source_url = str((documents.get(doc_id) or {}).get("source_url") or "")
                write_json_atomic(target, {"slug": row["slug"], "doc_id": doc_id, "source_url": source_url,
                                           "generated_at": generated_at, "schema_version": "1.2.0", "records": items})
                written.add(target)
    # 抽出し直して無くなった文書の控えは消す（records の下だけ）。
    records_dir = out / "records"
    if records_dir.is_dir():
        for stale in records_dir.glob("*/*.json"):
            if stale not in written:
                stale.unlink()

    sheet_counts = copy_sheets(ai_records_root, out) if ai_records_root is not None else {}
    if overlay_slugs and overlay.get("layouts_root"):
        sheet_counts.update(copy_sheets(Path(overlay["layouts_root"]), out, overlay_slugs))

    for row in rows:
        audit = index["by_slug"].get(row["slug"])
        row["indexed_documents"] = audit["indexed_documents"] if audit else None
        row["records"] = record_counts.get(row["slug"], 0)
        row["records_source"] = record_sources.get(row["slug"], "")
        row["sheets"] = sheet_counts.get(row["slug"], 0)
    totals = {"municipalities": len(rows)}
    for key in ("documents", "text_ok", "ocr_done", "ocr_waiting", "ocr_failed", "no_text", "pages", "records", "sheets"):
        totals[key] = sum(int(row[key] or 0) for row in rows)
    totals["indexed_documents"] = sum(int(row["indexed_documents"] or 0) for row in rows)
    totals["searchable_municipalities"] = sum(1 for row in rows if (row["indexed_documents"] or 0) > 0)
    totals["municipalities_with_records"] = sum(1 for row in rows if row["records"] > 0)
    totals["records_ai"] = sum(row["records"] for row in rows if row["records_source"] == "ai")
    totals["municipalities_with_ai_records"] = sum(1 for row in rows if row["records_source"] == "ai" and row["records"] > 0)
    fetch: dict[str, int] = {}
    for row in rows:
        fetch[row["fetch_status"] or "unknown"] = fetch.get(row["fetch_status"] or "unknown", 0) + 1
    totals["fetch_status"] = fetch
    status = {"generated_at": generated_at, "index": index["last_build"], "totals": totals, "municipalities": rows}
    write_json_atomic(out / "status.json", status)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="事務事業評価の画面用の控え（data/hyoka）を作る")
    parser.add_argument("--work-root", type=Path, default=WORKSPACE_ROOT / "work" / "hyoka")
    parser.add_argument("--records-root", type=Path, default=WORKSPACE_ROOT / "work" / "hyoka-extracted")
    parser.add_argument("--out", type=Path, default=WORKSPACE_ROOT / "data" / "hyoka")
    parser.add_argument("--ai-records-root", type=Path, default=WORKSPACE_ROOT / "work" / "hyoka-ai",
                        help="AI が書き写した記録の置き場。機械で取り出した記録の無い自治体に使う（無ければ使わない）")
    parser.add_argument("--no-records", action="store_true", help="事業の記録の控えを作らない（今ある控えは消える）")
    parser.add_argument("--index-log", type=Path, action="append", default=[],
                        help="索引づくりのログ。古い順に複数渡せる（後のもので自治体ごとに上書き）")
    parser.add_argument("--overlay-slug", action="append", default=[],
                        help="調査から集め直した保存先（--overlay-root）で公開する自治体（複数可）")
    parser.add_argument("--overlay-root", type=Path, help="集め直した保存先（work/hyoka2）")
    parser.add_argument("--overlay-records-root", type=Path, help="様式ごとの読み取りの記録の置き場（<slug>/records.jsonl）")
    parser.add_argument("--overlay-layouts-root", type=Path, help="集め直した文書の升目の置き場（<slug>/layouts）")
    args = parser.parse_args(argv)
    if not args.work_root.is_dir():
        parser.error(f"--work-root が無い: {args.work_root}")
    overlay = None
    if args.overlay_slug:
        if not args.overlay_root or not args.overlay_records_root:
            parser.error("--overlay-slug には --overlay-root と --overlay-records-root が要る")
        overlay = {"slugs": args.overlay_slug, "root": args.overlay_root, "records_root": args.overlay_records_root,
                   "layouts_root": args.overlay_layouts_root}
    try:
        ai_root = args.ai_records_root if args.ai_records_root.is_dir() else None
        status = build(args.work_root, None if args.no_records else args.records_root, args.out, args.index_log, ai_root, overlay)
    except ValueError as exc:
        parser.error(str(exc))
    totals = status["totals"]
    print(f"[HYOKA-DISPLAY] 自治体 {totals['municipalities']} 文書 {totals['documents']} 本文 {totals['text_ok']} "
          f"検索 {totals['indexed_documents']} OCR {totals['ocr_done']} 待ち {totals['ocr_waiting']} 打ち切り {totals['ocr_failed']} "
          f"事業の記録 {totals['records']}（{totals['municipalities_with_records']} 自治体、うち AI {totals['records_ai']}・"
          f"{totals['municipalities_with_ai_records']} 自治体） -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
