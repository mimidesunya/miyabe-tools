#!/usr/bin/env python3
"""お知らせのリンク先を読み、本文をためる。

フィードの取得（`fetch_news_feeds.py`）がためた項目を後ろから追いかけ、まだ本文の無い
項目のリンク先を読む。Celery の `run_news_fetch` がフィードの取得のすぐ後に呼ぶ。

    data/news/<slug>/bodies/
        <年>.jsonl    本文（読んだ年ごと。1 行 1 件。追記だけで消さない）
        state.json    どこまで項目を見たか・まだ読んでいない項目・読んだ項目の印

**自治体のサイトはお知らせのページを消す。** 2026-11-14 に試した 300 件では、
出てから数日で 1% が 404 だった。見つけたらすぐ読む。消えていたらそれも 1 行残す。

- 新しく見つけた項目から読む。版が上がった（題名か日付が変わった）項目は読み直し、
  本文が前と同じなら行を足さない。
- 1 回に読むのは 1 自治体あたり `全体の上限 ÷ 待っている自治体の数`（5〜200 件）。
  1 回目のような溜まった分は何回かに分けて読み、どの自治体も少しずつ進む。
- 同じホストへは 1 秒以上空け、同時に 1 本（フィードの取得と同じ）。
- つながらない・5xx などは次の回に読み直し、4 回続けて駄目なら諦めて 1 行残す。
  http でつながらないときは https で読み直す（http を閉じたサイトがある）。
  404・410 は消えたとして 1 行残す。
- HTML は本文と添付の一覧（`news_body.py`）、PDF は本文を抜く。そのほかの形式は
  形式だけ残す。

    python tools/news/fetch_news_bodies.py --code 01100
    python tools/news/fetch_news_bodies.py --per-run 200 --data-root /tmp/news --work-root /tmp/news-work
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.document_crawl import USER_AGENT, extract_pdf_text, read_limited, sniff_document_type, write_json  # noqa: E402
from tools.news.fetch_news_feeds import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    DEFAULT_WORK_ROOT,
    HostPacer,
    now_jst,
    time_text,
)
from tools.news.news_body import TEXT_MAX_CHARS, extract_page  # noqa: E402

DEFAULT_PER_RUN = 4000
MIN_PER_MUNICIPALITY = 5
MAX_PER_MUNICIPALITY = 200
DEFAULT_WORKERS = 16
MAX_TRIES = 4
MAX_BYTES = 20_000_000
TIMEOUT_SECONDS = 30.0
GONE_STATUSES = (404, 410)
ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"


class TooLarge(Exception):
    """上限より大きい。`size` は応答が告げた大きさ（告げなければ None）。"""

    def __init__(self, size: int | None) -> None:
        super().__init__(f"大きすぎるため取りません（{size} バイト）" if size else "大きすぎるため取りません")
        self.size = size


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class BodyStore:
    """1 自治体ぶんの本文。1 つのスレッドだけが触る。

    `state.json`:
        cursor   項目のファイルごとに、どこまで読んだか（バイト）
        pending  id → {url, revision, seen, date, tries, error}  まだ読んでいない項目
        done     id → [読んだ版, 本文の印 か "gone" / "failed"]
    """

    def __init__(self, data_root: Path, slug: str) -> None:
        self.slug = slug
        self.directory = data_root / slug
        self.state_path = self.directory / "bodies" / "state.json"
        state = self._load()
        self.cursor: dict[str, int] = {str(k): int(v) for k, v in dict(state.get("cursor") or {}).items()}
        self.pending: dict[str, dict] = dict(state.get("pending") or {})
        self.done: dict[str, list] = dict(state.get("done") or {})
        self.lines: list[dict] = []

    def _load(self) -> dict:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            # 壊れていたら脇へどけ、項目を初めから見直す（読んだ本文は done が無いので読み直しになる）。
            self.state_path.replace(self.state_path.with_name(f"state.json.corrupt-{int(time.time())}"))
            return {}
        return payload if isinstance(payload, dict) else {}

    def scan_items(self) -> int:
        """前の回より後に足された項目を、待ちに入れる。入れた数を返す。"""
        added = 0
        items = self.directory / "items"
        if not items.is_dir():
            return 0
        for path in sorted(items.glob("*.jsonl")):
            offset = self.cursor.get(path.name, 0)
            try:
                size = path.stat().st_size
                if size <= offset:
                    continue
                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(size - offset)
            except OSError:
                continue
            end = chunk.rfind(b"\n")
            if end < 0:
                continue
            for line in chunk[:end].decode("utf-8", "replace").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if self._enqueue(record):
                    added += 1
            self.cursor[path.name] = offset + end + 1
        return added

    def _enqueue(self, record: dict) -> bool:
        item_id = str(record.get("id") or "")
        url = str(record.get("url") or "")
        if not item_id or not url.startswith(("http://", "https://")):
            return False
        revision = int(record.get("revision") or 0)
        known = self.done.get(item_id)
        if known and int(known[0]) >= revision:
            return False
        waiting = self.pending.get(item_id)
        if waiting and int(waiting.get("revision", 0)) >= revision:
            return False
        self.pending[item_id] = {
            "url": url,
            "revision": revision,
            "seen": str(record.get("first_seen_at") or ""),
            "date": str(record.get("date") or ""),
            "tries": 0,
        }
        return True

    def take(self, limit: int) -> list[tuple[str, dict]]:
        """新しく見つけた順（時の単位。同じなら日付の新しい順）に `limit` 件。

        見つけた時刻はフィードごとに秒が違うので、秒まで比べると後に読んだフィードの
        古い項目が先に来る。
        """
        ordered = sorted(self.pending.items(),
                         key=lambda pair: (str(pair[1].get("seen", ""))[:13], pair[1].get("date", "")), reverse=True)
        return ordered[:max(0, limit)]

    def finish(self, item_id: str, entry: dict, line: dict, mark: str) -> bool:
        """読んだ結果を残す。本文が前の版と同じなら行は足さない。足したら True。"""
        self.pending.pop(item_id, None)
        previous = self.done.get(item_id)
        self.done[item_id] = [int(entry.get("revision", 0)), mark]
        if previous and previous[1] == mark and mark not in ("gone", "failed"):
            return False
        self.lines.append(line)
        return True

    def retry_later(self, item_id: str, error: str) -> bool:
        """読めなかった。諦めたら True。"""
        entry = self.pending.get(item_id)
        if entry is None:
            return False
        entry["tries"] = int(entry.get("tries", 0)) + 1
        entry["error"] = error[:300]
        return entry["tries"] >= MAX_TRIES

    def save(self, now: datetime) -> None:
        if self.lines:
            path = self.directory / "bodies" / f"{now.year}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                for line in self.lines:
                    handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            self.lines = []
        write_json(self.state_path, {"cursor": self.cursor, "pending": self.pending, "done": self.done})


class BodyFetcher:
    def __init__(self, *, data_root: Path, session: requests.Session | None = None, pacer: HostPacer | None = None,
                 clock=now_jst) -> None:
        self.data_root = data_root
        self.session = session or requests.Session()
        self.pacer = pacer or HostPacer()
        self.clock = clock
        # http をやめて https だけで答えるホスト（鶴岡市など。フィードのリンクは http のまま）。
        self._https_hosts: set[str] = set()
        self._lock = threading.Lock()

    def fetch(self, url: str, max_bytes: int = MAX_BYTES) -> tuple[int, str, str, bytes]:
        """(状態, 行き着いた URL, Content-Type, 中身)。4xx・5xx は中身を読まない。

        http でつながらなければ https で読み直す。読めたホストは以後 https で読む。
        """
        host = (urlsplit(url).hostname or "").lower()
        if url.startswith("http://"):
            with self._lock:
                upgraded = host in self._https_hosts
            if upgraded:
                return self._get("https://" + url[len("http://"):], max_bytes)
        try:
            return self._get(url, max_bytes)
        except (requests.ConnectionError, requests.Timeout):
            if not url.startswith("http://"):
                raise
            result = self._get("https://" + url[len("http://"):], max_bytes)
            with self._lock:
                self._https_hosts.add(host)
            return result

    def _get(self, url: str, max_bytes: int) -> tuple[int, str, str, bytes]:
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT, "Accept-Language": "ja,en;q=0.5"}
        host = (urlsplit(url).hostname or "").lower()
        with self.pacer.hold(host):
            response = self.session.get(url, headers=headers, timeout=TIMEOUT_SECONDS, stream=True)
            try:
                if response.status_code >= 400:
                    return response.status_code, response.url, "", b""
                declared = str(response.headers.get("Content-Length") or "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise TooLarge(int(declared))
                try:
                    raw = read_limited(response, max_bytes)
                except ValueError as exc:
                    raise TooLarge(None) from exc
            finally:
                response.close()
        return response.status_code, response.url, response.headers.get("Content-Type", ""), raw

    def read(self, store: BodyStore, code: str, item_id: str, entry: dict) -> str:
        """1 件読む。`fetched` / `unchanged` / `gone` / `retry` / `failed` を返す。"""
        now = self.clock()
        url = str(entry["url"])
        line: dict[str, object] = {"id": item_id, "jis_code": code, "revision": int(entry.get("revision", 0)),
                                   "url": url, "fetched_at": time_text(now)}
        try:
            status, final_url, content_type, raw = self.fetch(url)
        except TooLarge as exc:  # 取り直しても同じ
            line.update({"kind": "too_large", "size": exc.size})
            store.finish(item_id, entry, line, "too_large")
            return "failed"
        except Exception as exc:  # つながらない・時間切れ
            return self._failure(store, item_id, entry, line, f"{type(exc).__name__}: {exc}")
        line["status"] = status
        if final_url and final_url != url:
            line["final_url"] = final_url
        if status in GONE_STATUSES:
            line["kind"] = "gone"
            store.finish(item_id, entry, line, "gone")
            return "gone"
        if status >= 400:
            return self._failure(store, item_id, entry, line, f"HTTP {status}")
        try:
            line.update(self._content(raw, content_type, final_url or url))
        except Exception as exc:  # 読めない HTML・壊れた PDF
            line.update({"kind": "unreadable", "error": f"{type(exc).__name__}: {exc}"[:300]})
        text = str(line.get("text") or "")
        if text:
            line["chars"] = len(text)
        mark = text_hash(text) if text else str(line.get("kind"))
        line["text_hash"] = mark
        return "fetched" if store.finish(item_id, entry, line, mark) else "unchanged"

    def _content(self, raw: bytes, content_type: str, url: str) -> dict[str, object]:
        kind = sniff_document_type(raw, url, content_type)
        if kind == "pdf":
            text, pages = extract_pdf_text(raw)
            return {"kind": "pdf", "pages": pages, "text": text[:TEXT_MAX_CHARS]}
        if kind:
            return {"kind": kind}
        media_type = content_type.split(";", 1)[0].strip().lower()
        if "html" not in media_type and "xml" not in media_type and not raw[:1024].lstrip().startswith(b"<"):
            # 画像・動画など。HTML として読むと文字化けの本文ができる。
            return {"kind": "other", "media_type": media_type}
        page = extract_page(raw, content_type, url)
        result: dict[str, object] = {"kind": "html", "page_title": page.title, "text": page.text, "method": page.method}
        if page.attachments:
            result["attachments"] = page.attachments
        return {key: value for key, value in result.items() if value not in ("", [], None)}

    def _failure(self, store: BodyStore, item_id: str, entry: dict, line: dict, error: str) -> str:
        if not store.retry_later(item_id, error):
            return "retry"
        line.update({"kind": "failed", "error": error[:300], "tries": int(entry.get("tries", 0))})
        store.finish(item_id, entry, line, "failed")
        return "failed"

    def read_municipality(self, store: BodyStore, code: str, limit: int) -> Counter[str]:
        counts: Counter[str] = Counter()
        for item_id, entry in store.take(limit):
            try:
                counts[self.read(store, code, item_id, entry)] += 1
            except Exception as exc:  # 1 件の思わぬ失敗で、自治体の残りを捨てない
                counts[self._failure(store, item_id, entry, {"id": item_id, "url": entry.get("url")},
                                     f"{type(exc).__name__}: {exc}")] += 1
        return counts


def municipality_dirs(data_root: Path, codes: set[str] | None = None) -> list[Path]:
    if not data_root.is_dir():
        return []
    found = []
    for directory in sorted(data_root.iterdir()):
        if not (directory / "items").is_dir():
            continue
        if codes and directory.name.split("-", 1)[0] not in codes:
            continue
        found.append(directory)
    return found


def per_municipality_limit(per_run: int, waiting: int) -> int:
    return max(MIN_PER_MUNICIPALITY, min(MAX_PER_MUNICIPALITY, per_run // max(1, waiting)))


def run(*, data_root: Path = DEFAULT_DATA_ROOT, work_root: Path = DEFAULT_WORK_ROOT, codes: set[str] | None = None,
        per_run: int = DEFAULT_PER_RUN, workers: int = DEFAULT_WORKERS, session: requests.Session | None = None,
        pacer: HostPacer | None = None, quiet: bool = False) -> dict[str, object]:
    """待っている項目のリンク先を読む。結果の要約を返す。"""
    started = now_jst()
    stores: list[tuple[str, BodyStore]] = []
    queued = 0
    errors: list[dict[str, str]] = []
    for directory in municipality_dirs(data_root, codes):
        try:
            store = BodyStore(data_root, directory.name)
            queued += store.scan_items()
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"slug": directory.name, "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        stores.append((directory.name.split("-", 1)[0], store))
    waiting_before = sum(len(store.pending) for _, store in stores)
    with_pending = [(code, store) for code, store in stores if store.pending]
    limit = per_municipality_limit(per_run, len(with_pending))

    fetcher = BodyFetcher(data_root=data_root, session=session, pacer=pacer)
    counts: Counter[str] = Counter()
    lock = threading.Lock()

    def work(pair: tuple[str, BodyStore]) -> None:
        code, store = pair
        try:
            result = fetcher.read_municipality(store, code, limit)
        except Exception as exc:
            result = Counter()
            with lock:
                if len(errors) < 30:
                    errors.append({"slug": store.slug, "error": f"{type(exc).__name__}: {exc}"[:300]})
        with lock:
            counts.update(result)

    with ThreadPoolExecutor(max(1, workers)) as executor:
        list(executor.map(work, with_pending))
    saved_at = now_jst()
    for _, store in stores:
        try:
            store.save(saved_at)
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"slug": store.slug, "error": f"保存できません {type(exc).__name__}: {exc}"[:300]})

    finished = now_jst()
    summary: dict[str, object] = {
        "task": "news_bodies",
        "started_at": time_text(started),
        "finished_at": time_text(finished),
        "seconds": round((finished - started).total_seconds(), 1),
        "municipalities": len(stores),
        "queued": queued,
        "waiting_before": waiting_before,
        "per_municipality": limit,
        "fetched": counts["fetched"],
        "unchanged": counts["unchanged"],
        "gone": counts["gone"],
        "retry": counts["retry"],
        "failed": counts["failed"],
        "waiting_after": sum(len(store.pending) for _, store in stores),
        "errors": errors,
    }
    write_json(work_root / "bodies_last_run.json", summary)
    with (work_root / "bodies_runs.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({key: value for key, value in summary.items() if key != "errors"}, ensure_ascii=False) + "\n")
    if not quiet:
        print(
            f"[NEWS-BODY] 自治体 {summary['municipalities']} 待ち {waiting_before}→{summary['waiting_after']}"
            f"（1 自治体 {limit} 件まで） 読んだ {summary['fetched']}・同じ {summary['unchanged']}"
            f"・消えていた {summary['gone']}・読み直し {summary['retry']}・諦めた {summary['failed']}"
            f" {summary['seconds']} 秒",
            flush=True,
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="お知らせのリンク先を読み、本文をためる")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--code", action="append", default=[], help="自治体コード（繰り返し可）")
    parser.add_argument("--per-run", type=int, default=DEFAULT_PER_RUN, help="1 回に読む数のめやす")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run(
        data_root=args.data_root,
        work_root=args.work_root,
        codes={code.strip() for code in args.code if code.strip()} or None,
        per_run=args.per_run,
        workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
