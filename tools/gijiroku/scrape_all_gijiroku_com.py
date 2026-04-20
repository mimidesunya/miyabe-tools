#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility wrapper for the unified minutes batch scraper."""

import argparse
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="gijiroku.com / voices 対象の一括スクレイピングを unified batch に委譲します。"
    )
    parser.add_argument("--ack-robots", action="store_true", help="robots.txt・利用規約・許諾確認済みとして実行する")
    parser.add_argument("--max-targets", type=int, default=0, help="処理する自治体数の上限（0 は無制限）")
    parser.add_argument(
        "--per-target-max-meetings",
        type=int,
        default=0,
        help="各自治体で処理する会議数上限（0 は無制限）",
    )
    parser.add_argument("--delay-seconds", type=float, default=1.5, help="各会議アクセス間の待機秒数")
    parser.add_argument(
        "--delay-between-targets",
        type=float,
        default=2.0,
        help="同一ホストで次の自治体を起動するまでの最小待機秒数",
    )
    parser.add_argument("--timeout-ms", type=int, default=10_000, help="Playwright 操作タイムアウト（ミリ秒）")
    parser.add_argument("--save-html", action="store_true", help="ダウンロード失敗時に会議詳細HTMLを保存する")
    parser.add_argument("--headful", action="store_true", help="ブラウザを表示して実行する")
    parser.add_argument("--parallel", type=int, default=6, help="同時に走らせる自治体数")
    parser.add_argument("--index-parallel", type=int, default=1, help="同時に走らせる自治体インデックス更新数")
    parser.add_argument("--refresh-seconds", type=float, default=5.0, help="進捗表示の更新間隔（秒）")
    parser.add_argument("--filter", default="", help="slug / code / name に部分一致する自治体だけを対象にする")
    parser.add_argument("--no-resume", action="store_true", help="既存の保存結果を無視して最初から取り直す")
    parser.add_argument("--no-build-index", action="store_true", help="自治体ごとのスクレイプ完了後に minutes.sqlite を更新しない")
    parser.add_argument("--list-targets", action="store_true", help="対象自治体一覧だけ表示して終了する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    script_path = Path(__file__).with_name("scrape_all_minutes.py")
    command = [
        sys.executable,
        str(script_path),
        "--systems",
        "gijiroku.com",
        "--parallel",
        str(args.parallel),
        "--index-parallel",
        str(args.index_parallel),
        "--per-host-parallel",
        "1",
        "--per-host-start-interval",
        str(args.delay_between_targets),
        "--delay-seconds",
        str(args.delay_seconds),
        "--timeout-ms",
        str(args.timeout_ms),
        "--refresh-seconds",
        str(args.refresh_seconds),
    ]
    if args.ack_robots:
        command.append("--ack-robots")
    if args.max_targets > 0:
        command.extend(["--max-targets", str(args.max_targets)])
    if args.per_target_max_meetings > 0:
        command.extend(["--per-target-max-meetings", str(args.per_target_max_meetings)])
    if args.save_html:
        command.append("--save-html")
    if args.headful:
        command.append("--headful")
    if args.filter:
        command.extend(["--filter", args.filter])
    if args.no_resume:
        command.append("--no-resume")
    if args.no_build_index:
        command.append("--no-build-index")
    if args.list_targets:
        command.append("--list-targets")

    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(command, cwd=str(project_root))
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
