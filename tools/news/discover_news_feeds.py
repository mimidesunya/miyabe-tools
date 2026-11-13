#!/usr/bin/env python3
"""自治体のホームページから新着情報のフィードを探し、台帳を作る。

2 段に分かれる。

1. 探す（`--discover`）: `municipality_homepages.csv` のトップページを開き、
   head の `<link rel="alternate">`、本文の RSS らしいリンク（RSS の説明ページ
   なら 1 段だけたどる）、無ければよくある場所を試して、項目のあるフィードを
   探す。結果は自治体ごとに 1 行の JSONL（既定 `work/news/discovery.jsonl`）。
   途中で止めても、もう一度走らせれば済んだ自治体を飛ばす。
2. 台帳にする（`--write-registry`）: JSONL から
   `data/municipalities/news_feed_urls.tsv` を書く。1 年以上更新の無いフィードは
   `excluded`（理由 `stale`）。前の台帳で人が除外にした行（理由が `stale` 以外）
   は、その判断を残す。今回見つからなかった前の行も残す。

2026-11-13 の全国調査（`doc/news-feed-survey.md`）はこの 1 段目と同じやり方。

    python tools/news/discover_news_feeds.py --discover --limit 30
    python tools/news/discover_news_feeds.py --write-registry --ledger work/news/discovery.jsonl
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.document_crawl import USER_AGENT, read_limited  # noqa: E402
from tools.news.news_feed import FeedError, decode_feed, parse_feed  # noqa: E402

DATA_ROOT = WORKSPACE_ROOT / "data" / "municipalities"
HOMEPAGES = DATA_ROOT / "municipality_homepages.csv"
REGISTRY = DATA_ROOT / "news_feed_urls.tsv"
DEFAULT_LEDGER = WORKSPACE_ROOT / "work" / "news" / "discovery.jsonl"

REGISTRY_COLUMNS = ("jis_code", "url", "found_by", "feed_format", "crawl_status", "exclusion_reason", "exclusion_detail")
STALE_DAYS = 365
REQUEST_INTERVAL_SECONDS = 1.0
MAX_BYTES = 3_000_000

# フィードの置き場所としてよくあるもの（調査で見つかった型）。
COMMON_PATHS = (
    "/rss/rss.xml", "/rss.xml", "/feed/", "/news.rss", "/index.rss", "/atom.xml",
    "/rss/rss10.xml", "/docs/index.rss", "/cgi-bin/feed.php?siteNew=1",
    "/rss.php?mode=news&type=0", "/new/shinchaku.xml", "/shinchaku/shinchaku.xml",
    "/rss/10/list1.xml", "/rss/10/list3.xml", "/rss/10/list6.xml", "/news/index.rss",
    "/feed.xml", "/rss/news.xml",
)
LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
FEED_TYPE_RE = re.compile(r"application/(rss|atom|rdf)\+xml", re.I)
HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)", re.I)
ANCHOR_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
FEEDISH_HREF_RE = re.compile(r"(rss|atom|feed|\.rdf|\.xml)(\b|$|\?|/)", re.I)
INNER_FEED_HREF_RE = re.compile(r"\.(xml|rdf|rss)(\?|$)|feed\.php|rss\.php", re.I)
NEWS_TEXT_RE = re.compile(r"新着|お知らせ|新しい情報|最新情報|報道発表|記者発表|プレスリリース|トピックス")
TAG_RE = re.compile(r"<[^>]+>")


# ─────────────────────────────────────
# 探す
# ─────────────────────────────────────

class SiteClient:
    """1 自治体ぶんの取得。間隔を空けて 1 本ずつ当てる。"""

    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self.requests = 0
        self._last = 0.0

    def get(self, url: str) -> tuple[str, str, bytes]:
        wait = self._last + REQUEST_INTERVAL_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.requests += 1
        try:
            response = self.session.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"},
                                        timeout=25, stream=True)
            if response.status_code >= 400:
                response.close()
                raise requests.HTTPError(f"HTTP {response.status_code}")
            raw = read_limited(response, MAX_BYTES)
            return response.url, response.headers.get("Content-Type", ""), raw
        finally:
            self._last = time.monotonic()


def _dedup(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _href(value: str, base: str) -> str:
    return urljoin(base, value.replace("&amp;", "&").strip())


def _same_site(url: str, host: str) -> bool:
    other = (urlsplit(url).hostname or "").lower()
    return other == host or other.endswith("." + host.split(".", 1)[-1])


def discover_one(row: dict[str, str], session: requests.Session) -> dict:
    client = SiteClient(session)
    record: dict = {"jis_code": row["jis_code"], "homepage": row["url"]}
    try:
        final, content_type, raw = client.get(row["url"])
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {str(exc)[:120]}", requests=client.requests)
        return record
    page = decode_feed(raw, content_type)
    record["final_url"] = final
    host = (urlsplit(final).hostname or "").lower()

    head_feeds = _dedup([
        _href(match.group(1), final)
        for tag in LINK_TAG_RE.findall(page) if FEED_TYPE_RE.search(tag)
        for match in [HREF_RE.search(tag)] if match
    ])[:6]
    anchors: list[str] = []
    news_pages: list[dict[str, str]] = []
    for href, inner in ANCHOR_RE.findall(page):
        if href.strip().startswith(("javascript:", "mailto:", "#")):
            continue
        url = _href(href, final)
        text = TAG_RE.sub("", inner).strip()
        parts = urlsplit(url)
        if FEEDISH_HREF_RE.search(parts.path + "?" + parts.query) or re.search(r"\bRSS\b", text, re.I):
            anchors.append(url)
        elif NEWS_TEXT_RE.search(text) and len(text) <= 15 and not re.search(r"[0-9０-９]", text):
            if url.rstrip("/") != final.rstrip("/"):
                news_pages.append({"text": text[:30], "url": url})
    anchors = [url for url in _dedup(anchors) if url not in head_feeds and _same_site(url, host)][:6]
    record["head_feeds"] = head_feeds
    record["anchor_candidates"] = anchors
    record["news_pages"] = list({page["url"]: page for page in news_pages}.values())[:8]

    feeds: list[dict] = []

    def try_feed(url: str, found_by: str) -> dict | None:
        if any(feed["url"] == url for feed in feeds):
            return None
        try:
            feed_final, feed_type, feed_raw = client.get(url)
        except Exception as exc:
            return {"url": url, "source": found_by, "ok": False, "why": type(exc).__name__}
        try:
            parsed = parse_feed(feed_raw, content_type=feed_type, base_url=feed_final)
        except FeedError:
            # RSS の説明ページなら、その中のフィードへのリンクを 1 段だけ見る。
            if found_by == "anchor" and "html" in feed_type.lower():
                inner_page = decode_feed(feed_raw, feed_type)
                inner = [
                    _href(match.group(1), feed_final)
                    for tag in LINK_TAG_RE.findall(inner_page) if FEED_TYPE_RE.search(tag)
                    for match in [HREF_RE.search(tag)] if match
                ] + [
                    _href(href, feed_final) for href, _ in ANCHOR_RE.findall(inner_page)
                    if INNER_FEED_HREF_RE.search(href)
                ]
                if inner:
                    return {"url": url, "source": found_by, "ok": False, "why": "html", "inner": inner[0]}
            return {"url": url, "source": found_by, "ok": False, "why": "not_feed"}
        return {
            "url": feed_final,
            "source": found_by,
            "ok": True,
            "format": parsed.format,
            "title": parsed.title,
            "items": len(parsed.items),
            "newest": parsed.newest,
        }

    for url in head_feeds:
        result = try_feed(url, "head")
        if result:
            feeds.append(result)
    inner_urls: list[str] = []
    for url in anchors:
        result = try_feed(url, "anchor")
        if result:
            feeds.append(result)
            if not result["ok"] and result.get("inner"):
                inner_urls.append(result["inner"])
    for url in _dedup(inner_urls)[:3]:
        result = try_feed(url, "rss_page")
        if result:
            feeds.append(result)
    if not any(feed["ok"] and feed.get("items") for feed in feeds):
        parts = urlsplit(final)
        for path in COMMON_PATHS:
            result = try_feed(f"{parts.scheme}://{parts.netloc}{path}", "probe")
            if result and result["ok"]:
                feeds.append(result)
                if result.get("items"):
                    break
    record["feeds"] = feeds
    record["requests"] = client.requests
    return record


def discover(ledger: Path, *, codes: set[str] | None = None, limit: int = 0, workers: int = 16) -> int:
    with io.open(HOMEPAGES, encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("url")]
    if codes:
        rows = [row for row in rows if row["jis_code"] in codes]
    done: set[str] = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["jis_code"])
            except Exception:
                continue
    todo = [row for row in rows if row["jis_code"] not in done]
    if limit > 0:
        todo = todo[:limit]
    print(f"[DISCOVER] 残り {len(todo)} / {len(rows)}", flush=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    lock = threading.Lock()

    def safe(row: dict[str, str]) -> dict:
        try:
            return discover_one(row, session)
        except Exception as exc:
            return {"jis_code": row["jis_code"], "homepage": row["url"], "error": f"crash {type(exc).__name__}: {str(exc)[:120]}"}

    with ledger.open("a", encoding="utf-8", newline="\n") as out, ThreadPoolExecutor(max(1, workers)) as executor:
        for count, record in enumerate(executor.map(safe, todo), start=1):
            with lock:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
            if count % 100 == 0:
                print(f"[DISCOVER] {count} 済み", flush=True)
    return len(todo)


# ─────────────────────────────────────
# 台帳にする
# ─────────────────────────────────────

def read_registry(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def registry_rows(records: list[dict], *, as_of: date, previous: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """探した結果から台帳の行を作る。"""
    kept = {
        (row.get("jis_code", ""), row.get("url", "")): row
        for row in previous or []
        if row.get("jis_code") and row.get("url")
    }
    stale_before = (as_of - timedelta(days=STALE_DAYS)).isoformat()
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        code = str(record.get("jis_code", ""))
        for feed in record.get("feeds") or []:
            if not feed.get("ok") or not feed.get("items"):
                continue
            key = (code, str(feed["url"]))
            if key in rows:
                continue
            newest = str(feed.get("newest") or "")
            row = {
                "jis_code": code,
                "url": key[1],
                "found_by": str(feed.get("source", "")),
                "feed_format": str(feed.get("format", "")),
                "crawl_status": "enabled",
                "exclusion_reason": "",
                "exclusion_detail": "",
            }
            if newest and newest < stale_before:
                row.update(crawl_status="excluded", exclusion_reason="stale",
                           exclusion_detail=f"最新の項目 {newest}（{as_of.isoformat()} の確認）")
            before = kept.get(key)
            if before and before.get("crawl_status") != "enabled" and before.get("exclusion_reason") not in ("", "stale"):
                # 人が理由を書いて除外したものは、その判断を残す。
                for column in ("crawl_status", "exclusion_reason", "exclusion_detail"):
                    row[column] = before.get(column, "")
            rows[key] = row
    for key, before in kept.items():
        rows.setdefault(key, {column: before.get(column, "") for column in REGISTRY_COLUMNS})
    return sorted(rows.values(), key=lambda row: (row["jis_code"], row["url"]))


def write_registry(path: Path, rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REGISTRY_COLUMNS, delimiter="\t", lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="新着情報のフィードを探し、台帳を作る")
    parser.add_argument("--discover", action="store_true", help="ホームページからフィードを探す")
    parser.add_argument("--write-registry", action="store_true", help="探した結果から台帳を書く")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--as-of", default="", help="古いフィードを決める基準日（既定は今日）")
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if not args.discover and not args.write_registry:
        parser.error("--discover か --write-registry を付けてください")
    if args.discover:
        discover(args.ledger, codes=set(args.code) or None, limit=args.limit, workers=args.workers)
    if args.write_registry:
        records = [json.loads(line) for line in args.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        rows = registry_rows(records, as_of=as_of, previous=read_registry(args.registry))
        write_registry(args.registry, rows)
        enabled = sum(1 for row in rows if row["crawl_status"] == "enabled")
        print(f"[REGISTRY] {args.registry}: {len(rows)} 行（enabled {enabled}・"
              f"自治体 {len({row['jis_code'] for row in rows if row['crawl_status'] == 'enabled'})}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
