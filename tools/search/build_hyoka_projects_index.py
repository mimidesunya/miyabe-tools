#!/usr/bin/env python3
"""事務事業評価の事業ごとの索引を作り直す（毎回全部。新しい世代を作ってから alias を切り替える）。

入力は画面用の控え `data/hyoka`（読むだけ）。文書の年度・種別・題名と、検索結果の ID（頁で分けた
文書は `:p<頁>`）は文書の索引 `miyabe-hyoka-current` から読む。手元で中身を確かめるだけなら
`--out` で索引に入れる文書を NDJSON に書き、`--manifest-root` で文書の情報を取得の manifest から読む。

    python3 tools/search/build_hyoka_projects_index.py --display-dir data/hyoka
    python3 tools/search/build_hyoka_projects_index.py --display-dir data/hyoka \\
        --manifest-root work/hyoka --out /tmp/projects.ndjson

公開中の索引より件数が半分未満に減るときは、alias を切り替えずに止める（控えを空で作り直した事故の備え）。
古い世代は、今の世代のほかに `--keep-previous` 個（既定 1）を残して消す。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

SEARCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SEARCH_DIR))

import hyoka_projects  # noqa: E402
from opensearch_client import OpenSearchClient, OpenSearchRequestError  # noqa: E402
from opensearch_mappings import load_index_settings  # noqa: E402

WORKSPACE_ROOT = SEARCH_DIR.parents[1]
META_FIELDS = ["slug", "local_id", "page", "fiscal_year", "kind", "title", "source_url",
               "municipality_name", "pref_code", "pref_name"]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="事務事業評価の事業ごとの索引を作り直す")
    parser.add_argument("--display-dir", type=Path,
                        default=Path(os.environ.get("MIYABE_HYOKA_DISPLAY_DIR", str(WORKSPACE_ROOT / "data" / "hyoka"))))
    parser.add_argument("--municipality-master", type=Path,
                        default=WORKSPACE_ROOT / "data" / "municipalities" / "municipality_master.tsv")
    parser.add_argument("--opensearch-url", default=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"))
    parser.add_argument("--opensearch-user", default=os.environ.get("OPENSEARCH_USER", ""))
    parser.add_argument("--opensearch-password", default=os.environ.get("OPENSEARCH_PASSWORD", ""))
    parser.add_argument("--insecure-dev", action="store_true",
                        default=os.environ.get("OPENSEARCH_INSECURE_DEV", "").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--doc-alias", default=os.environ.get("MIYABE_HYOKA_ALIAS", "miyabe-hyoka-current"),
                        help="文書の情報を読む、事務事業評価の文書の索引")
    parser.add_argument("--alias", default=os.environ.get("MIYABE_HYOKA_PROJECTS_ALIAS", hyoka_projects.ALIAS))
    parser.add_argument("--build-id", default="", help="世代名の後ろ。既定は UTC の日時")
    parser.add_argument("--keep-previous", type=int, default=1, help="残す前の世代の数")
    parser.add_argument("--allow-shrink", action="store_true", help="件数が半分未満に減っても切り替える")
    parser.add_argument("--bulk-size", type=int, default=500)
    parser.add_argument("--out", type=Path, help="索引に入れずに、文書を NDJSON に書く")
    parser.add_argument("--manifest-root", type=Path, action="append", default=[],
                        help="--out のとき、文書の情報を読む取得の保存先（<slug>/manifest.json）。何度でも")
    parser.add_argument("--slug", action="append", default=[], help="--out のとき、この自治体だけ")
    args = parser.parse_args(argv)
    if args.slug and args.out is None:
        parser.error("--slug は --out と一緒に使う（索引は毎回全部作り直す）")
    if args.manifest_root and args.out is None:
        parser.error("--manifest-root は --out と一緒に使う（索引の検索の ID は文書の索引から読む）")
    return args


def strip_pref(name: str, pref: str) -> str:
    # 文書の索引は自治体名に都道府県名を付けて持っている（「神奈川県 川崎市」）。
    return name[len(pref) + 1:] if pref and name.startswith(pref + " ") else name


def metadata_from_index(client: OpenSearchClient, alias: str) -> dict[str, dict]:
    """文書の索引の全件から、文書ごとの情報と検索の ID の一覧を作る。"""
    metadata: dict[str, dict] = {}
    response = client.request("POST", f"/{quote(alias)}/_search", query={"scroll": "2m"},
                              body={"size": 5000, "_source": META_FIELDS, "sort": ["_doc"], "query": {"match_all": {}}})
    scroll_id = response.get("_scroll_id")
    try:
        while True:
            hits = ((response.get("hits") or {}).get("hits")) or []
            if not hits:
                break
            for hit in hits:
                source = hit.get("_source") or {}
                slug = str(source.get("slug") or "")
                local_id = str(source.get("local_id") or "")
                if not slug or not local_id:
                    continue
                entry = metadata.setdefault(f"{slug}:{local_id}", {
                    "fiscal_year": source.get("fiscal_year"), "kind": source.get("kind") or "",
                    "title": source.get("title") or "", "source_url": source.get("source_url") or "",
                    "municipality_name": strip_pref(str(source.get("municipality_name") or ""), str(source.get("pref_name") or "")),
                    "pref_code": source.get("pref_code") or "", "pref_name": source.get("pref_name") or "", "ids": [],
                })
                entry["ids"].append(str(hit.get("_id") or ""))
            response = client.request("POST", "/_search/scroll", body={"scroll": "2m", "scroll_id": scroll_id})
            scroll_id = response.get("_scroll_id") or scroll_id
    finally:
        if scroll_id:
            try:
                client.request("DELETE", "/_search/scroll", body={"scroll_id": scroll_id})
            except (OpenSearchRequestError, RuntimeError):
                pass
    for entry in metadata.values():
        entry["ids"].sort()
    return metadata


def alias_indices(client: OpenSearchClient, alias: str) -> list[str]:
    try:
        response = client.request("GET", f"/_alias/{quote(alias)}")
    except OpenSearchRequestError as exc:
        if exc.status == 404:
            return []
        raise
    return sorted(response.keys()) if isinstance(response, dict) else []


def count(client: OpenSearchClient, target: str) -> int:
    return int(client.request("GET", f"/{quote(target)}/_count").get("count") or 0)


def summarize(batches: list[list[dict]]) -> dict:
    documents = [document for batch in batches for document in batch]
    return {
        "documents": len(documents),
        "municipalities": len(batches),
        "with_amounts": sum(1 for document in documents if document["has_amounts"]),
        "with_cost": sum(1 for document in documents if document["cost_yen"] is not None),
        "amount_facts": sum(len(document["amounts"]) for document in documents),
        "best_facts": sum(1 for document in documents for fact in document["amounts"] if fact["best"]),
        "without_document_id": sum(1 for document in documents if not document["document_id"]),
    }


def write_ndjson(path: Path, batches: list[list[dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for batch in batches:
            for document in batch:
                stream.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n")


def bulk_index(client: OpenSearchClient, index: str, batches: list[list[dict]], size: int) -> int:
    lines: list[bytes] = []
    pending = 0
    sent = 0
    for batch in batches:
        for document in batch:
            lines.append(json.dumps({"index": {"_index": index, "_id": document["project_id"]}}).encode("utf-8"))
            lines.append(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            pending += 1
            if pending >= size:
                sent += client.bulk_lines(lines, pending)
                lines, pending = [], 0
    if pending:
        sent += client.bulk_lines(lines, pending)
    return sent


def drop_old_generations(client: OpenSearchClient, keep: set[str], keep_previous: int) -> list[str]:
    response = client.request("GET", f"/_cat/indices/{hyoka_projects.INDEX_PREFIX}*", query={"format": "json", "h": "index"})
    names = sorted(str(row.get("index") or "") for row in response if isinstance(row, dict)) if isinstance(response, list) else []
    names = [name for name in names if name.startswith(hyoka_projects.INDEX_PREFIX)]
    older = [name for name in names if name not in keep]
    # 世代名は作った日時なので、名前の順が古い順。新しい方から keep_previous 個を残す。
    doomed = older[: max(0, len(older) - max(0, keep_previous))]
    for name in doomed:
        client.request("DELETE", f"/{quote(name)}")
    return doomed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not (args.display_dir / "records").is_dir():
        print(f"[ERROR] 控えの records が無い: {args.display_dir}", file=sys.stderr)
        return 2
    municipalities = hyoka_projects.read_municipalities(args.municipality_master)
    client = None
    if args.out is not None and args.manifest_root:
        metadata = hyoka_projects.metadata_from_manifests(args.manifest_root)
    else:
        client = OpenSearchClient(args.opensearch_url, user=args.opensearch_user, password=args.opensearch_password,
                                  insecure_dev=bool(args.insecure_dev))
        metadata = metadata_from_index(client, args.doc_alias)
    batches = list(hyoka_projects.build_documents(args.display_dir, metadata, municipalities, set(args.slug) or None))
    summary = summarize(batches)
    if args.out is not None:
        write_ndjson(args.out, batches)
        print("[HYOKA-PROJECTS] " + json.dumps({"out": str(args.out), **summary}, ensure_ascii=False), flush=True)
        return 0
    if summary["documents"] == 0:
        print("[ERROR] 事業が 1 件も無いので索引を作らない", file=sys.stderr)
        return 1

    assert client is not None
    build_id = args.build_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    index = hyoka_projects.INDEX_PREFIX + build_id
    client.request("PUT", f"/{quote(index)}", body=hyoka_projects.index_body(load_index_settings(refresh_interval="-1")))
    sent = bulk_index(client, index, batches, max(1, args.bulk_size))
    client.request("POST", f"/{quote(index)}/_refresh")
    client.request("PUT", f"/{quote(index)}/_settings", body={"index": {"refresh_interval": None}})
    indexed = count(client, index)
    if indexed != sent:
        print(f"[ERROR] 入れた数 {sent} と索引の数 {indexed} が合わない。alias は切り替えない: {index}", file=sys.stderr)
        return 1
    current = alias_indices(client, args.alias)
    previous_count = count(client, args.alias) if current else 0
    if previous_count and indexed * 2 < previous_count and not args.allow_shrink:
        print(f"[ERROR] 件数が {previous_count} から {indexed} に減る。alias は切り替えない（--allow-shrink で切り替える）: {index}",
              file=sys.stderr)
        return 1
    actions = [{"remove": {"index": name, "alias": args.alias}} for name in current]
    actions.append({"add": {"index": index, "alias": args.alias}})
    client.request("POST", "/_aliases", body={"actions": actions})
    dropped = drop_old_generations(client, {index}, args.keep_previous)
    print("[HYOKA-PROJECTS] " + json.dumps({"index": index, "alias": args.alias, "previous": current, "previous_count": previous_count,
                                            "dropped": dropped, **summary}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
