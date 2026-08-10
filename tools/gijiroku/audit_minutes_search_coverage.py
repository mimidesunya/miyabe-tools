#!/usr/bin/env python3
"""Compare saved minutes, source-crawl coverage, task progress, and OpenSearch counts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOT = TOOLS_ROOT / "search"
sys.path.insert(0, str(SEARCH_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_opensearch_index as search_builder  # type: ignore  # noqa: E402
import gijiroku_targets  # type: ignore  # noqa: E402
from opensearch_client import OpenSearchClient  # type: ignore  # noqa: E402


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="取得済み会議録と公開検索件数、および取得元の全件走査状態を照合します。"
    )
    parser.add_argument("--system", default="", help="system family で絞り込み（例: dbsr）")
    parser.add_argument("--slug", action="append", default=[], help="自治体 slug。複数指定可。")
    parser.add_argument("--only-issues", action="store_true", help="件数差または取得範囲未確定だけ表示")
    parser.add_argument("--json", action="store_true", help="JSON で出力")
    parser.add_argument("--opensearch-url", default=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"))
    parser.add_argument("--opensearch-user", default=os.environ.get("OPENSEARCH_USER", ""))
    parser.add_argument("--opensearch-password", default=os.environ.get("OPENSEARCH_PASSWORD", ""))
    parser.add_argument("--minutes-alias", default=os.environ.get("MIYABE_MINUTES_ALIAS", "miyabe-minutes-current"))
    parser.add_argument(
        "--insecure-dev",
        action="store_true",
        default=os.environ.get("OPENSEARCH_INSECURE_DEV", "").lower() in {"1", "true", "yes", "on"},
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def task_progress_by_slug() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    task_root = WORKSPACE_ROOT / "data" / "background_tasks"
    for task_name in ["gijiroku", "gijiroku_snapshot"]:
        items = read_json(task_root / f"{task_name}.json").get("items")
        if not isinstance(items, dict):
            continue
        for slug, item in items.items():
            if not isinstance(item, dict) or str(slug).strip() == "":
                continue
            existing = result.get(str(slug), {})
            candidate_time = str(item.get("updated_at") or item.get("finished_at") or item.get("last_checked_at") or "")
            existing_time = str(
                existing.get("updated_at") or existing.get("finished_at") or existing.get("last_checked_at") or ""
            )
            if not existing or candidate_time >= existing_time:
                result[str(slug)] = item
    return result


def indexed_counts(client: OpenSearchClient, alias: str) -> dict[str, int]:
    response = client.request(
        "POST",
        f"/{alias}/_search",
        body={
            "size": 0,
            "aggs": {"slugs": {"terms": {"field": "slug", "size": 10000}}},
        },
    )
    buckets = response.get("aggregations", {}).get("slugs", {}).get("buckets", [])
    result: dict[str, int] = {}
    if not isinstance(buckets, list):
        return result
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        slug = str(bucket.get("key") or "").strip()
        count = max(0, int(bucket.get("doc_count") or 0))
        if slug and count > 0:
            result[slug] = count
    return result


def int_value(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def main() -> int:
    args = parse_args()
    requested_slugs = {str(value).strip() for value in args.slug if str(value).strip()}
    targets = gijiroku_targets.iter_gijiroku_targets(
        expected_system=str(args.system).strip() or None,
        include_inactive=False,
    )
    if requested_slugs:
        targets = [target for target in targets if str(target.get("slug") or "") in requested_slugs]

    local_counts = search_builder.count_minutes_documents_by_slug(
        slugs={str(target.get("slug") or "") for target in targets}
    )
    client = OpenSearchClient(
        args.opensearch_url,
        user=args.opensearch_user,
        password=args.opensearch_password,
        insecure_dev=args.insecure_dev,
    )
    search_counts = indexed_counts(client, args.minutes_alias)
    task_items = task_progress_by_slug()

    rows: list[dict[str, Any]] = []
    for target in targets:
        slug = str(target.get("slug") or "").strip()
        state = read_json(Path(target["work_dir"]) / "scrape_state.json")
        coverage = state.get("source_coverage") if isinstance(state.get("source_coverage"), dict) else {}
        validation = state.get("validation") if isinstance(state.get("validation"), dict) else {}
        task_item = task_items.get(slug, {})
        saved_file_count = int_value(local_counts.get(slug))
        indexed_count = int_value(search_counts.get(slug))
        source_state = str(coverage.get("state") or "unknown").strip() or "unknown"
        verified_acquired = int_value(validation.get("progress_current")) if source_state == "complete" else 0
        issues: list[str] = []
        # 取得したファイルには目次も混ざり、検索に載るのは本文だけ。件数差
        # だけでは反映待ちと言えないので、1 件も検索できないときに限る。
        # 種別の内訳が残っている取得元は、この下で本文の件数と突き合わせる。
        if verified_acquired > 0 and indexed_count <= 0:
            issues.append("index_pending")
        elif verified_acquired > 0 and indexed_count > max(verified_acquired, saved_file_count):
            # validation は最後の走査で確認した件数で、累計の取得数ではない。
            # 取得済みファイルより多く検索できるときだけ、取りすぎを疑う。
            issues.append("index_ahead_of_verified")
        # 走査記録を持たない取得元でも検索反映は遅れる。取得したファイルの
        # うち本文が何件かは index 構築時の内訳に残るので、それと突き合わせる。
        # 内訳が無い取得元は、保存件数との大きな開きだけを手掛かりにする。
        kinds = read_json(Path(target["work_dir"]) / "document_kinds.json")
        indexable = int_value(kinds.get("indexable")) if isinstance(kinds, dict) else 0
        if isinstance(kinds, dict) and indexable > indexed_count:
            if "index_pending" not in issues:
                issues.append("index_pending")
        elif not kinds and saved_file_count >= 20 and indexed_count * 2 < saved_file_count:
            issues.append("index_far_behind_saved")
        if source_state in {"partial_planned", "partial_limit", "partial_error", "partial_recent_only"}:
            issues.append(source_state)
        elif source_state != "complete" and saved_file_count > 0:
            issues.append("coverage_unknown")

        row = {
            "slug": slug,
            "code": str(target.get("code") or ""),
            "name": str(target.get("name") or ""),
            "system": str(target.get("system_family") or target.get("system_type") or ""),
            "saved_files": saved_file_count,
            "indexable": indexable if isinstance(kinds, dict) else None,
            "verified_acquired": verified_acquired,
            "indexed": indexed_count,
            "delta": (verified_acquired - indexed_count) if verified_acquired > 0 else None,
            "source_state": source_state,
            "source_discovered": int_value(coverage.get("discovered_count")),
            "validated": int_value(validation.get("progress_current")),
            "task_current": int_value(task_item.get("progress_current")),
            "task_total": int_value(task_item.get("progress_total")),
            "task_status": str(task_item.get("status") or ""),
            "issues": issues,
        }
        if not args.only_issues or issues:
            rows.append(row)

    rows.sort(key=lambda row: (-abs(int(row["delta"] or 0)), str(row["slug"])))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    columns = [
        "slug",
        "name",
        "system",
        "saved_files",
        "indexable",
        "verified_acquired",
        "indexed",
        "delta",
        "source_state",
        "source_discovered",
        "validated",
        "task_current",
        "task_total",
        "task_status",
        "issues",
    ]
    print("\t".join(columns))
    for row in rows:
        values = []
        for column in columns:
            value = row[column]
            values.append(",".join(value) if isinstance(value, list) else str(value))
        print("\t".join(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
