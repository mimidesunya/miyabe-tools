#!/usr/bin/env python3
"""登録簿に URL がある自治体を、その URL だけ開き直して判定し直す。

`discover_hyoka_urls.py` はホームページから歩き直すので 1 自治体に数十秒かかる。
既に URL が分かっている自治体（`review_required` の 509 件）を仕分けるだけなら、
その 1 ページを開けば足りる。判定は探索ツールと同じ関数を使うので、結論は
探索し直した場合と揃う。

    python tools/hyoka/recheck_hyoka_urls.py --status review_required --save-out work/hyoka/recheck.csv
    python tools/hyoka/recheck_hyoka_urls.py --codes 22206 23445 --verbose
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(Path(__file__).resolve().parent))

import discover_hyoka_urls as discover  # noqa: E402

REGISTRY = WORKSPACE_ROOT / "data" / "municipalities" / "hyoka_system_urls.tsv"


def load_registry() -> list[dict[str, str]]:
    with io.open(REGISTRY, encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle, delimiter="\t")]


def recheck_one(session: requests.Session, row: dict[str, str], timeout: float) -> dict[str, str]:
    code = str(row.get("jis_code", "")).strip()
    url = str(row.get("url", "")).strip()
    result = {"jis_code": code, "url": url, "confidence": "none", "evidence": "",
              "title": "", "attachments": "0", "year_docs": "0", "note": ""}
    if not url:
        result["note"] = "URL が無い"
        return result
    fetched = discover.fetch(session, url, timeout)
    if fetched is None:
        result["note"] = "開けない"
        return result
    final_url, html = fetched
    confidence, evidence = discover.score_page(final_url, html, "")
    result["confidence"] = confidence
    result["evidence"] = evidence
    result["title"] = discover.page_title(html)
    result["attachments"] = str(discover.count_attachments(final_url, html))
    result["year_docs"] = str(len(discover.year_evaluation_links(final_url, html)))
    result["url"] = final_url
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="登録簿の URL を開き直して判定し直す。")
    parser.add_argument("--status", default="", help="この crawl_status の行だけを見る")
    parser.add_argument("--codes", nargs="*", default=None, help="対象 jis_code")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=8, help="同時に見る自治体数（別ホストなので分散する）")
    parser.add_argument("--save-out", default="", help="結果 CSV の出力先")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = load_registry()
    if args.codes:
        wanted = set(args.codes)
        rows = [row for row in rows if str(row.get("jis_code", "")).strip() in wanted]
    elif args.status:
        rows = [row for row in rows if str(row.get("crawl_status", "")).strip() == args.status]
    rows = [row for row in rows if str(row.get("url", "")).strip()]

    out_path = Path(args.save_out) if args.save_out else (
        WORKSPACE_ROOT / "work" / "hyoka" / f"recheck_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"対象 {len(rows)} 自治体 / 出力 {out_path}", flush=True)

    def work(row: dict[str, str]) -> dict[str, str]:
        session = requests.Session()
        try:
            return recheck_one(session, row, args.timeout)
        except Exception as error:
            return {"jis_code": str(row.get("jis_code", "")).strip(), "url": str(row.get("url", "")),
                    "confidence": "none", "evidence": "", "title": "", "attachments": "0",
                    "year_docs": "0", "note": f"error: {error}"}
        finally:
            session.close()

    counts = {"high": 0, "medium": 0, "low": 0, "education": 0, "none": 0}
    marks = {"high": "◎", "medium": "○", "low": "△", "education": "教", "none": "×"}
    with io.open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["jis_code", "confidence", "url", "title",
                                                    "attachments", "year_docs", "evidence", "note"])
        writer.writeheader()
        handle.flush()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(work, row): row for row in rows}
            for index, future in enumerate(as_completed(futures), 1):
                found = future.result()
                counts[found["confidence"]] += 1
                writer.writerow(found)
                handle.flush()
                if args.verbose or index % 25 == 0:
                    print(f"[{index}/{len(rows)}] {marks[found['confidence']]} {found['jis_code']} "
                          f"{found['title'][:30]} {found['evidence'][:30]}", flush=True)

    print(f"\n完了: {len(rows)}件  確実={counts['high']} 有力={counts['medium']} "
          f"弱={counts['low']} 教委={counts['education']} 不明={counts['none']}", flush=True)
    print(f"結果: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
