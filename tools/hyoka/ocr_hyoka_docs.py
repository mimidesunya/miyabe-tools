#!/usr/bin/env python3
"""文字情報のない事務事業評価の PDF を OCR して本文にする、後追いの掃き取り。

取得器（`scrape_hyoka_docs.py`）は、紙をスキャンしただけの PDF を
`text_status: needs_ocr` として本文を作らずに置く。1 回目の全件取得
（2026-10-27〜28）では 1,578 本・約 3 万頁がこの形だった。ここではそれを
NDLOCR-Lite（会議録の OCR と同じ `tools/gijiroku/pdf_ocr.py`、GPU 不要・
実測 0.7 秒/頁）で読み、本文 `text/<doc_id>.txt.gz` を作って manifest を
`text_status: ok`（`text_source: ocr:ndlocr-lite`）にする。全文検索には、
そのあと自治体ごとの索引の更新（`build_opensearch_index.py --doc-type hyoka
--mode update --slug …`）で載る。

- 原典のファイルは変えない。本文と manifest の印だけを書く。
- 取れなかった PDF は、試した回数と原典の指紋を自治体ごとに残し
  （`ocr_attempts.json`）、同じ中身は上限回数で打ち切る。取得元が差し替えたら
  また試す。
- **取得器と同時に走らせない。** どちらも manifest を書くので、同じ自治体で
  重なると片方の書き込みが消える。全量の索引づくりとも重ねない。

    python tools/hyoka/ocr_hyoka_docs.py --all --dry-run
    python tools/hyoka/ocr_hyoka_docs.py --code 23229
    python tools/hyoka/ocr_hyoka_docs.py --all --limit-docs 200
"""

from __future__ import annotations

import argparse
import gzip
import sys
from dataclasses import dataclass, field
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_ROOT = WORKSPACE_ROOT / "work" / "hyoka"
sys.path.append(str(WORKSPACE_ROOT / "tools"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "gijiroku"))

import pdf_ocr  # noqa: E402
from document_crawl import load_manifest, now_text, text_document, write_json  # noqa: E402

OCR_SOURCE = "ocr:ndlocr-lite"


@dataclass
class OcrResult:
    slug: str
    candidates: int = 0
    done: int = 0
    failed: int = 0
    skipped_attempts: int = 0
    pages: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def needs_ocr(entry: dict) -> bool:
    return (
        str(entry.get("file_type") or "") == "pdf"
        and str(entry.get("text_status") or "") == "needs_ocr"
        and bool(entry.get("file"))
        and bool(entry.get("sha256"))
    )


def ocr_one_municipality(work_dir: Path, *, limit_docs: int = 0, dry_run: bool = False,
                         ocr=pdf_ocr.ocr_pdf_text) -> OcrResult:
    """1 自治体の OCR 待ちを読む。manifest は 1 本読むたびに書き戻す（止まっても続きから）。"""
    result = OcrResult(slug=work_dir.name)
    manifest = load_manifest(work_dir)
    documents: dict[str, dict] = manifest.get("documents") or {}
    for doc_id, entry in sorted(documents.items()):
        if not needs_ocr(entry):
            continue
        result.candidates += 1
        if limit_docs and result.done + result.failed >= limit_docs:
            continue
        source = work_dir / str(entry["file"])
        digest = pdf_ocr.file_digest(source)
        if not pdf_ocr.should_try(work_dir, doc_id, digest):
            result.skipped_attempts += 1
            continue
        if dry_run:
            result.pages += int(entry.get("pages") or 0)
            continue
        body, reason = ocr(source)
        if not body:
            result.failed += 1
            key = reason.split("（")[0].split(":")[0] or "不明"
            result.reasons[key] = result.reasons.get(key, 0) + 1
            pdf_ocr.record_attempt(work_dir, doc_id, digest, status="failed", reason=reason)
            continue
        relative_text = f"text/{doc_id}.txt.gz"
        (work_dir / "text").mkdir(parents=True, exist_ok=True)
        (work_dir / relative_text).write_bytes(gzip.compress(text_document(entry, body).encode("utf-8")))
        entry.update({
            "text_status": "ok",
            "text_file": relative_text,
            "text_chars": len(body),
            "text_source": OCR_SOURCE,
            "ocr_at": now_text(),
        })
        pdf_ocr.record_attempt(work_dir, doc_id, digest, status="ok")
        write_json(work_dir / "manifest.json", manifest)
        result.done += 1
        result.pages += int(entry.get("pages") or 0)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="事務事業評価の OCR 待ちの PDF を NDLOCR-Lite で本文にする")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--code", action="append", default=[], help="団体コード（複数可）")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--limit-docs", type=int, default=0, help="1 自治体で読む本数の上限（0 は無制限）")
    parser.add_argument("--dry-run", action="store_true", help="数えるだけで OCR しない")
    args = parser.parse_args(argv)

    if not args.dry_run and pdf_ocr.tool_directory() is None:
        print("[ERROR] NDLOCR-Lite が見つかりません（MIYABE_NDLOCR_DIR か /opt/ndlocr-lite）", file=sys.stderr)
        return 2
    dirs = sorted(p.parent for p in args.work_root.glob("*/manifest.json"))
    if args.code:
        wanted = set(args.code)
        dirs = [d for d in dirs if d.name[:5] in wanted]
    total = OcrResult(slug="(all)")
    for work_dir in dirs:
        r = ocr_one_municipality(work_dir, limit_docs=args.limit_docs, dry_run=args.dry_run)
        if not r.candidates:
            continue
        for key in ("candidates", "done", "failed", "skipped_attempts", "pages"):
            setattr(total, key, getattr(total, key) + getattr(r, key))
        for key, n in r.reasons.items():
            total.reasons[key] = total.reasons.get(key, 0) + n
        print(f"[OCR] {r.slug} 候補{r.candidates} 本文にした{r.done} 失敗{r.failed} 打ち切り{r.skipped_attempts} 頁{r.pages} {r.reasons or ''}", flush=True)
    print(f"[OCR] 合計 候補{total.candidates} 本文にした{total.done} 失敗{total.failed} 打ち切り{total.skipped_attempts} 頁{total.pages} {total.reasons or ''}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
