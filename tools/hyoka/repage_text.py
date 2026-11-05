#!/usr/bin/env python3
"""事務事業評価の保存済みの本文を、頁の区切り（改頁文字）入りに書き直す（取得し直しはしない）。

以前の取得器は PDF の頁を空行でつなぎ、空の頁を落としていたので、検索の索引が多頁の PDF を
頁や事業ごとのシートに分けられなかった（川崎市の政策ごとの PDF は 46 頁に約 20 事業が入って 1 件になる）。
保存してある原典の PDF から頁ごとに取り出し直し、`text/<doc_id>.txt.gz` を書き換える。

- 書き換えるのは、manifest の `text_status` が ok、形式が pdf、2 頁以上、本文に改頁文字が無い文書だけ。
  OCR で作った本文（`text_source: ocr:…`）は触らない。
- 原典の SHA-256 が manifest と同じで、取り出した頁の数が manifest の `pages` と同じで、
  空白を除いた文字が前の本文と同じときだけ書く（取り出し方が変わって文字が違えば `--allow-changed` が要る）。
- manifest は書き換えない（取得器と同時に書かない）。題名などの頭の行は前の本文のものをそのまま使う。

    python3 tools/hyoka/repage_text.py --root work/hyoka2 --slug 14130-kawasaki-shi --dry-run
    python3 tools/hyoka/repage_text.py --root work/hyoka2 --slug 14130-kawasaki-shi --workers 4
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT / "tools"))

from document_crawl import extract_pdf_pages, join_pdf_pages, write_bytes  # noqa: E402

HEADER_LINES = 5  # 題名・年度・種別・出典・掲載ページ（document_crawl.TEXT_HEADER_FIELDS）


def split_header(text: str) -> tuple[str, str]:
    """保存済みの本文を、頭の行（題名など、空行まで）と本文に分ける。頭の行が無ければ ("", 本文)。"""
    lines = text.split("\n", HEADER_LINES + 1)
    if len(lines) == HEADER_LINES + 2 and lines[HEADER_LINES] == "" and all(": " in line for line in lines[:HEADER_LINES]):
        return "\n".join(lines[:HEADER_LINES]) + "\n\n", lines[-1]
    return "", text


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def repage_one(work_dir: str, doc_id: str, entry: dict, write: bool, allow_changed: bool) -> str:
    """1 文書を書き直す。返すのは結果の印（paged、already、changed など）。"""
    # pypdf は読めない文字の符号化を頁ごとに警告する（川崎市で数百行）。結果の印には関係しない。
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    work = Path(work_dir)
    text_path = work / str(entry.get("text_file") or "")
    source_path = work / str(entry.get("file") or "")
    if not text_path.is_file() or text_path.parent.name != "text" or not source_path.is_file():
        return "missing_file"
    raw = source_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        return "sha256_mismatch"
    with gzip.open(text_path, "rt", encoding="utf-8", newline="") as stream:
        saved = stream.read()
    header, body = split_header(saved)
    if "\f" in body:
        return "already"
    pages = extract_pdf_pages(raw)
    if len(pages) != entry.get("pages"):
        return "page_count_mismatch"
    paged = join_pdf_pages(pages)
    if not paged.strip():
        return "empty"
    if squeeze(paged) != squeeze(body):
        if not allow_changed:
            return "changed"
    if write:
        write_bytes(text_path, gzip.compress((header + paged + "\n").encode("utf-8")))
    return "paged"


def targets(root: Path, slugs: list[str]) -> list[tuple[str, str, dict]]:
    found = []
    for slug in slugs or sorted(path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith(("_", "."))):
        manifest_path = root / slug / "manifest.json"
        if not manifest_path.is_file():
            continue
        documents = json.loads(manifest_path.read_text(encoding="utf-8")).get("documents") or {}
        for doc_id, entry in sorted(documents.items()):
            if (isinstance(entry, dict) and entry.get("text_status") == "ok" and entry.get("file_type") == "pdf"
                    and isinstance(entry.get("pages"), int) and entry["pages"] >= 2
                    and not str(entry.get("text_source") or "").startswith("ocr:")):
                found.append((str(root / slug), doc_id, entry))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="事務事業評価の保存済みの本文を頁の区切り入りに書き直す")
    parser.add_argument("--root", type=Path, required=True, help="取得の保存先（<slug>/manifest.json）")
    parser.add_argument("--slug", action="append", default=[], help="この自治体だけ。何度でも")
    parser.add_argument("--dry-run", action="store_true", help="書かずに数えるだけ")
    parser.add_argument("--allow-changed", action="store_true", help="空白を除いた文字が前と違っても書く")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"[ERROR] 保存先が無い: {args.root}", file=sys.stderr)
        return 2
    jobs = targets(args.root, args.slug)
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = pool.map(repage_one, *zip(*[(work, doc_id, entry, not args.dry_run, args.allow_changed)
                                              for work, doc_id, entry in jobs]), chunksize=8) if jobs else []
        for (work, doc_id, _), result in zip(jobs, results):
            counts[result] += 1
            if result not in ("paged", "already"):
                examples.setdefault(result, [])
                if len(examples[result]) < 5:
                    examples[result].append(f"{Path(work).name}:{doc_id}")
    print("[HYOKA-REPAGE] " + json.dumps({"root": str(args.root), "dry_run": args.dry_run, "documents": len(jobs),
                                          **dict(counts), "examples": examples}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
