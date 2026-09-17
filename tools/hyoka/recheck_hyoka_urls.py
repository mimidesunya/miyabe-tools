#!/usr/bin/env python3
"""登録簿に URL がある自治体を、その URL だけ開き直して判定し直す。

`discover_hyoka_urls.py` はホームページから歩き直すので 1 自治体に数十秒かかる。
既に URL が分かっている自治体（`review_required` の 509 件）を仕分けるだけなら、
その 1 ページを開けば足りる。判定は探索ツールと同じ関数を使うので、結論は
探索し直した場合と揃う。

    python tools/hyoka/recheck_hyoka_urls.py --status review_required --save-out work/hyoka/recheck.csv
    python tools/hyoka/recheck_hyoka_urls.py --codes 22206 23445 --verbose
    python tools/hyoka/recheck_hyoka_urls.py --reason needs_confirmation --descend

`--descend` を付けると、入口ページで確実にならなかった自治体について、
評価の文書らしい子ページを数本だけ開いて判定する（評価表が 1 階層下にある形）。
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


# 子ページを開く間隔。同じ自治体のサイトを続けて開くので、1 秒以上空ける。
DEFAULT_CHILD_DELAY = 1.2
DEFAULT_MAX_CHILDREN = 4


def settled(confidence: str, attachments: int) -> bool:
    """取得対象にしてよい判定か。前回と同じ基準（確実、または有力で実ファイルあり）。"""
    return confidence == "high" or (confidence == "medium" and attachments > 0)


def descend(
    session: requests.Session,
    base_url: str,
    html: str,
    timeout: float,
    *,
    max_children: int = DEFAULT_MAX_CHILDREN,
    delay: float = DEFAULT_CHILD_DELAY,
    sleep=time.sleep,
) -> dict[str, str] | None:
    """入口ページから子ページへ 1 階層だけ降り、評価表が並ぶ頁を探す。

    見つかれば、その子ページの判定を返す。除外（電源立地・総合戦略・
    指定管理・計画の進行管理・教育委員会など）は子ページ側でも同じ
    `score_page` が効くので、別制度の頁は採らない。
    """
    for index, (target, label) in enumerate(
        discover.child_evaluation_links(base_url, html, limit=max_children)
    ):
        if index and delay:
            sleep(delay)
        fetched = discover.fetch(session, target, timeout)
        if fetched is None:
            continue
        final_url, child_html = fetched
        if discover.child_page_is_off_topic(discover.page_title(child_html)):
            continue
        confidence, evidence = discover.score_page(final_url, child_html, label)
        if confidence in {"none", "education", "low"}:
            continue
        attachments = sum(
            1 for name in discover.evaluation_attachment_labels(child_html)
            if not discover.looks_negative(name)
        )
        table = discover.has_evaluation_table(child_html)
        if confidence == "medium" and attachments == 0 and not table:
            continue
        return {
            "url": final_url,
            "confidence": "high" if confidence == "high" else "medium",
            "evidence": f"子ページ『{label[:20]}』: {evidence}"
                        + (f"／評価表{attachments}件" if attachments else "")
                        + ("／HTML表" if table else ""),
            "title": discover.page_title(child_html),
            # HTML の表で載せている頁は、実ファイルがあるのと同じに扱う。
            "attachments": str(max(attachments, 1 if table else 0)),
        }
    return None


def recheck_one(
    session: requests.Session,
    row: dict[str, str],
    timeout: float,
    *,
    descend_children: bool = False,
    max_children: int = DEFAULT_MAX_CHILDREN,
    child_delay: float = DEFAULT_CHILD_DELAY,
) -> dict[str, str]:
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
    if descend_children and confidence in {"medium", "low"} and not settled(
        confidence, int(result["attachments"])
    ):
        # 入口では決まらなかった。評価表が 1 階層下にある形を見る。
        if child_delay:
            time.sleep(child_delay)
        child = descend(session, final_url, html, timeout,
                        max_children=max_children, delay=child_delay)
        if child is not None:
            result.update(child)
            result["note"] = f"入口 {final_url}"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="登録簿の URL を開き直して判定し直す。")
    parser.add_argument("--status", default="", help="この crawl_status の行だけを見る")
    parser.add_argument("--reason", default="", help="この exclusion_reason の行だけを見る")
    parser.add_argument("--descend", action="store_true",
                        help="入口で決まらなければ、評価の子ページへ 1 階層だけ降りる")
    parser.add_argument("--max-children", type=int, default=DEFAULT_MAX_CHILDREN,
                        help="1 自治体で開く子ページの上限")
    parser.add_argument("--child-delay", type=float, default=DEFAULT_CHILD_DELAY,
                        help="子ページを開く間隔秒（1 秒以上）")
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
    else:
        if args.status:
            rows = [row for row in rows if str(row.get("crawl_status", "")).strip() == args.status]
        if args.reason:
            rows = [row for row in rows if str(row.get("exclusion_reason", "")).strip() == args.reason]
    rows = [row for row in rows if str(row.get("url", "")).strip()]

    out_path = Path(args.save_out) if args.save_out else (
        WORKSPACE_ROOT / "work" / "hyoka" / f"recheck_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"対象 {len(rows)} 自治体 / 出力 {out_path}", flush=True)

    def work(row: dict[str, str]) -> dict[str, str]:
        session = requests.Session()
        try:
            return recheck_one(
                session, row, args.timeout,
                descend_children=args.descend,
                max_children=args.max_children,
                child_delay=max(1.0, args.child_delay),
            )
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
