#!/usr/bin/env python3
"""探索の結果を、包括外部監査の取得元台帳へ書き出す。

台帳の形は会議録・例規・事務事業評価と同じ（`jis_code` / `url` /
`system_type` / `crawl_status` / `exclusion_reason` / `exclusion_detail`）。
全 1,794 自治体の行を必ず持ち、まだ調べていない自治体は `unresolved` で置く。
そうしないと「調べて見つからなかった」と「まだ調べていない」が混ざる。

確信度の扱い:

- `high` → `enabled`（リンク文字か題名が包括外部監査を名乗っている）
- `medium` → `review_required`（本文に語はあるが、入口かどうか人の確認待ち）
- `low` / `none` → `unresolved`。義務付けの団体は必ずあるはずなので、
  見つからないのは探索の側の問題として残す（`exclusion_detail` に印）

BOM は付けない（PHP 側が `jis_code` を引けなくなる）。

    python tools/kansa/build_kansa_registry.py --from-csv work/kansa/discovery_xxx.csv
    python tools/kansa/build_kansa_registry.py --from-csv a.csv b.csv --dry-run
"""

from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data" / "municipalities"
REGISTRY = DATA_ROOT / "gaibu_kansa_system_urls.tsv"
COLUMNS = ("jis_code", "url", "system_type", "crawl_status", "exclusion_reason", "exclusion_detail")
# 事務事業評価と同じく、共通のベンダ系システムは無い。全部「独自」。
SYSTEM_TYPE = "独自"


def load_master_codes() -> list[str]:
    codes: list[str] = []
    with io.open(DATA_ROOT / "municipality_master.tsv", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            if code:
                codes.append(code)
    return sorted(set(codes))


def load_existing() -> dict[str, dict[str, str]]:
    if not REGISTRY.exists():
        return {}
    with io.open(REGISTRY, encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("jis_code", "")).strip(): row
            for row in csv.DictReader(handle, delimiter="\t")
            if str(row.get("jis_code", "")).strip()
        }


def read_findings(paths: list[Path]) -> dict[str, dict[str, str]]:
    """複数回に分けて走らせた結果を、後の行が勝つ形で重ねる。"""
    findings: dict[str, dict[str, str]] = {}
    for path in paths:
        with io.open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("jis_code", "")).strip()
                if code:
                    findings[code] = row
    return findings


def row_for(code: str, found: dict[str, str] | None, previous: dict[str, str] | None) -> dict[str, str]:
    if found is None:
        return previous or {
            "jis_code": code, "url": "", "system_type": "", "crawl_status": "unresolved",
            "exclusion_reason": "source_url_unresolved", "exclusion_detail": "",
        }
    confidence = str(found.get("confidence", "")).strip()
    url = str(found.get("url", "")).strip()
    kind = str(found.get("kind", "")).strip()
    attachments = str(found.get("attachments", "")).strip() or "0"
    evidence = str(found.get("evidence", "")).strip()
    individual = str(found.get("individual_only", "")).strip()
    prefix = f"{kind}（義務）" if kind else "任意"

    if confidence == "high" and url:
        return {
            "jis_code": code, "url": url, "system_type": SYSTEM_TYPE, "crawl_status": "enabled",
            "exclusion_reason": "", "exclusion_detail": f"{prefix} {evidence} 添付{attachments}",
        }
    if confidence == "medium" and url:
        return {
            "jis_code": code, "url": url, "system_type": SYSTEM_TYPE,
            "crawl_status": "review_required", "exclusion_reason": "needs_confirmation",
            "exclusion_detail": f"{prefix} {evidence} 添付{attachments} 入口かどうか要確認",
        }
    detail = f"{prefix} 確信度{confidence or 'none'}"
    if individual:
        detail += " 個別外部監査のみ"
    if url:
        detail += f" 候補 {url}"
    return {
        "jis_code": code, "url": "", "system_type": "", "crawl_status": "unresolved",
        "exclusion_reason": "source_url_unresolved", "exclusion_detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="探索結果から包括外部監査の台帳を作る。")
    parser.add_argument("--from-csv", nargs="+", required=True, help="探索結果 CSV（複数可）")
    parser.add_argument("--dry-run", action="store_true", help="書かずに内訳だけ出す")
    args = parser.parse_args()

    findings = read_findings([Path(path) for path in args.from_csv])
    existing = load_existing()
    rows = [row_for(code, findings.get(code), existing.get(code)) for code in load_master_codes()]

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["crawl_status"]] = counts.get(row["crawl_status"], 0) + 1
    print(f"行 {len(rows)}  " + "  ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if args.dry_run:
        return 0

    # BOM を付けない。PHP 側が jis_code を引けなくなる。
    with io.open(REGISTRY, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), delimiter="\t",
                                lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
    print(f"書き出し: {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
