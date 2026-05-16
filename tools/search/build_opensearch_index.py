#!/usr/bin/env python3
"""Build Miyabe OpenSearch indexes from scraper-produced files."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import ssl
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parents[1] / "gijiroku"))
sys.path.append(str(Path(__file__).resolve().parents[1] / "reiki"))
sys.path.append(str(Path(__file__).resolve().parents[2] / "lib" / "python"))

import gijiroku_targets  # type: ignore
import reiki_targets  # type: ignore
import build_locks  # type: ignore
from opensearch_mappings import build_index_body
from scraped_source_records import (  # type: ignore
    build_alias_map,
    build_minutes_record,
    build_reiki_record,
    choose_minutes_source_files,
    collect_reiki_preferred_files,
    load_reiki_manifest_index,
    parse_minutes_source_meta,
    reiki_sortable_prefixes,
)

try:
    import japanese_search_tokenizer  # type: ignore
except Exception:  # pragma: no cover - optional fallback for minimal machines
    japanese_search_tokenizer = None

try:
    import batch_status  # type: ignore
except Exception:  # pragma: no cover - progress UI is best-effort
    batch_status = None


PREFECTURE_NAMES = {
    "01": "北海道",
    "02": "青森県",
    "03": "岩手県",
    "04": "宮城県",
    "05": "秋田県",
    "06": "山形県",
    "07": "福島県",
    "08": "茨城県",
    "09": "栃木県",
    "10": "群馬県",
    "11": "埼玉県",
    "12": "千葉県",
    "13": "東京都",
    "14": "神奈川県",
    "15": "新潟県",
    "16": "富山県",
    "17": "石川県",
    "18": "福井県",
    "19": "山梨県",
    "20": "長野県",
    "21": "岐阜県",
    "22": "静岡県",
    "23": "愛知県",
    "24": "三重県",
    "25": "滋賀県",
    "26": "京都府",
    "27": "大阪府",
    "28": "兵庫県",
    "29": "奈良県",
    "30": "和歌山県",
    "31": "鳥取県",
    "32": "島根県",
    "33": "岡山県",
    "34": "広島県",
    "35": "山口県",
    "36": "徳島県",
    "37": "香川県",
    "38": "愛媛県",
    "39": "高知県",
    "40": "福岡県",
    "41": "佐賀県",
    "42": "長崎県",
    "43": "熊本県",
    "44": "大分県",
    "45": "宮崎県",
    "46": "鹿児島県",
    "47": "沖縄県",
}


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$")


class OpenSearchRequestError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"OpenSearch {method} {path} failed: HTTP {status}: {body[:800]}")
        self.method = method
        self.path = path
        self.status = status
        self.body = body


class OpenSearchClient:
    def __init__(
        self,
        base_url: str,
        *,
        user: str = "",
        password: str = "",
        insecure_dev: bool = False,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.user = user
        self.password = password
        self.insecure_dev = insecure_dev
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        ndjson: str | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        path = "/" + path.lstrip("/")
        url = urljoin(self.base_url, path.lstrip("/"))
        if query:
            url += "?" + urlencode(query)

        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if ndjson is not None:
            data = ndjson.encode("utf-8")
            headers["Content-Type"] = "application/x-ndjson"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self.user or self.password:
            token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        context = None
        if self.insecure_dev and url.lower().startswith("https://"):
            context = ssl._create_unverified_context()

        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout, context=context) as response:
                raw = response.read().decode("utf-8", errors="replace")
                if raw == "":
                    return {}
                return json.loads(raw)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise OpenSearchRequestError(method.upper(), path, exc.code, raw) from exc
        except URLError as exc:
            raise RuntimeError(f"OpenSearch is unreachable: {exc}") from exc

    def bulk(self, actions: list[dict[str, Any]]) -> int:
        if not actions:
            return 0
        lines: list[str] = []
        for action in actions:
            meta = action["meta"]
            source = action["source"]
            lines.append(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
            lines.append(json.dumps(source, ensure_ascii=False, separators=(",", ":")))
        payload = "\n".join(lines) + "\n"
        response = self.request("POST", "/_bulk", ndjson=payload)
        if bool(response.get("errors")):
            errors = []
            for item in response.get("items", []):
                if not isinstance(item, dict):
                    continue
                index_result = item.get("index") or {}
                if isinstance(index_result, dict) and "error" in index_result:
                    errors.append(index_result["error"])
                if len(errors) >= 3:
                    break
            raise RuntimeError(f"OpenSearch bulk request had item errors: {errors!r}")
        return len(actions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or incrementally update OpenSearch indexes from scraper-produced source files."
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "rebuild", "update"],
        default="auto",
        help=(
            "auto は --slug 指定時だけ増分更新し、それ以外は versioned rebuild します。"
            " update は current alias に slug 単位で delete+bulk します。"
        ),
    )
    parser.add_argument("--doc-type", choices=["all", "minutes", "reiki"], default="all")
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="増分更新または部分 rebuild 対象の自治体 slug。カンマ区切り・複数指定可。",
    )
    parser.add_argument("--build-id", default="", help="Index build id. Defaults to a UTC timestamp.")
    parser.add_argument("--opensearch-url", default=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"))
    parser.add_argument("--opensearch-user", default=os.environ.get("OPENSEARCH_USER", ""))
    parser.add_argument("--opensearch-password", default=os.environ.get("OPENSEARCH_PASSWORD", ""))
    parser.add_argument(
        "--insecure-dev",
        action="store_true",
        default=os.environ.get("OPENSEARCH_INSECURE_DEV", "").lower() in {"1", "true", "yes", "on"},
        help="Disable TLS verification for local HTTPS OpenSearch endpoints.",
    )
    parser.add_argument("--documents-alias", default=os.environ.get("MIYABE_SEARCH_ALIAS", "miyabe-documents-current"))
    parser.add_argument("--minutes-alias", default=os.environ.get("MIYABE_MINUTES_ALIAS", "miyabe-minutes-current"))
    parser.add_argument("--reiki-alias", default=os.environ.get("MIYABE_REIKI_ALIAS", "miyabe-reiki-current"))
    parser.add_argument("--shards", type=int, default=int(os.environ.get("MIYABE_OPENSEARCH_SHARDS", "1")))
    parser.add_argument("--replicas", type=int, default=int(os.environ.get("MIYABE_OPENSEARCH_REPLICAS", "0")))
    parser.add_argument("--bulk-size", type=int, default=int(os.environ.get("MIYABE_OPENSEARCH_BULK_SIZE", "10")))
    parser.add_argument("--limit", type=int, default=0, help="Development limit per document type.")
    parser.add_argument("--no-switch-alias", action="store_true")
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_build_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def parse_slug_filter(values: list[str]) -> set[str]:
    slugs: set[str] = set()
    for value in values:
        for item in str(value or "").split(","):
            slug = item.strip()
            if slug:
                slugs.add(slug)
    return slugs


def normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if DATE_RE.match(text) else None


def normalize_datetime(value: Any) -> str | None:
    text = str(value or "").strip()
    if text == "":
        return None
    return text if DATETIME_RE.match(text) else None


def terms_text(value: str) -> str:
    if value == "":
        return ""
    if japanese_search_tokenizer is not None:
        try:
            return str(japanese_search_tokenizer.document_terms_text(value)).strip()
        except Exception:
            pass
    parts = re.split(r"[\s\u3000]+", value)
    return " ".join(part for part in parts if part)


def pref_code_from_code(code: str) -> str:
    code = str(code or "").strip()
    return code[:2] if re.match(r"^\d{2}", code) else ""


def target_metadata(target: dict[str, Any]) -> dict[str, str]:
    code = str(target.get("code") or "").strip()
    pref_code = pref_code_from_code(code)
    return {
        "slug": str(target.get("slug") or "").strip(),
        "municipality_code": code,
        "pref_code": pref_code,
        "pref_name": PREFECTURE_NAMES.get(pref_code, ""),
        "municipality_name": str(target.get("name") or "").strip(),
    }


def stable_local_id(*parts: str) -> str:
    material = "\0".join(str(part) for part in parts)
    return hashlib.sha1(material.encode("utf-8", errors="replace")).hexdigest()


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def first_date(value: Any) -> str | None:
    text = clean_text(value)
    if len(text) >= 10:
        return normalize_date(text[:10])
    return None


def preferred_reiki_sidecar(files: dict[str, Path], key: str) -> Path | None:
    return files.get(key) or files.get(Path(key).name)


def iter_minutes_documents(limit: int = 0, slugs: set[str] | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    indexed_at = utc_now_iso()
    emitted = 0
    slug_filter = slugs or set()
    for target in gijiroku_targets.iter_gijiroku_targets():
        if slug_filter and str(target.get("slug") or "").strip() not in slug_filter:
            continue
        downloads_dir = Path(target["downloads_dir"])
        if not downloads_dir.is_dir():
            continue
        meta = target_metadata(target)
        assembly_name = str(target.get("assembly_name") or (meta["municipality_name"] + "議会")).strip()
        source_system = str(target.get("system_family") or target.get("system_type") or "").strip()
        try:
            source_files = choose_minutes_source_files(downloads_dir)
            meta_map = parse_minutes_source_meta(Path(target["index_json_path"]))
        except Exception as exc:
            print(f"[WARN] failed to enumerate minutes files dir={downloads_dir}: {exc}", file=sys.stderr)
            continue

        for file_path in source_files:
            try:
                record = build_minutes_record(file_path, downloads_dir, meta_map, indexed_at)
            except Exception as exc:
                print(f"[WARN] failed to parse minutes file={file_path}: {exc}", file=sys.stderr)
                continue
            if record is None or record.doc_type != "minutes":
                continue

            local_id = stable_local_id(meta["slug"], record.rel_path)
            title = clean_text(record.title)
            meeting_name = clean_text(record.meeting_name)
            body = str(record.content or "")
            title_terms = " ".join(part for part in [record.title_terms, record.meeting_name_terms] if clean_text(part))
            source_url = clean_text(record.source_url)
            held_on = normalize_date(record.held_on)
            document = {
                **meta,
                "doc_type": "minutes",
                "title": title,
                "title_terms": title_terms or terms_text(" ".join([title, meeting_name])),
                "body": body,
                "body_terms": clean_text(record.content_terms) or terms_text(body),
                "body_length": len(body),
                "source_url": source_url,
                "detail_url": source_url,
                "source_file": record.rel_path,
                "source_system": source_system,
                "indexed_at": indexed_at,
                "updated_at": normalize_datetime(record.indexed_at) or indexed_at,
                "sort_date": held_on,
                "assembly_name": assembly_name,
                "meeting_name": meeting_name,
                "year_label": clean_text(record.year_label),
                "held_on": held_on,
                "speaker": "",
                "speaker_role": "",
                "local_id": local_id,
            }
            yield f"minutes:{meta['slug']}:{local_id}", compact_document(document)
            emitted += 1
            if limit > 0 and emitted >= limit:
                return


def iter_reiki_documents(limit: int = 0, slugs: set[str] | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    indexed_at = utc_now_iso()
    emitted = 0
    slug_filter = slugs or set()
    for target in reiki_targets.iter_reiki_targets():
        if slug_filter and str(target.get("slug") or "").strip() not in slug_filter:
            continue
        clean_html_dir = Path(target["html_dir"])
        source_html_dir = Path(target["source_dir"])
        html_root = clean_html_dir if clean_html_dir.is_dir() else source_html_dir
        has_local_detail = clean_html_dir.is_dir()
        if not html_root.is_dir():
            continue
        meta = target_metadata(target)
        source_system = str(target.get("system_type") or "").strip()
        try:
            html_files = collect_reiki_preferred_files(html_root, {".html", ".htm"})
            markdown_files = build_alias_map(
                collect_reiki_preferred_files(Path(target["markdown_dir"]), {".md"})
            )
            classification_files = build_alias_map(
                collect_reiki_preferred_files(Path(target["classification_dir"]), {".json"})
            )
            manifest_index = load_reiki_manifest_index(Path(target["work_root"]) / "source_manifest.json.gz")
            prefixes = reiki_sortable_prefixes(target)
        except Exception as exc:
            print(f"[WARN] failed to enumerate reiki files dir={html_root}: {exc}", file=sys.stderr)
            continue

        for key, html_path in sorted(html_files.items()):
            try:
                record = build_reiki_record(
                    key,
                    html_path,
                    preferred_reiki_sidecar(markdown_files, key),
                    preferred_reiki_sidecar(classification_files, key),
                    manifest_index.get(key) or manifest_index.get(Path(key).name),
                    prefixes,
                )
            except Exception as exc:
                print(f"[WARN] failed to parse reiki file={html_path}: {exc}", file=sys.stderr)
                continue
            if not isinstance(record, dict):
                continue

            filename = clean_text(record.get("filename")) or key
            local_id = stable_local_id(meta["slug"], filename)
            title = clean_text(record.get("title")) or Path(filename).name
            body_parts = [
                clean_text(record.get("document_type")),
                clean_text(record.get("responsible_department")),
                clean_text(record.get("combined_stance")),
                clean_text(record.get("combined_reason")),
                clean_text(record.get("reason")),
                clean_text(record.get("taxonomy_path")),
                str(record.get("content_text") or ""),
            ]
            body = "\n".join(part for part in body_parts if part)
            title_terms = " ".join(
                part
                for part in [
                    clean_text(record.get("title_terms")),
                    clean_text(record.get("reading_terms")),
                ]
                if part
            ) or terms_text(title)
            body_terms = " ".join(
                part
                for part in [
                    clean_text(record.get("content_terms")),
                    clean_text(record.get("department_terms")),
                    clean_text(record.get("combined_reason_terms")),
                    clean_text(record.get("reason_terms")),
                    clean_text(record.get("secondary_terms")),
                    clean_text(record.get("lens_terms")),
                    clean_text(record.get("taxonomy_terms")),
                ]
                if part
            ) or terms_text(body)
            promulgated_on = normalize_date(record.get("enactment_date"))
            updated_at = normalize_datetime(record.get("updated_at")) or indexed_at
            detail_file = filename if filename.lower().endswith((".html", ".htm")) else filename + ".html"
            source_url = clean_text(record.get("source_url"))
            detail_url = (
                "/reiki/?" + urlencode({"slug": meta["slug"], "file": detail_file})
                if has_local_detail
                else source_url
            )
            document = {
                **meta,
                "doc_type": "reiki",
                "title": title,
                "title_terms": title_terms,
                "body": body,
                "body_terms": body_terms,
                "body_length": len(body),
                "source_url": source_url,
                "detail_url": detail_url,
                "source_file": clean_text(record.get("source_file")) or detail_file,
                "source_system": source_system,
                "indexed_at": indexed_at,
                "updated_at": updated_at,
                "sort_date": promulgated_on or first_date(updated_at),
                "filename": filename,
                "ordinance_no": clean_text(record.get("number") or record.get("ordinance_no")),
                "category": clean_text(record.get("primary_class")) or clean_text(record.get("document_type")),
                "promulgated_on": promulgated_on,
                "enforced_on": None,
                "amended_on": first_date(updated_at),
                "local_id": local_id,
            }
            yield f"reiki:{meta['slug']}:{local_id}", compact_document(document)
            emitted += 1
            if limit > 0 and emitted >= limit:
                return


def limited_total_count(total: int, limit: int) -> int:
    return min(total, max(0, int(limit))) if limit > 0 else total


def count_minutes_documents(limit: int = 0, slugs: set[str] | None = None) -> int:
    total = 0
    slug_filter = slugs or set()
    for target in gijiroku_targets.iter_gijiroku_targets():
        if slug_filter and str(target.get("slug") or "").strip() not in slug_filter:
            continue
        downloads_dir = Path(target["downloads_dir"])
        if not downloads_dir.is_dir():
            continue
        try:
            total += len(choose_minutes_source_files(downloads_dir))
        except Exception as exc:
            print(f"[WARN] failed to count minutes files dir={downloads_dir}: {exc}", file=sys.stderr)
            continue
        if limit > 0 and total >= limit:
            return limit
    return limited_total_count(total, limit)


def count_reiki_documents(limit: int = 0, slugs: set[str] | None = None) -> int:
    total = 0
    slug_filter = slugs or set()
    for target in reiki_targets.iter_reiki_targets():
        if slug_filter and str(target.get("slug") or "").strip() not in slug_filter:
            continue
        clean_html_dir = Path(target["html_dir"])
        source_html_dir = Path(target["source_dir"])
        html_root = clean_html_dir if clean_html_dir.is_dir() else source_html_dir
        if not html_root.is_dir():
            continue
        try:
            total += len(collect_reiki_preferred_files(html_root, {".html", ".htm"}))
        except Exception as exc:
            print(f"[WARN] failed to count reiki files dir={html_root}: {exc}", file=sys.stderr)
            continue
        if limit > 0 and total >= limit:
            return limit
    return limited_total_count(total, limit)


def count_rebuild_documents(doc_type: str, limit: int = 0, slugs: set[str] | None = None) -> int:
    total = 0
    if doc_type in {"all", "minutes"}:
        total += count_minutes_documents(limit=limit, slugs=slugs)
    if doc_type in {"all", "reiki"}:
        total += count_reiki_documents(limit=limit, slugs=slugs)
    return total


def compact_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if value is not None and not (isinstance(value, str) and value == "")
    }


def create_versioned_index(
    client: OpenSearchClient,
    index_name: str,
    *,
    shards: int,
    replicas: int,
) -> None:
    body = build_index_body(shards=shards, replicas=replicas, refresh_interval="-1")
    client.request("PUT", f"/{quote(index_name)}", body=body)


def update_index_after_bulk(client: OpenSearchClient, index_name: str, *, replicas: int) -> None:
    client.request(
        "PUT",
        f"/{quote(index_name)}/_settings",
        body={
            "index": {
                "refresh_interval": "1s",
                "number_of_replicas": max(0, int(replicas)),
            }
        },
    )
    client.request("POST", f"/{quote(index_name)}/_refresh")


def index_documents(
    client: OpenSearchClient,
    index_name: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    *,
    bulk_size: int,
    progress_callback: Callable[[int, dict[str, Any]], None] | None = None,
    slug_complete_callback: Callable[[str, dict[str, Any], int], None] | None = None,
) -> int:
    actions: list[dict[str, Any]] = []
    total = 0
    current_slug = ""
    current_slug_last_source: dict[str, Any] = {}

    def flush_actions() -> None:
        nonlocal actions, total
        if not actions:
            return
        last_source = actions[-1]["source"]
        total += client.bulk(actions)
        print(f"[BULK] index={index_name} total={total}", flush=True)
        if progress_callback is not None:
            progress_callback(total, last_source if isinstance(last_source, dict) else {})
        actions = []

    for doc_id, source in documents:
        slug = str(source.get("slug") or "").strip()
        if current_slug and slug != "" and slug != current_slug:
            flush_actions()
            if slug_complete_callback is not None:
                slug_complete_callback(current_slug, current_slug_last_source, total)
        if slug != "":
            current_slug = slug
            current_slug_last_source = source
        actions.append(
            {
                "meta": {"index": {"_index": index_name, "_id": doc_id}},
                "source": source,
            }
        )
        if len(actions) >= bulk_size:
            flush_actions()
    flush_actions()
    if current_slug and slug_complete_callback is not None:
        slug_complete_callback(current_slug, current_slug_last_source, total)
    return total


def indices_for_alias(client: OpenSearchClient, alias: str) -> list[str]:
    try:
        response = client.request("GET", f"/_alias/{quote(alias)}")
    except OpenSearchRequestError as exc:
        if exc.status == 404:
            return []
        raise
    if not isinstance(response, dict):
        return []
    return sorted(response.keys())


def single_index_for_alias(client: OpenSearchClient, alias: str) -> str | None:
    indices = indices_for_alias(client, alias)
    if len(indices) > 1:
        raise RuntimeError(f"Alias {alias} points to multiple indexes; cannot use it as an update target.")
    return indices[0] if indices else None


def delete_documents_for_slugs(
    client: OpenSearchClient,
    *,
    index_or_alias: str,
    doc_type: str,
    slugs: set[str],
) -> int:
    if not slugs:
        raise ValueError("Incremental update requires at least one slug.")
    response = client.request(
        "POST",
        f"/{quote(index_or_alias)}/_delete_by_query",
        query={"conflicts": "proceed", "refresh": "false"},
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"doc_type": doc_type}},
                        {"terms": {"slug": sorted(slugs)}},
                    ]
                }
            }
        },
    )
    deleted = int(response.get("deleted") or 0) if isinstance(response, dict) else 0
    print(f"[DELETE] target={index_or_alias} doc_type={doc_type} slugs={len(slugs)} deleted={deleted}", flush=True)
    return deleted


def refresh_search_target(client: OpenSearchClient, index_or_alias: str) -> None:
    client.request("POST", f"/{quote(index_or_alias)}/_refresh")


def switch_aliases(
    client: OpenSearchClient,
    *,
    minutes_index: str | None,
    reiki_index: str | None,
    minutes_alias: str,
    reiki_alias: str,
    documents_alias: str,
) -> None:
    target_minutes = [minutes_index] if minutes_index else indices_for_alias(client, minutes_alias)
    target_reiki = [reiki_index] if reiki_index else indices_for_alias(client, reiki_alias)

    actions: list[dict[str, Any]] = []
    for alias in [minutes_alias, reiki_alias, documents_alias]:
        for index in indices_for_alias(client, alias):
            actions.append({"remove": {"index": index, "alias": alias}})
    for index in target_minutes:
        if index:
            actions.append({"add": {"index": index, "alias": minutes_alias}})
    for index in target_reiki:
        if index:
            actions.append({"add": {"index": index, "alias": reiki_alias}})
    for index in target_minutes + target_reiki:
        if index:
            actions.append({"add": {"index": index, "alias": documents_alias}})
    if actions:
        client.request("POST", "/_aliases", body={"actions": actions})


def alias_filter_for_completed_slugs(slugs: set[str]) -> dict[str, Any]:
    return {"terms": {"slug": sorted(slugs)}}


def alias_filter_excluding_completed_slugs(slugs: set[str]) -> dict[str, Any]:
    return {"bool": {"must_not": [{"terms": {"slug": sorted(slugs)}}]}}


def add_alias_action(index: str, alias: str, *, filter_body: dict[str, Any] | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"index": index, "alias": alias}
    if filter_body is not None:
        action["filter"] = filter_body
    return {"add": action}


def publish_partial_aliases(
    client: OpenSearchClient,
    *,
    minutes_index: str | None,
    reiki_index: str | None,
    initial_minutes_indices: list[str],
    initial_reiki_indices: list[str],
    completed_minutes_slugs: set[str],
    completed_reiki_slugs: set[str],
    minutes_alias: str,
    reiki_alias: str,
    documents_alias: str,
) -> None:
    actions: list[dict[str, Any]] = []
    for alias in [minutes_alias, reiki_alias, documents_alias]:
        for index in indices_for_alias(client, alias):
            actions.append({"remove": {"index": index, "alias": alias}})

    if completed_minutes_slugs and minutes_index:
        actions.append(
            add_alias_action(minutes_index, minutes_alias, filter_body=alias_filter_for_completed_slugs(completed_minutes_slugs))
        )
    for index in initial_minutes_indices:
        actions.append(
            add_alias_action(
                index,
                minutes_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_minutes_slugs)
                if completed_minutes_slugs
                else None,
            )
        )

    if completed_reiki_slugs and reiki_index:
        actions.append(add_alias_action(reiki_index, reiki_alias, filter_body=alias_filter_for_completed_slugs(completed_reiki_slugs)))
    for index in initial_reiki_indices:
        actions.append(
            add_alias_action(
                index,
                reiki_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_reiki_slugs)
                if completed_reiki_slugs
                else None,
            )
        )

    if completed_minutes_slugs and minutes_index:
        actions.append(
            add_alias_action(
                minutes_index,
                documents_alias,
                filter_body=alias_filter_for_completed_slugs(completed_minutes_slugs),
            )
        )
    for index in initial_minutes_indices:
        actions.append(
            add_alias_action(
                index,
                documents_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_minutes_slugs)
                if completed_minutes_slugs
                else None,
            )
        )

    if completed_reiki_slugs and reiki_index:
        actions.append(add_alias_action(reiki_index, documents_alias, filter_body=alias_filter_for_completed_slugs(completed_reiki_slugs)))
    for index in initial_reiki_indices:
        actions.append(
            add_alias_action(
                index,
                documents_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_reiki_slugs)
                if completed_reiki_slugs
                else None,
            )
        )

    if actions:
        client.request("POST", "/_aliases", body={"actions": actions})


def publish_completed_slug(
    client: OpenSearchClient,
    *,
    doc_type: str,
    index_name: str,
    minutes_index: str | None,
    reiki_index: str | None,
    slug: str,
    initial_minutes_indices: list[str],
    initial_reiki_indices: list[str],
    completed_minutes_slugs: set[str],
    completed_reiki_slugs: set[str],
    minutes_alias: str,
    reiki_alias: str,
    documents_alias: str,
) -> None:
    slug = slug.strip()
    if slug == "":
        return
    if doc_type == "minutes":
        completed_minutes_slugs.add(slug)
    elif doc_type == "reiki":
        completed_reiki_slugs.add(slug)
    else:
        return

    refresh_search_target(client, index_name)
    publish_partial_aliases(
        client,
        minutes_index=minutes_index,
        reiki_index=reiki_index,
        initial_minutes_indices=initial_minutes_indices,
        initial_reiki_indices=initial_reiki_indices,
        completed_minutes_slugs=completed_minutes_slugs,
        completed_reiki_slugs=completed_reiki_slugs,
        minutes_alias=minutes_alias,
        reiki_alias=reiki_alias,
        documents_alias=documents_alias,
    )
    print(f"[PUBLISH] doc_type={doc_type} slug={slug} index={index_name}", flush=True)


def build_one(
    client: OpenSearchClient,
    *,
    index_name: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    shards: int,
    replicas: int,
    bulk_size: int,
    progress_callback: Callable[[int, dict[str, Any]], None] | None = None,
    slug_complete_callback: Callable[[str, dict[str, Any], int], None] | None = None,
) -> int:
    print(f"[CREATE] {index_name}", flush=True)
    create_versioned_index(client, index_name, shards=shards, replicas=replicas)
    count = index_documents(
        client,
        index_name,
        documents,
        bulk_size=bulk_size,
        progress_callback=progress_callback,
        slug_complete_callback=slug_complete_callback,
    )
    update_index_after_bulk(client, index_name, replicas=replicas)
    print(f"[DONE] index={index_name} count={count}", flush=True)
    return count


def search_rebuild_status_start(*, build_id: str, doc_type: str, total_count: int) -> dict[str, Any] | None:
    if batch_status is None:
        return None
    total_count = max(0, int(total_count))
    try:
        total_cache = batch_status.status_root() / "search_rebuild_total_count.json"
        total_cache.write_text(json.dumps({"total_count": total_count}, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] search rebuild total count cache write failed: {exc}", file=sys.stderr, flush=True)
    state = {
        "task": "search_rebuild",
        "run_id": build_id,
        "running": True,
        "started_at": batch_status.now_text(),
        "finished_at": "",
        "heartbeat_at": batch_status.now_text(),
        "updated_at": batch_status.now_text(),
        "running_label": "検索インデックス再構築",
        "doc_type": doc_type,
        "current_stage": "",
        "current_index": "",
        "current_slug": "",
        "current_municipality_code": "",
        "current_municipality_name": "",
        "current_document_title": "",
        "published_slug_count": 0,
        "published_current_slug": "",
        "published_current_municipality_name": "",
        "processed_count": 0,
        "total_count": total_count,
        "completed_count": 0,
        "active_count": 1,
        "pending_count": 0,
        "worker_capacity": 1,
        "worker_active_count": 1,
        "worker_idle_count": 0,
        "index_capacity": 1,
        "index_active_count": 1,
        "index_idle_count": 0,
        "index_queue_count": 0,
        "items": {},
    }
    batch_status.write_state("search_rebuild", state)
    batch_status.invalidate_runtime_caches(include_homepage_payload=True)
    return state


def search_rebuild_status_progress(
    state: dict[str, Any] | None,
    *,
    stage: str,
    index_name: str,
    processed_count: int,
    source: dict[str, Any],
) -> None:
    if batch_status is None or state is None:
        return
    next_processed = max(0, int(processed_count))
    previous_processed = max(0, int(state.get("processed_count") or 0))
    state["current_stage"] = stage
    state["current_index"] = index_name
    state["current_slug"] = str(source.get("slug") or "").strip()
    state["current_municipality_code"] = str(source.get("municipality_code") or "").strip()
    state["current_municipality_name"] = str(source.get("municipality_name") or "").strip()
    state["current_document_title"] = str(source.get("title") or "").strip()
    state["processed_count"] = next_processed
    state["completed_count"] = next_processed
    state["updated_at"] = batch_status.now_text()
    batch_status.write_state("search_rebuild", state)
    if next_processed // 1000 != previous_processed // 1000:
        batch_status.invalidate_runtime_caches(include_homepage_payload=True)


def search_rebuild_status_finish(state: dict[str, Any] | None, *, ok: bool, message: str = "") -> None:
    if batch_status is None or state is None:
        return
    state["running"] = False
    state["finished_at"] = batch_status.now_text()
    state["heartbeat_at"] = state["finished_at"]
    state["updated_at"] = state["finished_at"]
    state["active_count"] = 0
    state["worker_active_count"] = 0
    state["worker_idle_count"] = 1
    state["index_active_count"] = 0
    state["index_idle_count"] = 1
    state["status"] = "done" if ok else "failed"
    state["message"] = message
    batch_status.write_state("search_rebuild", state)
    batch_status.invalidate_runtime_caches(include_homepage_payload=True)


def search_rebuild_status_slug_published(
    state: dict[str, Any] | None,
    *,
    source: dict[str, Any],
    published_slug_count: int,
) -> None:
    if batch_status is None or state is None:
        return
    state["published_slug_count"] = max(0, int(published_slug_count))
    state["published_current_slug"] = str(source.get("slug") or "").strip()
    state["published_current_municipality_name"] = str(source.get("municipality_name") or "").strip()
    state["updated_at"] = batch_status.now_text()
    batch_status.write_state("search_rebuild", state)
    batch_status.invalidate_runtime_caches(include_homepage_payload=True)


def update_one(
    client: OpenSearchClient,
    *,
    doc_type: str,
    index_prefix: str,
    alias: str,
    documents_alias: str,
    minutes_alias: str,
    reiki_alias: str,
    build_id: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    slugs: set[str],
    shards: int,
    replicas: int,
    bulk_size: int,
    switch_alias: bool,
) -> int:
    if not slugs:
        raise ValueError("Incremental update requires --slug.")

    current_index = single_index_for_alias(client, alias)
    if current_index is None:
        lock_path = build_locks.acquire_build_lock(
            f"opensearch-{doc_type}-bootstrap",
            owner="build_opensearch_index",
            wait_seconds=900.0,
        )
        if lock_path is None:
            raise RuntimeError(f"Could not acquire OpenSearch bootstrap lock for {doc_type}.")
        try:
            current_index = single_index_for_alias(client, alias)
            if current_index is None:
                index_name = f"{index_prefix}-v{build_id}"
                print(f"[BOOTSTRAP] alias={alias} has no index; creating {index_name} from selected slugs", flush=True)
                count = build_one(
                    client,
                    index_name=index_name,
                    documents=documents,
                    shards=shards,
                    replicas=replicas,
                    bulk_size=bulk_size,
                )
                if switch_alias:
                    switch_aliases(
                        client,
                        minutes_index=index_name if doc_type == "minutes" else None,
                        reiki_index=index_name if doc_type == "reiki" else None,
                        minutes_alias=minutes_alias,
                        reiki_alias=reiki_alias,
                        documents_alias=documents_alias,
                    )
                    print(f"[ALIAS] {alias}={index_name} {documents_alias}=combined", flush=True)
                return count
        finally:
            build_locks.release_build_lock(lock_path)

    print(f"[UPDATE] alias={alias} index={current_index} slugs={','.join(sorted(slugs))}", flush=True)
    delete_documents_for_slugs(client, index_or_alias=alias, doc_type=doc_type, slugs=slugs)
    count = index_documents(client, alias, documents, bulk_size=bulk_size)
    refresh_search_target(client, alias)
    print(f"[DONE] alias={alias} doc_type={doc_type} count={count}", flush=True)
    return count


def main() -> int:
    args = parse_args()
    build_id = args.build_id.strip() or default_build_id()
    slugs = parse_slug_filter(args.slug)
    mode = args.mode
    if mode == "auto":
        mode = "update" if slugs else "rebuild"
    if mode == "update" and not slugs:
        print("[ERROR] --mode update requires --slug.", file=sys.stderr, flush=True)
        return 2

    client = OpenSearchClient(
        args.opensearch_url,
        user=args.opensearch_user,
        password=args.opensearch_password,
        insecure_dev=bool(args.insecure_dev),
    )
    bulk_size = max(1, args.bulk_size)

    if mode == "update":
        if args.doc_type in {"all", "minutes"}:
            update_one(
                client,
                doc_type="minutes",
                index_prefix="miyabe-minutes",
                alias=args.minutes_alias,
                documents_alias=args.documents_alias,
                minutes_alias=args.minutes_alias,
                reiki_alias=args.reiki_alias,
                build_id=build_id,
                documents=iter_minutes_documents(limit=args.limit, slugs=slugs),
                slugs=slugs,
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                switch_alias=not args.no_switch_alias,
            )
        if args.doc_type in {"all", "reiki"}:
            update_one(
                client,
                doc_type="reiki",
                index_prefix="miyabe-reiki",
                alias=args.reiki_alias,
                documents_alias=args.documents_alias,
                minutes_alias=args.minutes_alias,
                reiki_alias=args.reiki_alias,
                build_id=build_id,
                documents=iter_reiki_documents(limit=args.limit, slugs=slugs),
                slugs=slugs,
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                switch_alias=not args.no_switch_alias,
            )
        return 0

    built_minutes_index: str | None = None
    built_reiki_index: str | None = None
    initial_minutes_indices = indices_for_alias(client, args.minutes_alias)
    initial_reiki_indices = indices_for_alias(client, args.reiki_alias)
    completed_minutes_slugs: set[str] = set()
    completed_reiki_slugs: set[str] = set()
    total_document_count = count_rebuild_documents(args.doc_type, limit=args.limit, slugs=slugs)
    print(f"[COUNT] doc_type={args.doc_type} total={total_document_count}", flush=True)
    status_state = search_rebuild_status_start(
        build_id=build_id,
        doc_type=args.doc_type,
        total_count=total_document_count,
    )
    processed_offset = 0
    try:
        if args.doc_type in {"all", "minutes"}:
            built_minutes_index = f"miyabe-minutes-v{build_id}"
            minutes_count = build_one(
                client,
                index_name=built_minutes_index,
                documents=iter_minutes_documents(limit=args.limit, slugs=slugs),
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                progress_callback=lambda total, source: search_rebuild_status_progress(
                    status_state,
                    stage="minutes",
                    index_name=built_minutes_index or "",
                    processed_count=processed_offset + total,
                    source=source,
                ),
                slug_complete_callback=(
                    None
                    if args.no_switch_alias
                    else lambda slug, source, _total: (
                        publish_completed_slug(
                            client,
                            doc_type="minutes",
                            index_name=built_minutes_index or "",
                            minutes_index=built_minutes_index,
                            reiki_index=built_reiki_index,
                            slug=slug,
                            initial_minutes_indices=initial_minutes_indices,
                            initial_reiki_indices=initial_reiki_indices,
                            completed_minutes_slugs=completed_minutes_slugs,
                            completed_reiki_slugs=completed_reiki_slugs,
                            minutes_alias=args.minutes_alias,
                            reiki_alias=args.reiki_alias,
                            documents_alias=args.documents_alias,
                        ),
                        search_rebuild_status_slug_published(
                            status_state,
                            source=source,
                            published_slug_count=len(completed_minutes_slugs) + len(completed_reiki_slugs),
                        ),
                    )
                ),
            )
            processed_offset += minutes_count
        if args.doc_type in {"all", "reiki"}:
            built_reiki_index = f"miyabe-reiki-v{build_id}"
            reiki_count = build_one(
                client,
                index_name=built_reiki_index,
                documents=iter_reiki_documents(limit=args.limit, slugs=slugs),
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                progress_callback=lambda total, source: search_rebuild_status_progress(
                    status_state,
                    stage="reiki",
                    index_name=built_reiki_index or "",
                    processed_count=processed_offset + total,
                    source=source,
                ),
                slug_complete_callback=(
                    None
                    if args.no_switch_alias
                    else lambda slug, source, _total: (
                        publish_completed_slug(
                            client,
                            doc_type="reiki",
                            index_name=built_reiki_index or "",
                            minutes_index=built_minutes_index,
                            reiki_index=built_reiki_index,
                            slug=slug,
                            initial_minutes_indices=initial_minutes_indices,
                            initial_reiki_indices=initial_reiki_indices,
                            completed_minutes_slugs=completed_minutes_slugs,
                            completed_reiki_slugs=completed_reiki_slugs,
                            minutes_alias=args.minutes_alias,
                            reiki_alias=args.reiki_alias,
                            documents_alias=args.documents_alias,
                        ),
                        search_rebuild_status_slug_published(
                            status_state,
                            source=source,
                            published_slug_count=len(completed_minutes_slugs) + len(completed_reiki_slugs),
                        ),
                    )
                ),
            )
            processed_offset += reiki_count

        if not args.no_switch_alias:
            print("[ALIAS] atomic switch", flush=True)
            switch_aliases(
                client,
                minutes_index=built_minutes_index,
                reiki_index=built_reiki_index,
                minutes_alias=args.minutes_alias,
                reiki_alias=args.reiki_alias,
                documents_alias=args.documents_alias,
            )
            print(
                "[ALIAS] "
                f"{args.minutes_alias}={built_minutes_index or 'unchanged'} "
                f"{args.reiki_alias}={built_reiki_index or 'unchanged'} "
                f"{args.documents_alias}=combined",
                flush=True,
            )
    except Exception as exc:
        search_rebuild_status_finish(status_state, ok=False, message=str(exc))
        raise
    search_rebuild_status_finish(status_state, ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
