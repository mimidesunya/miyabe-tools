#!/usr/bin/env python3
"""自治体の新着情報のフィードを読み、項目をためる。

台帳 `data/municipalities/news_feed_urls.tsv` の `enabled` のフィードを読み、
新しい項目を自治体ごとに追記する。Celery beat から 3 時間おきに呼ぶ
（`run_news_fetch`。同じタスクで続けて `fetch_news_bodies.py` がリンク先の
本文を、`fetch_news_attachments.py` が添付の PDF を読む）。調べた結果と作り方は `doc/news-feed-survey.md`・`doc/news.md`。

    data/news/<slug>/
        items/<年>.jsonl   項目（初めて見た年ごと。1 行 1 項目）
        seen.json          見た項目の印（id → [最後の版, 題名と日付の印 ...]）
    work/news/
        feed_state.json    フィードごとの ETag・Last-Modified・失敗の回数
        last_run.json      直近の 1 回の結果
        runs.jsonl         1 回ごとの結果の記録
        latest/            トップページの「各地のお知らせ」（latest_news.py）

**自治体のサイトは古いお知らせを消す。** フィードに出ているのは中央値で
36 日分しかないので、ためたものが記録になる。項目は消さない。

- 項目はリンク先の URL で見分ける。同じ URL の題名か日付が変わったら
  （更新のたびに新着へ出し直すページがある）、版を上げてもう 1 行足す。
  読む側は id ごとに最後の行を使う。
- 1 つの自治体の新着と分野ごとのフィードに同じ項目が出るので、重複は
  自治体ごとに除く。

取得元への配慮:

- 同じホストへは 1 秒以上空け、同時に 1 本しか当てない。
- ETag・Last-Modified で条件付きにする（変わっていなければ 304 で終わる）。
- 続けて失敗するフィードは間隔を延ばす（1 日続いたら 1 日おき、5 日続いたら 7 日おき）。

    python tools/news/fetch_news_feeds.py --code 01100
    python tools/news/fetch_news_feeds.py --limit 20 --data-root /tmp/news --work-root /tmp/news-work
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.document_crawl import (  # noqa: E402
    JST,
    USER_AGENT,
    load_master,
    read_limited,
    slug_for,
    write_json,
)
from tools.news.news_feed import FeedError, FeedItem, parse_feed  # noqa: E402
from tools.news.latest_news import build_latest  # noqa: E402

REGISTRY = WORKSPACE_ROOT / "data" / "municipalities" / "news_feed_urls.tsv"
DEFAULT_DATA_ROOT = WORKSPACE_ROOT / "data" / "news"
DEFAULT_WORK_ROOT = WORKSPACE_ROOT / "work" / "news"

DEFAULT_WORKERS = 16
# 同じホストへの間隔（秒）。
HOST_INTERVAL_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0
MAX_FEED_BYTES = 5_000_000
# 続けて失敗したときの間隔。3 時間おきに 8 回（1 日）続いたら 1 日おき、
# 40 回（5 日）続いたら 7 日おき。
BACKOFF_STEPS = ((40, timedelta(days=7)), (8, timedelta(days=1)))
# 1 項目について覚えておく印（題名と日付）の数。
MAX_FINGERPRINTS = 8
ACCEPT ="application/rss+xml, application/atom+xml, application/rdf+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_jst() -> datetime:
    return datetime.now(JST)


def time_text(value: datetime) -> str:
    return value.astimezone(JST).strftime(TIME_FORMAT)


def parse_time_text(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), TIME_FORMAT).replace(tzinfo=JST)
    except ValueError:
        return None


# ─────────────────────────────────────
# 台帳
# ─────────────────────────────────────

@dataclass(frozen=True)
class FeedTarget:
    code: str
    slug: str
    name: str
    url: str


def load_feed_targets(registry: Path = REGISTRY, master: dict | None = None) -> dict[str, list[FeedTarget]]:
    """台帳の `enabled` のフィードを、自治体コードごとにまとめて返す。"""
    master = load_master() if master is None else master
    grouped: dict[str, list[FeedTarget]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    with io.open(registry, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            url = str(row.get("url", "")).strip()
            if not code or not url or str(row.get("crawl_status", "")).strip() != "enabled":
                continue
            if (code, url) in seen:
                continue
            seen.add((code, url))
            master_row = master.get(code, {})
            grouped[code].append(FeedTarget(
                code=code,
                slug=slug_for(code, master_row),
                name=str(master_row.get("full_name") or master_row.get("name") or code),
                url=url,
            ))
    return dict(grouped)


# ─────────────────────────────────────
# 保存
# ─────────────────────────────────────

class MunicipalityStore:
    """1 自治体ぶんの項目。1 つのスレッドだけが触る。"""

    def __init__(self, data_root: Path, code: str, slug: str) -> None:
        self.code = code
        self.slug = slug
        self.directory = data_root / slug
        self.seen_path = self.directory / "seen.json"
        self.seen: dict[str, list] = self._load_seen()
        self.pending: list[dict] = []

    def _load_seen(self) -> dict[str, list]:
        try:
            payload = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            # 壊れていたら脇へどけ、項目の記録から作り直す。
            self.seen_path.replace(self.seen_path.with_name(f"seen.json.corrupt-{int(time.time())}"))
            return self._rebuild_seen()
        return payload if isinstance(payload, dict) else {}

    def _rebuild_seen(self) -> dict[str, list]:
        seen: dict[str, list] = {}
        for path in sorted((self.directory / "items").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    known = seen.setdefault(str(record["id"]), [0])
                    known[0] = max(int(known[0]), int(record.get("revision", 0)))
                    if record["fingerprint"] not in known[1:]:
                        known.append(str(record["fingerprint"]))
                except Exception:
                    continue
        return seen

    def add(self, item: FeedItem, feed_url: str, seen_at: datetime) -> str:
        """項目を 1 つ見る。`new` / `revised` / `known` を返す。

        `seen` は id → [最後の版, 見た印 ...]。前に見たどの印とも違うときだけ版を上げる。
        同じ URL に別のお知らせを付けるフィードや、新着と分野のフィードで日付が
        違う項目があり、最後の印とだけ比べると毎回「更新」になって行が増え続ける。
        """
        item_id = item.item_id(feed_url)
        fingerprint = item.fingerprint()
        known = self.seen.get(item_id)
        if known and fingerprint in known[1:]:
            return "known"
        revision = int(known[0]) + 1 if known else 0
        record = {
            "id": item_id,
            "jis_code": self.code,
            "slug": self.slug,
            "url": item.url,
            "title": item.title,
            "published_at": item.published_at,
            "date": item.date,
            "date_raw": item.date_raw,
            "summary": item.summary,
            "categories": item.categories,
            "event_start": item.event_start,
            "event_end": item.event_end,
            "feed_url": feed_url,
            "first_seen_at": time_text(seen_at),
            "revision": revision,
            "fingerprint": fingerprint,
        }
        # 空の欄は書かない（1 日 2,400 件たまるので）。
        record = {key: value for key, value in record.items() if value not in ("", [], None) or key in ("id", "revision")}
        self.pending.append(record)
        fingerprints = (known[1:] if known else []) + [fingerprint]
        self.seen[item_id] = [revision] + fingerprints[-MAX_FINGERPRINTS:]
        return "revised" if known else "new"

    def save(self, seen_at: datetime) -> None:
        if not self.pending:
            return
        items_path = self.directory / "items" / f"{seen_at.astimezone(JST).year}.jsonl"
        items_path.parent.mkdir(parents=True, exist_ok=True)
        # 先に項目を足し、後で印を書く。途中で落ちても項目は失わない
        # （印が古いまま残ると次の回にもう 1 行足すが、読む側は id で 1 つにする）。
        with items_path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in self.pending:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        write_json(self.seen_path, self.seen)
        self.pending = []


# ─────────────────────────────────────
# 取得
# ─────────────────────────────────────

class HostPacer:
    """同じホストへは同時に 1 本、`interval` 秒以上空けて当てる。"""

    def __init__(self, interval: float = HOST_INTERVAL_SECONDS) -> None:
        self.interval = interval
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}

    def hold(self, host: str):
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        pacer = self

        class _Hold:
            def __enter__(self_inner):
                lock.acquire()
                wait = pacer._last.get(host, 0.0) + pacer.interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)

            def __exit__(self_inner, *_exc):
                pacer._last[host] = time.monotonic()
                lock.release()

        return _Hold()


def backoff_due(state: dict, now: datetime) -> bool:
    """続けて失敗しているフィードを、今回読むか。"""
    failures = int(state.get("failures") or 0)
    checked = parse_time_text(state.get("checked_at"))
    if checked is None:
        return True
    for threshold, wait in BACKOFF_STEPS:
        if failures >= threshold:
            return now - checked >= wait
    return True


@dataclass
class FetchedFeed:
    status_code: int
    headers: object
    url: str
    raw: bytes = b""


@dataclass
class FeedResult:
    url: str
    status: str  # fetched / not_modified / failed / skipped
    items: int = 0
    new: int = 0
    revised: int = 0
    error: str = ""


class NewsFetcher:
    def __init__(self, *, data_root: Path, work_root: Path, session: requests.Session | None = None,
                 pacer: HostPacer | None = None, force: bool = False, dry_run: bool = False,
                 clock=now_jst) -> None:
        self.data_root = data_root
        self.work_root = work_root
        self.session = session or requests.Session()
        self.pacer = pacer or HostPacer()
        self.force = force
        self.dry_run = dry_run
        self.clock = clock
        self.state_path = work_root / "feed_state.json"
        self.state: dict[str, dict] = self._load_state()
        self._state_lock = threading.Lock()

    def _load_state(self) -> dict[str, dict]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            self.state_path.replace(self.state_path.with_name(f"feed_state.json.corrupt-{int(time.time())}"))
            return {}
        return payload if isinstance(payload, dict) else {}

    def _state_for(self, url: str) -> dict:
        with self._state_lock:
            return dict(self.state.get(url) or {})

    def _put_state(self, url: str, entry: dict) -> None:
        with self._state_lock:
            self.state[url] = entry

    def _get(self, url: str, entry: dict) -> FetchedFeed:
        """1 本読む。本文を読み終えるまでホストを放さない（同じホストへは同時に 1 本）。"""
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT, "Accept-Language": "ja,en;q=0.5"}
        if not self.force:
            if entry.get("etag"):
                headers["If-None-Match"] = str(entry["etag"])
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = str(entry["last_modified"])
        host = (urlsplit(url).hostname or "").lower()
        with self.pacer.hold(host):
            response = self.session.get(url, headers=headers, timeout=TIMEOUT_SECONDS, stream=True)
            try:
                if response.status_code == 304:
                    return FetchedFeed(304, response.headers, response.url)
                if response.status_code >= 400:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                raw = read_limited(response, MAX_FEED_BYTES)
            finally:
                response.close()
        return FetchedFeed(response.status_code, response.headers, response.url, raw)

    def fetch_feed(self, target: FeedTarget, store: MunicipalityStore) -> FeedResult:
        entry = self._state_for(target.url)
        now = self.clock()
        if not self.force and not backoff_due(entry, now):
            return FeedResult(target.url, "skipped")
        entry["checked_at"] = time_text(now)
        try:
            response = self._get(target.url, entry)
            if response.status_code == 304:
                entry.update({"ok_at": time_text(now), "failures": 0, "status": "not_modified"})
                entry.pop("last_error", None)
                self._put_state(target.url, entry)
                return FeedResult(target.url, "not_modified")
            parsed = parse_feed(response.raw, content_type=response.headers.get("Content-Type", ""),
                                base_url=response.url)
        except Exception as exc:  # 取得・読み取りの失敗は、どれもフィードの失敗として数える
            entry["failures"] = int(entry.get("failures") or 0) + 1
            entry["status"] = "failed"
            entry["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
            self._put_state(target.url, entry)
            return FeedResult(target.url, "failed", error=entry["last_error"])

        result = FeedResult(target.url, "fetched", items=len(parsed.items))
        for item in parsed.items:
            outcome = store.add(item, target.url, now)
            if outcome == "new":
                result.new += 1
            elif outcome == "revised":
                result.revised += 1
        entry.update({
            "ok_at": time_text(now),
            "failures": 0,
            "status": "fetched",
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
            "format": parsed.format,
            "title": parsed.title,
            "items": len(parsed.items),
            "newest": parsed.newest,
        })
        if response.url and response.url != target.url:
            entry["final_url"] = response.url
        else:
            entry.pop("final_url", None)
        entry.pop("last_error", None)
        self._put_state(target.url, entry)
        return result

    def fetch_municipality(self, targets: list[FeedTarget]) -> list[FeedResult]:
        """1 自治体のフィードを順に読む。保存先の失敗でも 1 回り全体は止めない。"""
        first = targets[0]
        try:
            store = MunicipalityStore(self.data_root, first.code, first.slug)
        except Exception as exc:
            error = f"保存先を開けません {type(exc).__name__}: {exc}"[:300]
            return [FeedResult(target.url, "failed", error=error) for target in targets]
        results: list[FeedResult] = []
        for target in targets:
            try:
                results.append(self.fetch_feed(target, store))
            except Exception as exc:  # 1 本の思わぬ失敗で、自治体の残りを捨てない
                results.append(FeedResult(target.url, "failed", error=f"{type(exc).__name__}: {exc}"[:300]))
        if not self.dry_run:
            try:
                store.save(self.clock())
            except Exception as exc:
                # 項目を書けなかった。印も書いていないので、次の回に同じ項目を新しく数え直す。
                # ETag を残すと次の回が 304 で終わって読み直さないので、条件付きの印を消す。
                error = f"保存できません {type(exc).__name__}: {exc}"[:300]
                for target in targets:
                    entry = self._state_for(target.url)
                    for key in ("etag", "last_modified"):
                        entry.pop(key, None)
                    self._put_state(target.url, entry)
                results = [FeedResult(result.url, "failed", error=error) if result.new or result.revised else result
                           for result in results]
        return results

    def save_state(self) -> None:
        if self.dry_run:
            return
        with self._state_lock:
            write_json(self.state_path, self.state)


def run(*, registry: Path = REGISTRY, data_root: Path = DEFAULT_DATA_ROOT, work_root: Path = DEFAULT_WORK_ROOT,
        codes: set[str] | None = None, limit: int = 0, workers: int = DEFAULT_WORKERS, force: bool = False,
        dry_run: bool = False, session: requests.Session | None = None, quiet: bool = False) -> dict[str, object]:
    """台帳のフィードを 1 回りする。結果の要約を返す（Celery の戻り値にもなる）。"""
    started = now_jst()
    grouped = load_feed_targets(registry)
    if codes:
        grouped = {code: targets for code, targets in grouped.items() if code in codes}
    jobs = [grouped[code] for code in sorted(grouped)]
    if limit > 0:
        jobs = jobs[:limit]

    fetcher = NewsFetcher(data_root=data_root, work_root=work_root, session=session, force=force, dry_run=dry_run)
    counts: Counter[str] = Counter()
    new_items = revised_items = 0
    municipalities_with_new = 0
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max(1, workers)) as executor:
        for done, results in enumerate(executor.map(fetcher.fetch_municipality, jobs), start=1):
            municipality_new = 0
            for result in results:
                counts[result.status] += 1
                new_items += result.new
                revised_items += result.revised
                municipality_new += result.new
                if result.error and len(errors) < 30:
                    errors.append({"url": result.url, "error": result.error})
            if municipality_new:
                municipalities_with_new += 1
            if not quiet and done % 200 == 0:
                print(f"[NEWS] {done}/{len(jobs)} 自治体", flush=True)
    fetcher.save_state()

    # トップページの「各地のお知らせ」。書けなくても取得の結果は残す。
    latest: dict[str, object] = {}
    if not dry_run:
        try:
            latest = dict(build_latest(data_root=data_root, work_root=work_root))
        except Exception as exc:
            latest = {"latest_error": f"{type(exc).__name__}: {exc}"[:300]}

    finished = now_jst()
    summary: dict[str, object] = {
        "task": "news_fetch",
        "started_at": time_text(started),
        "finished_at": time_text(finished),
        "seconds": round((finished - started).total_seconds(), 1),
        "municipalities": len(jobs),
        "feeds": sum(counts.values()),
        "fetched": counts["fetched"],
        "not_modified": counts["not_modified"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "new_items": new_items,
        "revised_items": revised_items,
        "municipalities_with_new": municipalities_with_new,
        "dry_run": dry_run,
        **latest,
        "errors": errors,
    }
    if not dry_run:
        write_json(work_root / "last_run.json", summary)
        with (work_root / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            brief = {key: value for key, value in summary.items() if key != "errors"}
            handle.write(json.dumps(brief, ensure_ascii=False) + "\n")
    if not quiet:
        print(
            f"[NEWS] 自治体 {summary['municipalities']} フィード {summary['feeds']}"
            f"（取得 {summary['fetched']}・変化なし {summary['not_modified']}・失敗 {summary['failed']}"
            f"・間隔待ち {summary['skipped']}） 新しい項目 {new_items}・更新 {revised_items}"
            f" {summary['seconds']} 秒",
            flush=True,
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="自治体の新着情報のフィードを読み、項目をためる")
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--code", action="append", default=[], help="自治体コード（繰り返し可）")
    parser.add_argument("--limit", type=int, default=0, help="先頭から何自治体まで")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--force", action="store_true", help="失敗の間隔と条件付き取得を無視して全部読む")
    parser.add_argument("--dry-run", action="store_true", help="読むだけで保存しない")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run(
        registry=args.registry,
        data_root=args.data_root,
        work_root=args.work_root,
        codes={code.strip() for code in args.code if code.strip()} or None,
        limit=args.limit,
        workers=args.workers,
        force=args.force,
        dry_run=args.dry_run,
    )
    return 0 if summary["feeds"] == 0 or summary["failed"] < summary["feeds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
