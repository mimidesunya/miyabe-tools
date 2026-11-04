#!/usr/bin/env python3
"""形 2.0 の抽出を、正解のあるシートで試す（費用がかかる）。

python tools/hyoka/format2/trial.py --work-root work/hyoka --out work/hyoka-format2-trial --model gpt-6-luna --effort medium

答えは --out に残り、残っていれば呼び直さない。summary.txt に値ごとの違いを並べる。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "tools" / "hyoka" / "ai"), str(ROOT / "tools" / "hyoka" / "format2")]

import fitz  # noqa: E402
import extract  # noqa: E402
import grid  # noqa: E402
import openai_api  # noqa: E402
import score  # noqa: E402

PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6.1-sol": (2.00, 10.00)}


def load_sheets(work_root: Path) -> list[dict]:
    sheets = []
    for path in sorted((ROOT / "tools" / "hyoka" / "format2" / "truth").glob("*.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        source = truth["source"]
        pdf_path = work_root / source["slug"] / "source" / f"{source['doc_id']}.pdf"
        if not pdf_path.is_file():
            print(f"原本が無い: {pdf_path}", flush=True)
            continue
        pages = []
        with fitz.open(pdf_path) as pdf:
            for number in source["pdf_pages"]:
                if not pdf[number - 1].get_text("text").strip():
                    pages.append({"page": number, "image_only": True})
                    continue
                pages.append({"page": number, **grid.page_layout(pdf[number - 1])})
        text, cells = extract.address_pages(pages)
        sheets.append({"slug": source["slug"], "truth": truth, "text": text, "cells": cells})
    return sheets


def ask(sheet: dict, key: str, model: str, effort: str, out: Path) -> None:
    path = out / f"{sheet['slug']}.json"
    if path.is_file():
        return
    started = time.time()
    body = openai_api.chat_body(model, extract.SYSTEM, sheet["text"], extract.SCHEMA, "hyoka_format2", reasoning_effort=effort)
    raw, usage, finish = openai_api.chat(key, body)
    path.write_text(json.dumps({"raw": raw, "usage": usage, "finish": finish, "seconds": round(time.time() - started)},
                               ensure_ascii=False), encoding="utf-8")


def report(sheets: list[dict], model: str, out: Path) -> None:
    totals: dict = {}
    tokens = [0, 0]
    lines = []
    for sheet in sheets:
        path = out / f"{sheet['slug']}.json"
        if not path.is_file():
            lines.append(f"== {sheet['slug']} 答えが無い")
            continue
        answer = json.loads(path.read_text(encoding="utf-8"))
        tokens[0] += int(answer["usage"].get("prompt_tokens") or 0)
        tokens[1] += int(answer["usage"].get("completion_tokens") or 0)
        truth = sheet["truth"]
        name = extract.norm(truth["project"]["name"])
        resolved = [extract.resolve(r, sheet["cells"]) for r in json.loads(answer["raw"]).get("records") or []]
        best = next((r for r in resolved if name[:6] in extract.norm(r["record"]["project"].get("name"))), None)
        best = best or (resolved[0] if resolved else {"record": {}, "problems": []})
        result = score.score_sheet(truth, best["record"])
        checks = score.arithmetic(best["record"])
        for key in ("amounts", "funding", "indicators", "evaluations", "project"):
            total = totals.setdefault(key, [0, 0])
            total[0] += result[key][0]
            total[1] += result[key][1]
        lines.append(f"== {sheet['slug']} 記録 {len(resolved)} 金額 {result['amounts']} 財源 {result['funding']} 指標 {result['indicators']} "
                     f"評価 {result['evaluations']} 事業 {result['project']} 番地の問題 {len(best['problems'])} 検算 {len(checks)}")
        lines += [f"  問題 {x}" for x in best["problems"]] + [f"  無い {x}" for x in result["miss"]]
        lines += [f"  誤り {x}" for x in result["wrong"]] + [f"  検算 {x}" for x in checks]
    price = PRICES[model]
    lines.append(f"合計 {totals} トークン {tokens} 費用 ${(tokens[0] * price[0] + tokens[1] * price[1]) / 1e6:.3f}")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[-1], flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="形 2.0 の抽出を正解のあるシートで試す")
    parser.add_argument("--work-root", type=Path, default=ROOT / "work" / "hyoka")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "data" / "config.json")
    parser.add_argument("--model", default="gpt-6-luna", choices=sorted(PRICES))
    parser.add_argument("--effort", default="medium")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sheets = load_sheets(args.work_root)
    key = openai_api.load_key(args.config)
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda sheet: ask(sheet, key, args.model, args.effort, args.out), sheets))
    report(sheets, args.model, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
