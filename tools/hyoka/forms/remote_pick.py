#!/usr/bin/env python3
"""本番で、Codex に渡す升目と原本を団体ごとに選び、置き場へ写す（run_forms.py fetch が呼ぶ）。

升目は年度ごとに散らして新しい年度から最大 --layouts 件、そのうち原本も渡すのは最大 --pdfs 件。
年度で様式が変わるので、どの年度も見られるようにする。原本は見た目を確かめるための見本。

    python3 remote_pick.py --work-root /data/hyoka2 --layouts-root /data/hyoka2-ai --stage /tmp/stage --slug 40348-hisayama-cho
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

ROLES = ("sheet", "report", "external")


def spread(docs: list[tuple[str, dict]], limit: int) -> list[str]:
    """年度ごとに 1 件ずつ、新しい年度から順に取る。"""
    by_year: dict[int, list[str]] = {}
    for doc_id, doc in sorted(docs, key=lambda item: (-(item[1].get("pages") or 0), item[0])):
        by_year.setdefault(int(doc.get("fiscal_year") or 0), []).append(doc_id)
    picked: list[str] = []
    while len(picked) < limit and any(by_year.values()):
        for year in sorted(by_year, reverse=True):
            if by_year[year] and len(picked) < limit:
                picked.append(by_year[year].pop(0))
    return picked


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--layouts-root", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--layouts", type=int, default=80)
    parser.add_argument("--pdfs", type=int, default=12)
    args = parser.parse_args()
    for slug in args.slug:
        manifest = json.loads((args.work_root / slug / "manifest.json").read_text(encoding="utf-8"))
        layouts = args.layouts_root / slug / "layouts"
        docs = [(doc_id, doc) for doc_id, doc in manifest["documents"].items()
                if str(doc.get("origin", "")).rsplit(":", 1)[-1] in ROLES and (layouts / f"{doc_id}.json.gz").is_file()]
        sheets = [item for item in docs if str(item[1].get("origin", "")).endswith(":sheet")] or docs
        chosen = spread(sheets, args.layouts)
        originals = spread([item for item in sheets if item[0] in set(chosen)], args.pdfs)
        out = args.stage / slug
        (out / "layouts").mkdir(parents=True, exist_ok=True)
        (out / "pdf").mkdir(exist_ok=True)
        for doc_id in chosen:
            shutil.copyfile(layouts / f"{doc_id}.json.gz", out / "layouts" / f"{doc_id}.json.gz")
        for doc_id in originals:
            source = args.work_root / slug / manifest["documents"][doc_id]["file"]
            shutil.copyfile(source, out / "pdf" / f"{doc_id}{source.suffix}")
        print(json.dumps({"slug": slug, "documents": len(docs), "layouts": len(chosen), "pdfs": len(originals)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    os.umask(0o022)
    main()
