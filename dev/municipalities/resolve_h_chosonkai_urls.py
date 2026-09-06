#!/usr/bin/env python3
"""h-chosonkai（北海道町村会 例規集データベース）の自治体別 URL を引き直す。

houmu.h-chosonkai.gr.jp/~reikidb は 130 余りの町村を 1 つの DB に同居させ、
`/~reikidb/data/{choson_no}/{版}/reiki.html` のように**版番号を URL に持つ**。
自治体が新版を出すと版が繰り上がり、旧ディレクトリごと 404 になる。
放置すると目録も本文も取れないまま、前回のマニフェストだけが残る。

このスクリプトは入口ページの choson_no→自治体名（漢字）対応を読み、
master の自治体名で jis_code に突き合わせ、現行の URL を
`reiki_system_urls.tsv` へ書き戻す。system_type は変えない。
同じホストに載っている `taikei` 行（`reiki_menu.html`）も対象にする。

解決処理そのものは `tools/reiki/source_url_recovery.py` と共有している。
巡回中の自動復旧はそちらが行い、このスクリプトは登録簿を正す側になる。

    python dev/municipalities/resolve_h_chosonkai_urls.py --dry-run
    python dev/municipalities/resolve_h_chosonkai_urls.py --only-dead
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "reiki"))

import source_url_recovery  # noqa: E402

TSV = ROOT / "data" / "municipalities" / "reiki_system_urls.tsv"
MASTER = ROOT / "data" / "municipalities" / "municipality_master.tsv"


def load_rows(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="北海道町村会の例規集 URL を現行の版へ引き直す。"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="TSV を書き換えず、引き直した結果だけを表示する",
    )
    parser.add_argument(
        "--only-dead",
        action="store_true",
        help="いま 404 になっている行だけを引き直す",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.3,
        help="自治体ページを開く間隔（秒）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    session = source_url_recovery.build_session()

    try:
        entry_html = source_url_recovery.fetch_h_chosonkai_entry(session)
    except Exception as error:
        print(f"[ERROR] 入口ページを読めませんでした: {error}")
        return 1

    choson_numbers = source_url_recovery.h_chosonkai_choson_numbers(entry_html)
    print(f"入口ページの自治体リンク: {len(choson_numbers)} 件")

    master = {row["jis_code"]: row for row in load_rows(MASTER)}
    rows = load_rows(TSV)

    resolved = 0
    unchanged = 0
    skipped_alive = 0
    unmatched: list[str] = []

    for row in rows:
        url = str(row.get("url", "")).strip()
        if not source_url_recovery.is_recoverable_source_url(url):
            continue
        code = str(row.get("jis_code", "")).strip()
        name = str(master.get(code, {}).get("name", "")).strip()

        if args.only_dead and not source_url_recovery.source_url_is_dead(url, session=session):
            skipped_alive += 1
            continue

        choson_no = choson_numbers.get(name)
        if choson_no is None:
            unmatched.append(f"{code} {name} (入口ページに名前が無い)")
            continue

        try:
            current_url = source_url_recovery.h_chosonkai_url_for_choson_no(session, choson_no)
        except Exception as error:
            unmatched.append(f"{code} {name} (自治体ページを開けない: {error})")
            continue
        time.sleep(max(0.0, float(args.sleep)))

        if current_url == "":
            unmatched.append(f"{code} {name} (例規集リンクが見つからない)")
            continue
        if current_url == url:
            unchanged += 1
            continue

        row["url"] = current_url
        resolved += 1
        print(f"  {code} {name} [{row.get('system_type')}] {url}\n      -> {current_url}")

    if not args.dry_run and resolved:
        with open(TSV, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    print(
        f"引き直し={resolved} 変更なし={unchanged} "
        f"生きているので飛ばした={skipped_alive} 解決できず={len(unmatched)}"
        + ("（--dry-run のため書き込んでいません）" if args.dry_run else "")
    )
    for item in unmatched:
        print(f"  [解決できず] {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
