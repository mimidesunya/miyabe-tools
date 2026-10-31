#!/usr/bin/env python3
"""事務事業評価を画面に出すための控えを作る（入力は読むだけ、書くのは出力先だけ）。

画面（PHP）からは取得の保存先 `work/hyoka`・抽出の出力先 `work/hyoka-extracted` が
見えないので、要る分だけを縮めて `data/hyoka` に置く。

- `status.json`: 自治体ごとの取り込みの状態（取得・本文・OCR・検索・事業の記録の数）。
  収集・公開状況（/status/）と自治体一覧（/municipalities/）が読む。
- `records/<slug>/<doc_id>.json`: 抽出した事業の記録（共通形式 1.2.0）を、
  詳細画面に出す欄だけに縮めたもの。欄名は原典のまま、値は専用欄の原文だけを写す。
  欄の中の折り返し（表のセルの幅で切れた改行）だけは詰める。AI・推定は使わない。

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
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT / "tools" / "gijiroku"))

import pdf_ocr  # noqa: E402

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
    return {
        "id": str(record.get("record_id") or ""),
        "layout": str(record.get("layout") or ""),
        "status": str(record.get("record_status") or ""),
        **record_location(record),
        "fields": shown,
        "raw_fields": raw,
    }


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


def build(work_root: Path, records_root: Path | None, out: Path, index_logs: list[Path]) -> dict:
    index = read_index_logs(index_logs)
    rows = []
    for work_dir in sorted(p for p in work_root.iterdir() if p.is_dir() and SLUG_RE.fullmatch(p.name)):
        if (work_dir / "manifest.json").is_file():
            rows.append(municipality_status(work_dir))
    # 保存先を付け忘れた容器（空のディレクトリ）で走らせて、画面の控えを空で上書きしない。
    if not rows:
        raise ValueError(f"manifest が 1 つも無い: {work_root}")
    if records_root is not None and not any(records_root.glob("*/records.jsonl")):
        raise ValueError(f"抽出の出力（*/records.jsonl）が無い: {records_root}。記録なしで作るなら --no-records")

    record_counts: dict[str, int] = {}
    written: set[Path] = set()
    generated_at = now_text()
    if records_root is not None:
        for row in rows:
            path = records_root / row["slug"] / "records.jsonl"
            if not path.is_file():
                continue
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

    for row in rows:
        audit = index["by_slug"].get(row["slug"])
        row["indexed_documents"] = audit["indexed_documents"] if audit else None
        row["records"] = record_counts.get(row["slug"], 0)
    totals = {"municipalities": len(rows)}
    for key in ("documents", "text_ok", "ocr_done", "ocr_waiting", "ocr_failed", "no_text", "pages", "records"):
        totals[key] = sum(int(row[key] or 0) for row in rows)
    totals["indexed_documents"] = sum(int(row["indexed_documents"] or 0) for row in rows)
    totals["searchable_municipalities"] = sum(1 for row in rows if (row["indexed_documents"] or 0) > 0)
    totals["municipalities_with_records"] = sum(1 for row in rows if row["records"] > 0)
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
    parser.add_argument("--no-records", action="store_true", help="事業の記録の控えを作らない（今ある控えは消える）")
    parser.add_argument("--index-log", type=Path, action="append", default=[],
                        help="索引づくりのログ。古い順に複数渡せる（後のもので自治体ごとに上書き）")
    args = parser.parse_args(argv)
    if not args.work_root.is_dir():
        parser.error(f"--work-root が無い: {args.work_root}")
    try:
        status = build(args.work_root, None if args.no_records else args.records_root, args.out, args.index_log)
    except ValueError as exc:
        parser.error(str(exc))
    totals = status["totals"]
    print(f"[HYOKA-DISPLAY] 自治体 {totals['municipalities']} 文書 {totals['documents']} 本文 {totals['text_ok']} "
          f"検索 {totals['indexed_documents']} OCR {totals['ocr_done']} 待ち {totals['ocr_waiting']} 打ち切り {totals['ocr_failed']} "
          f"事業の記録 {totals['records']}（{totals['municipalities_with_records']} 自治体） -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
