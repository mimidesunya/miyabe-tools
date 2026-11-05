#!/usr/bin/env python3
"""様式の読み取りプログラムを流して、番地、検算、取れ具合を確かめる。

    python tools/hyoka/forms/check.py --layouts work/hyoka2-ai/14130-kawasaki-shi/layouts --reader reader.py
    python tools/hyoka/forms/check.py --layouts ... --reader reader.py --out records.jsonl --show 10

読み取りプログラムは `read(document) -> list[dict]` を持つ（document は `cells.Document`）。1 件が 1 事業の記録で、
形は形 2.0 の書き写し（`tools/hyoka/format2/extract.py` の SCHEMA）と同じ。値は `cells.Cell.value()` の
原文と番地の組で入れ、事業の識別（project）の各項目は値の組か、その配列にする。

確かめること:
- 番地が文書にあり、原文がその升目の文字の中にあるか（書き写しの誤りを落とす）
- 検算: 事業費 + 人件費 = 総費用、財源の合計 = 事業費、達成率 = 実績 ÷ 目標
- 取れ具合: 事業名、金額、財源、指標、評価の方向性のある記録の数
- `--truth` を渡すと、人が読んだ正解（`tools/hyoka/format2/truth/`）と突き合わせる
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "format2")]

import cells  # noqa: E402
import score  # noqa: E402

VALUE_KEYS = ("amounts", "funding", "staff", "reflection")


def load_reader(path: Path):
    spec = importlib.util.spec_from_file_location(f"reader_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def values_with_refs(record: dict):
    """記録の中の、番地の付いた値を全部たどる。"""
    stack = [record]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "ref" in item and "raw" in item:
                yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def ref_problems(record: dict, refs: dict[str, str]) -> list[str]:
    problems = []
    for value in values_with_refs(record):
        ref = value.get("ref") or ""
        if not ref:
            continue
        source = refs.get(ref)
        if source is None:
            problems.append(f"番地が無い {ref} {value.get('raw')}")
        elif cells.norm(value.get("raw")) not in cells.norm(source):
            problems.append(f"原文が升目に無い {ref} {value.get('raw')!r} / {source[:30]!r}")
    return problems


def project_text(record: dict) -> dict:
    """事業の識別を文字にする（採点の道具は文字で比べる）。"""
    out = {}
    for key, value in (record.get("project") or {}).items():
        items = value if isinstance(value, list) else [value]
        out[key] = " ".join(str(item.get("raw") if isinstance(item, dict) else item or "") for item in items).strip()
    return out


def funding_problems(record: dict) -> list[str]:
    """財源の合計 = 事業費（同じ年度、同じ段階）。"""
    groups: dict = defaultdict(lambda: {"funding": Decimal(0), "count": 0, "direct": None})
    for row in record.get("funding") or []:
        amount = cells.number(row.get("raw"))
        if amount is not None:
            group = groups[(row.get("fiscal_year"), row.get("stage"))]
            group["funding"] += amount
            group["count"] += 1
    for row in record.get("amounts") or []:
        amount = cells.number(row.get("raw"))
        key = (row.get("fiscal_year"), row.get("stage"))
        if row.get("scope") == "direct" and amount is not None and key in groups:
            groups[key]["direct"] = max(amount, groups[key]["direct"] or amount)
    return [f"{year} {stage}: 財源の合計 {group['funding']} が事業費 {group['direct']} と合わない"
            for (year, stage), group in groups.items()
            if group["count"] >= 2 and group["direct"] is not None and group["funding"] != group["direct"]]


def checks(record: dict) -> tuple[int, list[str]]:
    """検算できた組の数と、合わなかったもの。"""
    problems = score.arithmetic(record) + funding_problems(record)
    pairs = 0
    by_year: dict = defaultdict(set)
    for row in record.get("amounts") or []:
        by_year[(row.get("fiscal_year"), row.get("stage"))].add(row.get("scope"))
    pairs += sum(1 for scopes in by_year.values() if {"direct", "personnel", "total"} <= scopes)
    funded = {(row.get("fiscal_year"), row.get("stage")) for row in record.get("funding") or []}
    pairs += sum(1 for key in funded if "direct" in by_year.get(key, set()))
    return pairs, problems


def load_truths(folder: Path | None) -> dict[str, dict]:
    if not folder:
        return {}
    truths = {}
    for path in folder.glob("*.json"):
        truth = json.loads(path.read_text(encoding="utf-8"))
        truths[truth["source"]["doc_id"]] = truth
    return truths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layouts", type=Path, required=True, help="升目の置き場（<slug>/layouts）")
    parser.add_argument("--reader", type=Path, required=True, help="読み取りプログラム（read(document) を持つ .py）")
    parser.add_argument("--out", type=Path, help="記録を書き出す先（jsonl）")
    parser.add_argument("--truth", type=Path, help="正解の置き場（tools/hyoka/format2/truth）")
    parser.add_argument("--show", type=int, default=5, help="問題の例をいくつ見せるか")
    parser.add_argument("--doc", action="append", default=[], help="この文書だけ（doc_id、複数可）")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    reader = load_reader(args.reader)
    truths = load_truths(args.truth)
    paths = sorted(args.layouts.glob("*.json.gz"))
    if args.doc:
        paths = [path for path in paths if path.name.split(".")[0] in set(args.doc)]
    counts: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    out = open(args.out, "w", encoding="utf-8", newline="\n") if args.out else None

    def note(kind: str, text: str) -> None:
        counts[kind] += 1
        if len(examples[kind]) < args.show:
            examples[kind].append(text)

    for path in paths:
        document = cells.load_document(path)
        refs = document.refs()
        counts["文書"] += 1
        try:
            records = reader.read(document) or []
        except Exception:
            note("読み取りの例外", f"{document.doc_id}: {traceback.format_exc(limit=2).splitlines()[-1]}")
            continue
        if not records:
            note("記録の無い文書", f"{document.doc_id} 頁 {len(document.pages)}")
        for record in records:
            counts["記録"] += 1
            project = project_text(record)
            where = f"{document.doc_id} p{(record.get('pages') or ['?'])[0]} {project.get('name', '')[:20]}"
            counts["事業名"] += bool(project.get("name"))
            counts["金額"] += bool(record.get("amounts"))
            counts["金額の単位"] += bool(record.get("amounts")) and all(row.get("unit") for row in record["amounts"])
            counts["財源"] += bool(record.get("funding"))
            counts["指標"] += bool(record.get("indicators"))
            counts["評価の方向性"] += any(row.get("direction") not in (None, "", "none") for row in record.get("evaluations") or [])
            # 形 2.0 の項目がどれだけ取れたか（様式に無ければ 0 でよい）
            evaluations = [row for row in record.get("evaluations") or [] if isinstance(row, dict)]
            counts["体系の道筋"] += bool(record.get("policy_path"))
            counts["文章の項目"] += bool(record.get("texts"))
            counts["そのほかの欄"] += bool(record.get("extra_fields"))
            counts["そろえた評価"] += any(row.get("achievement") or row.get("overall") for row in evaluations)
            counts["評価の理由"] += any(row.get("reasons") for row in evaluations)
            for problem in ref_problems(record, refs):
                note("番地の問題", f"{where}: {problem}")
            pairs, problems = checks(record)
            counts["検算の組"] += pairs
            for problem in problems:
                note("検算が合わない", f"{where}: {problem}")
            if not record.get("amounts"):
                note("金額の無い記録", where)
            truth = truths.get(document.doc_id)
            if truth and cells.norm(truth["project"]["name"])[:6] in cells.norm(project.get("name")):
                result = score.score_sheet(truth, {**record, "project": project})
                summary = " ".join(f"{key} {result[key][0]}/{result[key][1]}" for key in ("amounts", "funding", "indicators", "evaluations", "project"))
                note("正解との突き合わせ", f"{where}: {summary} 無い {result['miss'][:3]} 誤り {result['wrong'][:3]}")
            if out:
                out.write(json.dumps({"doc_id": document.doc_id, **record}, ensure_ascii=False) + "\n")
    if out:
        out.close()

    print(" ".join(f"{key} {counts[key]}" for key in ("文書", "記録", "事業名", "金額", "金額の単位", "財源", "指標", "評価の方向性", "検算の組")))
    print(" ".join(f"{key} {counts[key]}" for key in ("番地の問題", "検算が合わない", "金額の無い記録", "記録の無い文書", "読み取りの例外")))
    print("形 2.0: " + " ".join(f"{key} {counts[key]}" for key in ("体系の道筋", "文章の項目", "そのほかの欄", "そろえた評価", "評価の理由")))
    for kind, items in examples.items():
        print(f"■ {kind}")
        for item in items:
            print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
