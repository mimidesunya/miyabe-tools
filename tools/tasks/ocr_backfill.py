#!/usr/bin/env python3
"""文字情報のない PDF を OCR で本文にする、後追いの掃き取り。

紙をスキャンしただけの PDF は `extract_pdf_text` が空文字を返し、その会議は
`empty_pdf_text` として除外される。2026-09-06 の点検では 144 自治体・3,860 件
がこの形だった。OCR が無い限り、何周回しても同じように除外される。

**通常の巡回に OCR を混ぜない。** 1 件に数十秒かかるので、取得の周期へ入れる
と一巡が数日延びる。ここでは対象の自治体だけを選び、OCR を有効にして
既存のスクレイパをもう一度走らせる。保存も題名の補正も会議録判定も、
通常の経路がそのまま効く。

OCR で本文になった会議は次から `skipped_existing` になるので、**毎周回
再取得していた無駄も同時に消える**。取れなかった分は試行回数で打ち切る
（`tools/gijiroku/pdf_ocr.py`）。

    python tools/tasks/ocr_backfill.py --limit 3
    python tools/tasks/ocr_backfill.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT))
sys.path.append(str(WORKSPACE_ROOT / "tools"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "gijiroku"))

import gijiroku_targets  # noqa: E402
import pdf_ocr  # noqa: E402

WORK_ROOT = WORKSPACE_ROOT / "work" / "gijiroku"
# 1 回の掃き取りで扱う自治体数。1 自治体に数十分かかることがある。
DEFAULT_LIMIT = 2
# 会議録スクレイパの入口。scrape_all_minutes と同じ対応表を使う。
SCRAPER_TIMEOUT_SECONDS = 6 * 60 * 60


def empty_pdf_count(work_dir: Path) -> int:
    """その自治体で「文字情報が無い」として除外された件数。

    項目ごとの結果は実行のたびに消えるので、実行の要約に残る件数を見る
    （`scrape_state.json` の `validation.status_counts.empty_pdf_text`）。
    """
    try:
        state = json.loads((work_dir / "scrape_state.json").read_text(encoding="utf-8"))
    except Exception:
        return 0
    validation = state.get("validation") if isinstance(state, dict) else None
    if not isinstance(validation, dict):
        return 0
    counts = validation.get("status_counts")
    if not isinstance(counts, dict):
        return 0
    try:
        return max(0, int(counts.get("empty_pdf_text") or 0))
    except (TypeError, ValueError):
        return 0


def exhausted_count(work_dir: Path) -> int:
    """OCR しても本文にならず、試行回数を使い切った件数。"""
    exhausted = 0
    for entry in pdf_ocr.load_attempts(work_dir).values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "") == "ok":
            continue
        if int(entry.get("attempts") or 0) >= pdf_ocr.MAX_ATTEMPTS:
            exhausted += 1
    return exhausted


def pending_targets() -> list[tuple[str, int]]:
    """OCR 待ちの自治体を、件数の多い順に返す。

    試し尽くした分を引いて数える。全部試し終えた自治体は載せない。
    そうしないと、OCR でも読めない自治体を毎回選び直すことになる。
    """
    pending: list[tuple[str, int]] = []
    if not WORK_ROOT.is_dir():
        return pending
    for work_dir in sorted(WORK_ROOT.iterdir()):
        if not work_dir.is_dir():
            continue
        empty = empty_pdf_count(work_dir)
        if empty <= 0:
            continue
        remaining = empty - exhausted_count(work_dir)
        if remaining > 0:
            pending.append((work_dir.name, remaining))
    pending.sort(key=lambda row: -row[1])
    return pending


def child_command(target: dict) -> list[str]:
    system_type = str(target.get("system_type") or "")
    script = gijiroku_targets.canonical_minutes_system_type(system_type)
    from tools.gijiroku import scrape_all_minutes

    relative = scrape_all_minutes.SUPPORTED_SYSTEMS.get(script)
    if not relative:
        raise ValueError(f"OCR に対応していない系統: {system_type}")
    return [
        sys.executable,
        str(WORKSPACE_ROOT / "tools" / "gijiroku" / relative),
        "--slug", str(target.get("slug") or ""),
        "--ack-robots",
    ]


def run_one(slug: str, *, timeout_seconds: int = SCRAPER_TIMEOUT_SECONDS) -> dict:
    target = gijiroku_targets.load_gijiroku_target(slug)
    command = child_command(target)
    environment = dict(os.environ, MIYABE_MINUTES_OCR="1")
    print(f"[INFO] OCR 付きで取り直します: {slug} [{target.get('system_type')}]", flush=True)
    completed = subprocess.run(
        command,
        cwd=str(WORKSPACE_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    tail = [line for line in (completed.stdout or "").splitlines() if "OCR" in line]
    for line in tail[-5:]:
        print(f"    {line}", flush=True)
    return {
        "slug": slug,
        "returncode": completed.returncode,
        "ocr_lines": len(tail),
    }


def run(limit: int = DEFAULT_LIMIT) -> dict:
    if pdf_ocr.tool_directory() is None:
        print("[WARN] NDLOCR-Lite が見つかりません。OCR の掃き取りは何もしません。", flush=True)
        return {"checked": 0, "reason": "NDLOCR-Lite が無い"}

    targets = pending_targets()
    if not targets:
        print("[INFO] OCR 待ちの自治体はありません", flush=True)
        return {"checked": 0}

    handled = []
    for slug, remaining in targets[: max(1, int(limit))]:
        try:
            result = run_one(slug)
        except Exception as error:
            result = {"slug": slug, "error": str(error)}
        result["pending_before"] = remaining
        handled.append(result)
    print(f"[INFO] {len(handled)}自治体を OCR 付きで取り直しました", flush=True)
    return {"checked": len(handled), "targets": handled}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="文字情報のない PDF を OCR で本文にする。")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="1 回で扱う自治体数")
    parser.add_argument("--list", action="store_true", help="対象の自治体と件数だけを表示する")
    parser.add_argument("--slug", default="", help="自治体を 1 つだけ指定する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list:
        rows = pending_targets()
        total = sum(count for _slug, count in rows)
        print(f"OCR 待ち: {len(rows)}自治体 / {total}件")
        for slug, count in rows[:40]:
            print(f"  {count:5d}  {slug}")
        return 0
    if args.slug:
        print(run_one(args.slug))
        return 0
    run(limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
