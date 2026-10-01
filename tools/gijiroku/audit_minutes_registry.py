#!/usr/bin/env python3
"""会議録台帳の URL・system_type の変更を検出し、取得判断と変更検出値を TSV へ記録する。

robots.txt は取得可否の根拠にしない（crawl_policy.py の方針）。取得しないのは、
運用者が台帳に除外の理由を書いた行だけで、ここではその判断を保ったまま、
確認日と変更検出値（policy_fingerprint）を更新する。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.gijiroku.crawl_policy import (  # noqa: E402
    policy_fingerprint,
    policy_fingerprint_is_current,
    required_crawl_urls,
)


DEFAULT_TSV = ROOT / "data" / "municipalities" / "assembly_minutes_system_urls.tsv"
DEFAULT_POLICY_CACHE = ROOT / "work" / "gijiroku" / "registry_policy_cache.json"
FIELDNAMES = [
    "jis_code",
    "url",
    "system_type",
    "crawl_status",
    "exclusion_reason",
    "exclusion_detail",
    "policy_checked_at",
    "policy_fingerprint",
]
POLICY_RESULT_FIELDS = [
    "crawl_status",
    "exclusion_reason",
    "exclusion_detail",
    "policy_checked_at",
    "policy_fingerprint",
]
VALID_CACHE_STATUSES = {"enabled", "excluded", "review_required", "unresolved"}


@dataclass(frozen=True)
class AuditSummary:
    rows: int
    selected_rows: int
    changed_rows: int
    enabled_targets_changed: bool
    statuses: dict[str, int]
    reasons: dict[str, int]
    wrote: bool


def normalized_row(row: dict[str, str]) -> dict[str, str]:
    return {key: str(row.get(key, "") or "").strip() for key in FIELDNAMES}


def kept_detail(row: dict[str, str]) -> str:
    """enabled に戻すとき、残してよい説明を返す。

    除外の理由が書かれていれば、説明はその理由のものなので消す。理由が空の
    まま説明だけがある行は、運用者の覚書（兵庫県「旧型 CGI で kensakusystem
    スクレイパが扱えない」など）なので残す。
    """
    if str(row.get("exclusion_reason", "")).strip():
        return ""
    return str(row.get("exclusion_detail", "")).strip()


def classify_row(row: dict[str, str], *, checked_at: str) -> dict[str, str]:
    updated = normalized_row(row)
    current_fingerprint = policy_fingerprint(row)
    if not required_crawl_urls(row):
        updated.update(
            {
                "crawl_status": "unresolved",
                "exclusion_reason": "source_url_unresolved",
                "exclusion_detail": "会議録の代表URLを未特定",
                "policy_checked_at": checked_at,
                "policy_fingerprint": current_fingerprint,
            }
        )
        return updated

    # 除外の理由が書かれた行は運用者の判断なので残す。動画しか公開していない
    # （video_only）、公開が無い（not_published）などを一律 enabled に戻すと、
    # 取れないものを取りに行き続けることになる。
    if str(row.get("exclusion_reason", "")).strip():
        updated.update(
            {
                "policy_checked_at": checked_at,
                "policy_fingerprint": current_fingerprint,
            }
        )
        return updated
    updated.update(
        {
            "crawl_status": "enabled",
            "exclusion_reason": "",
            "exclusion_detail": kept_detail(row),
            "policy_checked_at": checked_at,
            "policy_fingerprint": current_fingerprint,
        }
    )
    return updated


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy_cache(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    targets = payload.get("targets", {}) if isinstance(payload, dict) else {}
    if not isinstance(targets, dict):
        return {}
    return {
        str(code): {key: str(value.get(key, "") or "").strip() for key in POLICY_RESULT_FIELDS}
        for code, value in targets.items()
        if isinstance(value, dict)
    }


def apply_cached_policies(
    rows: list[dict[str, str]],
    cache: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    restored: list[dict[str, str]] = []
    for row in rows:
        updated = normalized_row(row)
        code = str(row.get("jis_code", "")).strip()
        cached = cache.get(code, {})
        current_fingerprint = policy_fingerprint(row)
        if updated["crawl_status"] == "enabled":
            # enabled は運用者の明示許可。キャッシュの判定を復元せず、
            # URL/system_type の現在値だけを記録して監査対象から外す。
            if updated["policy_fingerprint"] != current_fingerprint or updated["exclusion_reason"]:
                updated["policy_checked_at"] = ""
            updated["exclusion_detail"] = kept_detail(updated)
            updated["exclusion_reason"] = ""
            updated["policy_fingerprint"] = current_fingerprint
            restored.append(updated)
            continue
        if updated["crawl_status"] == "excluded":
            # 録画のみ・公開なしなどの除外も運用者が台帳に書いた判断。キャッシュに
            # 残る除外前の enabled で戻してはいけない（指紋は URL と system_type
            # だけなので、除外しても一致する）。2026-09-18 にこれで 27 件が
            # enabled に戻り、本文の無い取得元を取りに行っていた。
            updated["policy_fingerprint"] = current_fingerprint
            restored.append(updated)
            continue
        if (
            str(cached.get("crawl_status", "")) in VALID_CACHE_STATUSES
            and str(cached.get("policy_fingerprint", "")) == current_fingerprint
        ):
            updated.update({key: str(cached.get(key, "") or "").strip() for key in POLICY_RESULT_FIELDS})
        restored.append(updated)
    return restored


def write_policy_cache(path: Path, cache: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "targets": cache}
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_rows(path: Path, rows: list[dict[str, str]], *, expected_digest: str | None = None) -> None:
    # 監査中に別のデプロイで正本が変わった場合、その新しい内容を上書きしない。
    if expected_digest is not None and file_digest(path) != expected_digest:
        raise RuntimeError(f"registry changed while registry audit was running: {path}")

    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        # mkstemp は 0600 で作るため、そのまま replace すると PHP/www-data が
        # 公開状態の判定に使う台帳を読めなくなる。台帳は秘密情報を含まない。
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def audit_registry(
    path: Path = DEFAULT_TSV,
    *,
    write: bool = False,
    codes: set[str] | None = None,
    stale_only: bool = False,
    stamp_fingerprints: bool = False,
    checked_at: str | None = None,
    cache_path: Path | None = None,
) -> AuditSummary:
    source_digest = file_digest(path)
    source_rows = read_rows(path)
    policy_cache = load_policy_cache(cache_path)
    rows = apply_cached_policies(source_rows, policy_cache)
    selected_codes = {str(value).strip() for value in (codes or set()) if str(value).strip()}

    def enabled_override_changed(row: dict[str, str]) -> bool:
        if str(row.get("crawl_status", "")).strip() != "enabled":
            return False
        code = str(row.get("jis_code", "")).strip()
        cached = policy_cache.get(code, {})
        return (
            str(cached.get("crawl_status", "")) != "enabled"
            or str(cached.get("policy_fingerprint", "")) != policy_fingerprint(row)
        )

    operator_enabled_changed = bool(
        cache_path is not None
        and policy_cache
        and any(enabled_override_changed(row) for row in source_rows)
    )

    def is_selected(row: dict[str, str]) -> bool:
        if selected_codes and str(row.get("jis_code", "")).strip() not in selected_codes:
            return False
        if not stamp_fingerprints and str(row.get("crawl_status", "")).strip() == "enabled":
            return False
        if stale_only and policy_fingerprint_is_current(row):
            return False
        return True

    selected_indexes = {index for index, row in enumerate(rows) if is_selected(row)}
    audit_date = checked_at or date.today().isoformat()
    audited: list[dict[str, str]] = []
    enabled_targets_changed = operator_enabled_changed
    for index, row in enumerate(rows):
        before = normalized_row(row)
        if index not in selected_indexes:
            updated = before
        elif stamp_fingerprints:
            updated = before
            updated["policy_fingerprint"] = policy_fingerprint(row)
        else:
            updated = classify_row(row, checked_at=audit_date)
            if updated["crawl_status"] == "enabled" and (
                not policy_fingerprint_is_current(row) or before["crawl_status"] != "enabled"
            ):
                enabled_targets_changed = True
        audited.append(updated)

    changed_rows = sum(
        before != after
        for before, after in zip((normalized_row(row) for row in source_rows), audited)
    )
    counts = Counter(row["crawl_status"] for row in audited)
    reasons = Counter(row["exclusion_reason"] for row in audited if row["exclusion_reason"])
    wrote = bool(write and changed_rows)
    if wrote:
        write_rows(path, audited, expected_digest=source_digest)

    if write and cache_path is not None and not stamp_fingerprints:
        updated_cache: dict[str, dict[str, str]] = {}
        for row in audited:
            code = str(row.get("jis_code", "")).strip()
            if (
                code
                and row["crawl_status"] in VALID_CACHE_STATUSES
                and row["policy_fingerprint"] == policy_fingerprint(row)
            ):
                updated_cache[code] = {key: row[key] for key in POLICY_RESULT_FIELDS}
        if updated_cache != policy_cache:
            write_policy_cache(cache_path, updated_cache)

    return AuditSummary(
        rows=len(audited),
        selected_rows=len(selected_indexes),
        changed_rows=changed_rows,
        enabled_targets_changed=enabled_targets_changed,
        statuses=dict(counts),
        reasons=dict(reasons),
        wrote=wrote,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="会議録台帳の URL・system_type の変更を検出して取得判断を記録する")
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    parser.add_argument("--write", action="store_true", help="監査結果をTSVへ保存する")
    parser.add_argument("--codes", default="", help="監査する自治体コードのカンマ区切り（空なら全件）")
    parser.add_argument("--stale-only", action="store_true", help="URLかsystem_typeが変わった行だけ監査する")
    parser.add_argument(
        "--stamp-fingerprints",
        action="store_true",
        help="既存の監査結果を保ったまま変更検出値だけ設定する（移行用）",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="監査結果を保持・復元する永続キャッシュ（Celery runtime用）",
    )
    args = parser.parse_args()

    selected_codes = {value.strip() for value in args.codes.split(",") if value.strip()}
    summary = audit_registry(
        args.tsv,
        write=args.write,
        codes=selected_codes,
        stale_only=args.stale_only,
        stamp_fingerprints=args.stamp_fingerprints,
        cache_path=args.cache,
    )
    print(f"rows={summary.rows} selected={summary.selected_rows} changed={summary.changed_rows}")
    print("statuses " + " ".join(f"{key}={value}" for key, value in sorted(summary.statuses.items())))
    print("reasons " + " ".join(f"{key}={value}" for key, value in sorted(summary.reasons.items())))
    if summary.wrote:
        print(f"[WROTE] {args.tsv}")
    elif args.write:
        print("[UNCHANGED]")
    else:
        print("(dry-run; pass --write to apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
