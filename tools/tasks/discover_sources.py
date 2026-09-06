#!/usr/bin/env python3
"""取得元 URL が空の自治体を、少しずつ自動で探索して埋める。

登録簿に URL が無い自治体は `crawl_status=unresolved` で、巡回のキューに
載らない。載らないので何度放置しても状態は変わらない。2026-09-06 の点検で
会議録 245 自治体、例規集 27 自治体がこの形だった。

探索の実装は既にある（`discover_minutes_urls.py` / `discover_reiki_urls.py`）。
人が走らせて結果を見てから TSV へ書く前提だったので、その人がいないと
埋まらない。ここから定期的に呼び、**確信度の高い結果だけ**を
`work/<task>/discovered_sources.json` へ記録する。登録簿は書き換えない。

1 回で全件を回すと相手に負荷がかかるので、既定では少数ずつ処理し、
前回から日数が経った自治体を古い順に選ぶ。放っておけばひと回りする。
見つからなかった場合も試した時刻を残すので、同じ自治体で足踏みしない。

    python tools/tasks/discover_sources.py --task gijiroku --limit 20
    python tools/tasks/discover_sources.py --task reiki --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT))
sys.path.append(str(WORKSPACE_ROOT / "tools"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "gijiroku"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "reiki"))

import discovered_sources  # noqa: E402

DATA_ROOT = WORKSPACE_ROOT / "data"
REGISTRY = {
    "gijiroku": DATA_ROOT / "municipalities" / "assembly_minutes_system_urls.tsv",
    "reiki": DATA_ROOT / "municipalities" / "reiki_system_urls.tsv",
}
# 1 回の実行で探索する自治体数。相手のサイトを 1 自治体あたり十数ページ
# 開くので、まとめて回さない。
DEFAULT_LIMIT = 20


def load_rows(task_name: str) -> list[dict]:
    path = REGISTRY[task_name]
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle, delimiter="\t") if isinstance(row, dict)]


def unresolved_codes(task_name: str) -> list[str]:
    """URL が空で、取得の対象になっていない自治体。

    `excluded` は「本文が無い」「認証が要る」と分かっている自治体なので
    探索しない。探しても同じ結論にしかならない。
    """
    codes: list[str] = []
    for row in load_rows(task_name):
        code = str(row.get("jis_code", "")).strip()
        if code == "" or str(row.get("url", "")).strip():
            continue
        if str(row.get("crawl_status", "")).strip() == "excluded":
            continue
        codes.append(code)
    return codes


def load_names_and_homepages() -> tuple[dict[str, str], dict[str, str]]:
    names: dict[str, str] = {}
    with open(DATA_ROOT / "municipalities" / "municipality_master.tsv",
              "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            if code:
                names[code] = str(row.get("name", "")).strip()
    homepages: dict[str, str] = {}
    with open(DATA_ROOT / "municipalities" / "municipality_homepages.csv",
              "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("jis_code", "")).strip()
            url = str(row.get("url", "")).strip()
            if code and url and code not in homepages:
                homepages[code] = url
    return names, homepages


def discover_minutes(session, code: str, name: str, homepage: str) -> dict[str, str]:
    import discover_minutes_urls

    found = discover_minutes_urls.discover_one(
        session, code, name, homepage,
        max_pages=18, max_depth=3, timeout=15.0, page_delay=0.7,
    )
    return {
        "url": str(found.candidate_url or ""),
        "system_type": str(found.system_type or ""),
        "confidence": str(found.confidence or "none"),
        "note": str(found.evidence or found.note or ""),
    }


def discover_reiki(session, code: str, name: str, homepage: str) -> dict[str, str]:
    import discover_reiki_urls

    finding = discover_reiki_urls.Finding(
        slug="", code=code, name=name, registered_url="", registered_system=""
    )
    finding = discover_reiki_urls.discover_one(session, finding, homepage, pause=0.5)
    # 入口を開いて中身まで確かめられたものだけを採用する。系統を推定できた
    # だけでは、例規集ではない別のページを掴んでいることがある。
    confidence = "high" if (finding.verified == "ok" and finding.detected_system) else "low"
    return {
        "url": str(finding.entry_url or ""),
        "system_type": str(finding.detected_system or ""),
        "confidence": confidence,
        "note": str(finding.evidence or finding.note or ""),
    }


DISCOVERERS = {"gijiroku": discover_minutes, "reiki": discover_reiki}


def run(task_name: str, *, limit: int = DEFAULT_LIMIT, retry_days: int = 14,
        dry_run: bool = False) -> dict:
    if task_name not in REGISTRY:
        raise ValueError(f"未対応の task: {task_name}")

    codes = discovered_sources.due_codes(
        task_name, unresolved_codes(task_name), retry_days=retry_days, limit=limit
    )
    if not codes:
        print(f"[INFO] {task_name}: 探索する自治体はありません", flush=True)
        return {"task": task_name, "checked": 0, "registered": 0}

    import requests

    names, homepages = load_names_and_homepages()
    session = requests.Session()
    discover = DISCOVERERS[task_name]

    registered = 0
    for index, code in enumerate(codes, 1):
        name = names.get(code, "")
        homepage = homepages.get(code, "")
        if not homepage:
            if not dry_run:
                discovered_sources.record(task_name, code, confidence="none",
                                          note="公式ホームページ URL が無い")
            print(f"[{index}/{len(codes)}] × {code} {name} 公式ホームページ URL が無い", flush=True)
            continue
        try:
            found = discover(session, code, name, homepage)
        except Exception as error:
            found = {"url": "", "system_type": "", "confidence": "none", "note": f"error: {error}"}

        usable = discovered_sources.is_usable(found)
        mark = "○" if usable else "×"
        print(
            f"[{index}/{len(codes)}] {mark} {code} {name} "
            f"{found['confidence']:6s} {found['system_type'] or '-':16s} "
            f"{found['url'] or found['note']}",
            flush=True,
        )
        if dry_run:
            continue
        discovered_sources.record(
            task_name, code,
            url=found["url"], system_type=found["system_type"],
            confidence=found["confidence"], note=found["note"],
        )
        if usable:
            registered += 1

    print(
        f"[INFO] {task_name}: {len(codes)}件を探索し、{registered}件を取得対象に加えました"
        + ("（--dry-run のため記録していません）" if dry_run else ""),
        flush=True,
    )
    return {"task": task_name, "checked": len(codes), "registered": registered}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="取得元 URL が空の自治体を探索して埋める。")
    parser.add_argument("--task", choices=sorted(REGISTRY), required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="1 回で探索する自治体数（0 は無制限）")
    parser.add_argument("--retry-days", type=int, default=14,
                        help="同じ自治体を探索し直すまでの日数")
    parser.add_argument("--dry-run", action="store_true", help="記録せず結果だけ表示する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run(args.task, limit=args.limit, retry_days=args.retry_days, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
