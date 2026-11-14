#!/usr/bin/env python3
"""お知らせの添付の PDF を読み、本文をためる。

本文の取得（`fetch_news_bodies.py`）がページから控えた添付の一覧を後ろから追いかけ、
まだ読んでいない PDF を読む。Celery の `run_news_fetch` が本文の取得のすぐ後に呼ぶ。

    data/news/<slug>/attachments/
        <年>.jsonl    PDF の本文（読んだ年ごと。1 行 1 本。追記だけで消さない）
        state.json    どこまで本文の行を見たか・まだ読んでいない PDF・読んだ PDF の印

お知らせの半分に PDF の添付があり、報道発表やイベントの案内のように中身が PDF だけの
ものも多い。ページと同じく PDF も消えるので、本文を読んだ回のうちに読む。

- PDF そのものは残さず、本文だけ残す（2026-11-14 に全国 400 本で試すと平均 1.5MB。
  1 日 5 千本ほど出るので、残すと年に数 TB になる）。頁は改頁文字（\\f）で区切る。
- 1 件のお知らせから読むのは、ページに並んだ順に 20 本まで（様式や過去の号を何十本も
  並べるページがある）。同じ自治体で同じ URL は 1 回だけ読む。
- 1 回に読むのは 1 自治体あたり `全体の上限 ÷ 待っている自治体の数`（3〜200 本）。
  加えて、使ってよい時間（既定 60 分）を過ぎたら新しく読み始めない。3 時間おきの回が
  次の回に重ならないため。待ちの古い自治体から読むので、時間切れでも同じ自治体ばかり
  残らない。
- 同じホストへは 1 秒以上空け、同時に 1 本。http でつながらなければ https で読み直す。
- 30MB を超えるもの（大きさを告げていればその場で）は読まずに 1 行残す。頁は先頭の
  300 頁まで読む。文字の無い PDF（画像だけ）は頁数だけ残す。

    python tools/news/fetch_news_attachments.py --code 01100
    python tools/news/fetch_news_attachments.py --budget-minutes 10 --data-root /tmp/news --work-root /tmp/news-work
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

from tools.document_crawl import join_pdf_pages, sniff_document_type, write_json  # noqa: E402
from tools.news.fetch_news_bodies import (  # noqa: E402
    GONE_STATUSES,
    MAX_TRIES,
    BodyFetcher,
    TooLarge,
    municipality_dirs,
    text_hash,
)
from tools.news.fetch_news_feeds import DEFAULT_DATA_ROOT, DEFAULT_WORK_ROOT, HostPacer, now_jst, time_text  # noqa: E402
from tools.news.pdf_text_worker import PdfTextWorker, PdfTimeout  # noqa: E402

DEFAULT_PER_RUN = 6000
MIN_PER_MUNICIPALITY = 3
MAX_PER_MUNICIPALITY = 200
DEFAULT_WORKERS = 16
DEFAULT_BUDGET_SECONDS = 60 * 60
MAX_PER_ITEM = 20
MAX_BYTES = 30_000_000
MAX_PAGES = 300
# 1 本を読む時間の上限。手元で最大 49 秒（図面）、本番の CPU は 0.5 なのでその倍ほど。
PARSE_TIMEOUT_SECONDS = 180.0
TEXT_MAX_CHARS = 200_000
READ_EXTENSIONS = (".pdf",)


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def is_pdf_link(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(READ_EXTENSIONS)


class AttachmentStore:
    """1 自治体ぶんの添付。1 つのスレッドだけが触る。

    `state.json`:
        cursor   本文のファイルごとに、どこまで読んだか（バイト）
        pending  URL → {item, text, seen, tries, error}  まだ読んでいない PDF
        done     URL の印 → 本文の印 か "gone" / "failed" / "too_large" / 形式
    """

    def __init__(self, data_root: Path, slug: str) -> None:
        self.slug = slug
        self.directory = data_root / slug
        self.state_path = self.directory / "attachments" / "state.json"
        state = self._load()
        self.cursor: dict[str, int] = {str(k): int(v) for k, v in dict(state.get("cursor") or {}).items()}
        self.pending: dict[str, dict] = dict(state.get("pending") or {})
        self.done: dict[str, str] = dict(state.get("done") or {})
        self.lines: list[dict] = []

    def _load(self) -> dict:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            # 壊れていたら脇へどけ、本文の行を初めから見直す（読んだ PDF も読み直しになる）。
            self.state_path.replace(self.state_path.with_name(f"state.json.corrupt-{int(time.time())}"))
            return {}
        return payload if isinstance(payload, dict) else {}

    def scan_bodies(self) -> int:
        """前の回より後に足された本文の行から、添付の PDF を待ちに入れる。入れた数を返す。"""
        added = 0
        bodies = self.directory / "bodies"
        if not bodies.is_dir():
            return 0
        for path in sorted(bodies.glob("*.jsonl")):
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
                added += self._enqueue(record)
            self.cursor[path.name] = offset + end + 1
        return added

    def _enqueue(self, record: dict) -> int:
        links = [link for link in record.get("attachments") or []
                 if isinstance(link, dict) and str(link.get("url") or "").startswith(("http://", "https://"))
                 and is_pdf_link(str(link["url"]))]
        added = 0
        for link in links[:MAX_PER_ITEM]:
            url = str(link["url"])
            if url in self.pending or url_key(url) in self.done:
                continue
            self.pending[url] = {
                "item": str(record.get("id") or ""),
                "text": str(link.get("text") or ""),
                "seen": str(record.get("fetched_at") or ""),
                "tries": 0,
            }
            added += 1
        return added

    def take(self, limit: int) -> list[tuple[str, dict]]:
        """本文を新しく読んだ順（時の単位。同じならページに並んだ順）に `limit` 本。"""
        ordered = sorted(enumerate(self.pending.items()),
                         key=lambda pair: (str(pair[1][1].get("seen", ""))[:13], -pair[0]), reverse=True)
        return [pair for _, pair in ordered[:max(0, limit)]]

    def oldest_wait(self) -> str:
        return min((str(entry.get("seen") or "") for entry in self.pending.values()), default="")

    def finish(self, url: str, line: dict, mark: str) -> None:
        self.pending.pop(url, None)
        self.done[url_key(url)] = mark
        self.lines.append(line)

    def retry_later(self, url: str, error: str) -> bool:
        """読めなかった。諦めたら True。"""
        entry = self.pending.get(url)
        if entry is None:
            return False
        entry["tries"] = int(entry.get("tries", 0)) + 1
        entry["error"] = error[:300]
        return entry["tries"] >= MAX_TRIES

    def save(self, now: datetime) -> None:
        if self.lines:
            path = self.directory / "attachments" / f"{now.year}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                for line in self.lines:
                    handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            self.lines = []
        write_json(self.state_path, {"cursor": self.cursor, "pending": self.pending, "done": self.done})


class AttachmentReader:
    def __init__(self, *, fetcher: BodyFetcher, parser: PdfTextWorker, deadline: float, clock=now_jst) -> None:
        self.fetcher = fetcher
        self.parser = parser
        self.deadline = deadline
        self.clock = clock
        self._lock = threading.Lock()
        self.downloaded = 0

    def read(self, store: AttachmentStore, code: str, url: str, entry: dict) -> str:
        """1 本読む。`fetched` / `no_text` / `too_slow` / `gone` / `skipped` / `retry` / `failed` を返す。"""
        line: dict[str, object] = {"url": url, "item": entry.get("item", ""), "jis_code": code,
                                   "link_text": entry.get("text", ""), "fetched_at": time_text(self.clock())}
        try:
            status, final_url, content_type, raw = self.fetcher.fetch(url, MAX_BYTES)
        except TooLarge as exc:  # 取り直しても同じ
            line.update({"kind": "too_large", "size": exc.size})
            store.finish(url, line, "too_large")
            return "skipped"
        except Exception as exc:  # つながらない・時間切れ
            return self._failure(store, url, entry, line, f"{type(exc).__name__}: {exc}")
        line["status"] = status
        if final_url and final_url != url:
            line["final_url"] = final_url
        if status in GONE_STATUSES:
            line["kind"] = "gone"
            store.finish(url, line, "gone")
            return "gone"
        if status >= 400:
            return self._failure(store, url, entry, line, f"HTTP {status}")
        with self._lock:
            self.downloaded += len(raw)
        line["bytes"] = len(raw)
        line["sha1"] = hashlib.sha1(raw).hexdigest()
        kind = sniff_document_type(raw, final_url or url, content_type)
        if kind != "pdf":
            # 転送でトップページや「ページが見つかりません」の HTML に行き着くことがある。
            media_type = content_type.split(";", 1)[0].strip().lower()
            line.update({"kind": kind or "other", "media_type": media_type})
            store.finish(url, line, str(line["kind"]))
            return "skipped"
        line["kind"] = "pdf"
        try:
            pages, total = self.parser.read(raw, MAX_PAGES)
        except PdfTimeout as exc:  # 読み直しても同じ
            line["error"] = str(exc)
            store.finish(url, line, "parse_timeout")
            return "too_slow"
        text = join_pdf_pages(pages)
        line["pages"] = total
        if total > len(pages):
            line["pages_read"] = len(pages)
        if not text:
            store.finish(url, line, "no_text")
            return "no_text"
        if len(text) > TEXT_MAX_CHARS:
            text = text[:TEXT_MAX_CHARS]
            line["truncated"] = True
        line.update({"text": text, "chars": len(text), "text_pages": sum(1 for page in pages if page.strip())})
        mark = text_hash(text)
        line["text_hash"] = mark
        store.finish(url, line, mark)
        return "fetched"

    def _failure(self, store: AttachmentStore, url: str, entry: dict, line: dict, error: str) -> str:
        if not store.retry_later(url, error):
            return "retry"
        line.update({"kind": "failed", "error": error[:300], "tries": int(entry.get("tries", 0))})
        store.finish(url, line, "failed")
        return "failed"

    def read_municipality(self, store: AttachmentStore, code: str, limit: int) -> Counter[str]:
        counts: Counter[str] = Counter()
        for url, entry in store.take(limit):
            if time.monotonic() >= self.deadline:
                counts["deferred"] += 1
                continue
            try:
                counts[self.read(store, code, url, entry)] += 1
            except Exception as exc:  # 1 本の思わぬ失敗で、自治体の残りを捨てない
                counts[self._failure(store, url, entry, {"url": url, "item": entry.get("item", "")},
                                     f"{type(exc).__name__}: {exc}")] += 1
        return counts


def per_municipality_limit(per_run: int, waiting: int) -> int:
    return max(MIN_PER_MUNICIPALITY, min(MAX_PER_MUNICIPALITY, per_run // max(1, waiting)))


def run(*, data_root: Path = DEFAULT_DATA_ROOT, work_root: Path = DEFAULT_WORK_ROOT, codes: set[str] | None = None,
        per_run: int = DEFAULT_PER_RUN, workers: int = DEFAULT_WORKERS, budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        session: requests.Session | None = None, pacer: HostPacer | None = None, quiet: bool = False) -> dict[str, object]:
    """待っている PDF を読む。結果の要約を返す。"""
    started = now_jst()
    deadline = time.monotonic() + max(0.0, budget_seconds)
    stores: list[tuple[str, AttachmentStore]] = []
    queued = 0
    errors: list[dict[str, str]] = []
    for directory in municipality_dirs(data_root, codes):
        try:
            store = AttachmentStore(data_root, directory.name)
            queued += store.scan_bodies()
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"slug": directory.name, "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        stores.append((directory.name.split("-", 1)[0], store))
    waiting_before = sum(len(store.pending) for _, store in stores)
    # 待ちの古い自治体から。時間切れで後ろの自治体ばかり残らないように。
    with_pending = sorted(((code, store) for code, store in stores if store.pending),
                          key=lambda pair: (pair[1].oldest_wait(), pair[1].slug))
    limit = per_municipality_limit(per_run, len(with_pending))

    parser = PdfTextWorker(PARSE_TIMEOUT_SECONDS)
    reader = AttachmentReader(fetcher=BodyFetcher(data_root=data_root, session=session, pacer=pacer),
                              parser=parser, deadline=deadline)
    counts: Counter[str] = Counter()
    lock = threading.Lock()

    def work(pair: tuple[str, AttachmentStore]) -> None:
        code, store = pair
        try:
            result = reader.read_municipality(store, code, limit)
        except Exception as exc:
            result = Counter()
            with lock:
                if len(errors) < 30:
                    errors.append({"slug": store.slug, "error": f"{type(exc).__name__}: {exc}"[:300]})
        with lock:
            counts.update(result)

    try:
        with ThreadPoolExecutor(max(1, workers)) as executor:
            list(executor.map(work, with_pending))
    finally:
        parser.close()
    saved_at = now_jst()
    for _, store in stores:
        try:
            store.save(saved_at)
        except Exception as exc:
            if len(errors) < 30:
                errors.append({"slug": store.slug, "error": f"保存できません {type(exc).__name__}: {exc}"[:300]})

    finished = now_jst()
    summary: dict[str, object] = {
        "task": "news_attachments",
        "started_at": time_text(started),
        "finished_at": time_text(finished),
        "seconds": round((finished - started).total_seconds(), 1),
        "municipalities": len(stores),
        "queued": queued,
        "waiting_before": waiting_before,
        "per_municipality": limit,
        "fetched": counts["fetched"],
        "no_text": counts["no_text"],
        "too_slow": counts["too_slow"],
        "gone": counts["gone"],
        "skipped": counts["skipped"],
        "retry": counts["retry"],
        "failed": counts["failed"],
        "out_of_time": counts["deferred"],
        "downloaded_bytes": reader.downloaded,
        "waiting_after": sum(len(store.pending) for _, store in stores),
        "errors": errors,
    }
    write_json(work_root / "attachments_last_run.json", summary)
    with (work_root / "attachments_runs.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({key: value for key, value in summary.items() if key != "errors"}, ensure_ascii=False) + "\n")
    if not quiet:
        print(
            f"[NEWS-ATTACH] 自治体 {summary['municipalities']} 待ち {waiting_before}→{summary['waiting_after']}"
            f"（1 自治体 {limit} 本まで） 読んだ {summary['fetched']}・文字なし {summary['no_text']}・読み切れず {summary['too_slow']}"
            f"・消えていた {summary['gone']}・PDF でない/大きすぎ {summary['skipped']}・読み直し {summary['retry']}"
            f"・諦めた {summary['failed']}・時間切れ {summary['out_of_time']}"
            f" {round(reader.downloaded / 1e6)}MB {summary['seconds']} 秒",
            flush=True,
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="お知らせの添付の PDF を読み、本文をためる")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--code", action="append", default=[], help="自治体コード（繰り返し可）")
    parser.add_argument("--per-run", type=int, default=DEFAULT_PER_RUN, help="1 回に読む数のめやす")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--budget-minutes", type=float, default=DEFAULT_BUDGET_SECONDS / 60,
                        help="この時間を過ぎたら新しく読み始めない")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run(
        data_root=args.data_root,
        work_root=args.work_root,
        codes={code.strip() for code in args.code if code.strip()} or None,
        per_run=args.per_run,
        workers=args.workers,
        budget_seconds=args.budget_minutes * 60,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
