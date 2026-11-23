#!/usr/bin/env python3
"""お知らせの全文検索の索引（OpenSearch の `miyabe-news-current`）。

お知らせ 1 件につき 1 文書（`part: item`。題名・概要・リンク先のページの本文）、添付の PDF 1 本につき
1 文書（`part: attachment`。題名・リンクの文字・PDF の文字）。検索では、同じお知らせの文書を 1 件に
まとめる（`item_key`）。同じ自治体の同じ題名・同じ日付で後から見つけたもの（`hidden`）は検索に出さない。

本文と添付の文字は、`news_index.py` が作る SQLite の索引（`work/news/index.sqlite`）に入れていないので、
行の位置から元のファイルを 1 行ずつ読む。文字は検索用に Sudachi で分かち書きして足す（`title_terms`・`body_terms`）。

    python3 tools/news/news_search.py                    # 印の付いた分を入れる（取得の回の中でも呼ぶ）
    python3 tools/news/news_search.py --rebuild --workers 4  # 新しい世代を一から作って別名を切り替える

- **入れるものは、印（`search_dirty`）で決める。** 項目・本文・添付の行を書くたびに `news_index.py` が付け、
  入れ終わったものを消す。読んでから消すまでに付け直された印は残るので、取りこぼさない。新しい日付から入れる。
- 入れ終わる前に止まっても、印が残っているので次の回に続きから入れる。同じ文書を入れ直しても中身は同じ。
- 取得の回では時間の上限（`--budget-seconds`）の中だけ。初めて作るとき・作り直すときは `--rebuild`。
  別の世代を作り終えてから別名を切り替えるので、そのあいだ検索は前のままである。件数が前の半分に満たなければ
  切り替えない。古い世代は消さない。作りかけで失敗した世代は片付ける（公開していないものだけ）。
- 作り直しのあいだ、取得の回の更新は待たせる（印は残る）。途中で更新すると、古い世代に入れた分の印が消えて、
  作り直した世代から抜けるため。
- 分かち書きの部品（Sudachi）が無ければ止まる。黙って空白区切りにすると、検索の当たりが静かに悪くなるため。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
for extra in (WORKSPACE_ROOT, WORKSPACE_ROOT / "tools" / "search", WORKSPACE_ROOT / "lib" / "python"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from opensearch_client import OpenSearchClient, OpenSearchRequestError  # noqa: E402
from opensearch_mappings import load_index_settings  # noqa: E402
from tools.news import news_index  # noqa: E402

DEFAULT_DATA_ROOT = news_index.DEFAULT_DATA_ROOT
DEFAULT_WORK_ROOT = news_index.DEFAULT_WORK_ROOT
ALIAS = "miyabe-news-current"
INDEX_PREFIX = "miyabe-news-v"
BULK_DOCS = 100
BULK_CHARS = 1_500_000
COMMIT_SECONDS = 60.0
BUDGET_SECONDS = 600.0
REFRESH_INTERVAL = "30s"
REBUILD_MARK = "search_rebuild"  # meta の鍵。作り直し中の印
REBUILD_STALE_SECONDS = 24 * 3600  # これより古い印は、作り直しが落ちた残りと見なす

DATE_FORMAT = "strict_date_optional_time||yyyy-MM-dd HH:mm:ss||yyyy-MM-dd||epoch_millis"
KEYWORD = {"type": "keyword"}
URL = {"type": "keyword", "ignore_above": 2048}
DATE = {"type": "date", "format": DATE_FORMAT, "ignore_malformed": True}
TEXT = {"type": "text", "analyzer": "ja_cjk"}
TERMS = {"type": "text", "analyzer": "sudachi_terms"}

MAPPING: dict[str, Any] = {
    "dynamic": False,
    "_source": {"excludes": ["title_terms", "body_terms"]},
    "properties": {
        "item_key": KEYWORD,
        "part": KEYWORD,
        "slug": KEYWORD,
        "municipality_code": KEYWORD,
        "pref_code": KEYWORD,
        "pref_name": KEYWORD,
        "municipality_name": KEYWORD,
        "title": {
            **TEXT,
            "fields": {
                "keyword": {"type": "keyword", "ignore_above": 512},
                "ngram": {"type": "text", "analyzer": "ja_ngram", "search_analyzer": "ja_cjk"},
            },
        },
        "title_terms": TERMS,
        "summary": TEXT,
        "body": TEXT,
        "body_terms": TERMS,
        "body_length": {"type": "integer"},
        "link_text": TEXT,
        "page_title": TEXT,
        "url": URL,
        "attachment_url": URL,
        "categories": KEYWORD,
        "kind": KEYWORD,
        "pages": {"type": "integer"},
        "date": DATE,
        "sort_date": DATE,
        "first_seen_at": DATE,
        "event_start": DATE,
        "event_end": DATE,
        "fetched_at": DATE,
        "hidden": {"type": "boolean"},
        "ref": KEYWORD,
        "offset": {"type": "long"},
    },
}

ITEM_SQL = """
SELECT i.slug, i.item_id, i.jis_code, i.pref_code, i.title, i.url, i.date, i.sort_date, i.first_seen_at,
       i.summary, i.categories, i.event_start, i.event_end, i.hidden, m.name AS municipality_name, m.pref_name,
       b.kind AS body_kind, b.ref AS body_ref, b.offset AS body_offset, b.chars AS body_chars, b.pages AS body_pages,
       b.page_title, b.fetched_at AS body_fetched_at, b.attachments
FROM items AS i
JOIN municipalities AS m ON m.jis_code = i.jis_code
LEFT JOIN bodies AS b ON b.slug = i.slug AND b.item_id = i.item_id
WHERE i.slug = :slug AND i.item_id = :item_id
"""

ATTACHMENT_SQL = """
SELECT a.url AS attachment_url, a.kind, a.pages, a.chars, a.ref, a.offset, a.fetched_at, a.item_id AS parent
FROM attachments AS a
WHERE a.slug = :slug AND a.url = :url
"""


class MissingTokenizer(RuntimeError):
    pass


def terms_text(value: str) -> str:
    """Sudachi の語を空白でつなぐ。部品が無ければ止まる（会議録・例規と同じ分かち書きにするため）。"""
    if value.strip() == "":
        return ""
    try:
        import japanese_search_tokenizer  # type: ignore
    except Exception as exc:
        raise MissingTokenizer("分かち書きの部品（sudachipy・sudachidict_core）がありません") from exc
    return str(japanese_search_tokenizer.document_terms_text(value)).strip()


def terms_pair(pair: tuple[str, str]) -> tuple[str, str]:
    title, body = pair
    return terms_text(title), terms_text(body)


def read_line(data_root: Path, slug: str, ref: str, offset: int) -> dict | None:
    """索引に控えた位置から 1 行だけ読む。`lib/news_api.php` の読み方と同じ。"""
    if news_index.SLUG_PATTERN.match(slug) is None or not news_index_ref(ref) or offset < 0:
        return None
    path = data_root / slug / ref
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.readline()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def news_index_ref(ref: str) -> bool:
    kind, _, name = ref.partition("/")
    return kind in ("bodies", "attachments") and news_index.YEAR_FILE.match(name) is not None


def item_key(slug: str, item_id: str) -> str:
    return f"news:{slug}:{item_id}"


def attachment_id(slug: str, url: str) -> str:
    return f"news:{slug}:a:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}"


def text_or_none(value: Any) -> str | None:
    text = str(value or "")
    return text if text != "" else None


def base_document(row: sqlite3.Row) -> dict[str, Any]:
    """項目の文書と添付の文書に共通の欄。"""
    categories = []
    try:
        categories = [str(value) for value in json.loads(row["categories"] or "[]")]
    except ValueError:
        pass
    doc: dict[str, Any] = {
        "item_key": item_key(row["slug"], row["item_id"]),
        "slug": row["slug"],
        "municipality_code": row["jis_code"],
        "pref_code": row["pref_code"],
        "pref_name": row["pref_name"],
        "municipality_name": row["municipality_name"],
        "title": row["title"],
        "url": row["url"],
        "categories": categories,
        "sort_date": row["sort_date"],
        "first_seen_at": row["first_seen_at"],
        "hidden": bool(row["hidden"]),
    }
    for key in ("summary", "date", "event_start", "event_end"):
        if row[key]:
            doc[key] = row[key]
    return doc


def prepare_item(conn: sqlite3.Connection, data_root: Path, slug: str, item_id: str) -> tuple[str, dict] | None:
    row = conn.execute(ITEM_SQL, {"slug": slug, "item_id": item_id}).fetchone()
    if row is None:
        return None
    body = ""
    if (row["body_chars"] or 0) > 0:
        line = read_line(data_root, slug, str(row["body_ref"]), int(row["body_offset"]))
        # 記録のファイルが作り直されて行がずれていたら、ほかの項目の本文を入れない。
        if line is not None and str(line.get("id") or "") == item_id:
            body = str(line.get("text") or "")
    doc = base_document(row)
    doc.update({
        "part": "item",
        "body": body,
        "body_length": len(body),
        "kind": row["body_kind"],
        "page_title": row["page_title"],
        "pages": row["body_pages"],
        "ref": row["body_ref"],
        "offset": row["body_offset"],
        "fetched_at": row["body_fetched_at"],
    })
    return item_key(slug, item_id), {key: value for key, value in doc.items() if value is not None}


def prepare_attachment(conn: sqlite3.Connection, data_root: Path, slug: str, url: str) -> tuple[str, dict] | None:
    found = conn.execute(ATTACHMENT_SQL, {"slug": slug, "url": url}).fetchone()
    if found is None or not found["parent"]:
        return None
    row = conn.execute(ITEM_SQL, {"slug": slug, "item_id": found["parent"]}).fetchone()
    if row is None or (found["chars"] or 0) <= 0:
        return None
    line = read_line(data_root, slug, str(found["ref"]), int(found["offset"]))
    if line is None or str(line.get("url") or "") != url:
        return None
    body = str(line.get("text") or "")
    if body.strip() == "":
        return None
    link_text = ""
    try:
        for link in json.loads(row["attachments"] or "[]"):
            if str(link.get("url")) == url:
                link_text = str(link.get("text") or "")
                break
    except (ValueError, AttributeError):
        pass
    doc = base_document(row)
    doc.update({
        "part": "attachment",
        "attachment_url": url,
        "body": body,
        "body_length": len(body),
        "link_text": text_or_none(link_text),
        "kind": found["kind"],
        "pages": found["pages"],
        "ref": found["ref"],
        "offset": found["offset"],
        "fetched_at": found["fetched_at"],
    })
    return attachment_id(slug, url), {key: value for key, value in doc.items() if value is not None}


def prepare(conn: sqlite3.Connection, data_root: Path, kind: str, slug: str, key: str) -> tuple[str, dict] | None:
    if kind == "item":
        return prepare_item(conn, data_root, slug, key)
    if kind == "attachment":
        return prepare_attachment(conn, data_root, slug, key)
    return None


def add_terms(doc: dict, pair: tuple[str, str]) -> dict:
    title_terms, body_terms = pair
    return {**doc, "title_terms": title_terms, "body_terms": body_terms}


def term_sources(doc: dict) -> tuple[str, str]:
    """分かち書きする文字。本文のほうには、概要・リンクの文字・ページの題名も入れる。"""
    extra = [str(doc.get(key) or "") for key in ("summary", "link_text", "page_title")]
    return str(doc.get("title") or ""), "\n".join(part for part in [*extra, str(doc.get("body") or "")] if part)


def bulk_line(index: str, doc_id: str, doc: dict) -> bytes:
    action = json.dumps({"index": {"_index": index, "_id": doc_id}}, ensure_ascii=False, separators=(",", ":"))
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    return (action + "\n" + body).encode("utf-8")


def batches(rows: Iterator[tuple], prepared: Callable[[tuple], tuple[str, dict] | None],
            ) -> Iterator[list[tuple[tuple, str | None, dict | None]]]:
    """行を、文書の数（BULK_DOCS）と文字数（BULK_CHARS）でまとめる。作れない行も（印を消すために）返す。"""
    batch: list[tuple[tuple, str | None, dict | None]] = []
    chars = 0
    for row in rows:
        found = prepared(row)
        doc_id, doc = found if found is not None else (None, None)
        batch.append((row, doc_id, doc))
        chars += doc["body_length"] if doc else 0
        if len(batch) >= BULK_DOCS or chars >= BULK_CHARS:
            yield batch
            batch, chars = [], 0
    if batch:
        yield batch


class Indexer:
    """文書に語を足し、まとめて索引に入れる。"""

    def __init__(self, client: OpenSearchClient, index: str, pool: ProcessPoolExecutor | None = None,
                 terms: Callable[[tuple[str, str]], tuple[str, str]] = terms_pair) -> None:
        self.client = client
        self.index = index
        self.pool = pool
        self.terms = terms

    def put(self, documents: list[tuple[str, dict]]) -> int:
        sources = [term_sources(doc) for _, doc in documents]
        pairs = (list(self.pool.map(self.terms, sources, chunksize=4)) if self.pool is not None
                 else [self.terms(pair) for pair in sources])
        lines: list[bytes] = []
        for (doc_id, doc), pair in zip(documents, pairs):
            lines.append(bulk_line(self.index, doc_id, add_terms(doc, pair)))
        return self.client.bulk_lines(lines, len(lines))


def aliased_indices(client: OpenSearchClient, alias: str = ALIAS) -> list[str]:
    try:
        found = client.request("GET", f"/_alias/{alias}")
    except OpenSearchRequestError as exc:
        if exc.status == 404:
            return []
        raise
    return sorted(found) if isinstance(found, dict) else []


def document_count(client: OpenSearchClient, index: str) -> int:
    return int(client.request("GET", f"/{index}/_count").get("count") or 0)


def dirty_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM search_dirty").fetchone()[0])


def rebuild_running(conn: sqlite3.Connection, now: float | None = None) -> bool:
    """作り直し中か。落ちて残った印は、1 日たったら見なさない。"""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (REBUILD_MARK,)).fetchone()
    if row is None:
        return False
    try:
        started = float(row[0])
    except ValueError:
        return False
    return (now if now is not None else time.time()) - started < REBUILD_STALE_SECONDS


def mark_rebuild(conn: sqlite3.Connection, running: bool) -> None:
    with conn:
        if running:
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (REBUILD_MARK, str(time.time())))
        else:
            conn.execute("DELETE FROM meta WHERE key = ?", (REBUILD_MARK,))


def sync(conn: sqlite3.Connection, data_root: Path, client: OpenSearchClient, *, alias: str = ALIAS,
         budget_seconds: float = BUDGET_SECONDS, terms: Callable[[tuple[str, str]], tuple[str, str]] = terms_pair,
         clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """印の付いた文書を、新しい日付から時間の許すだけ索引に入れる。索引がまだ無ければ何もしない。"""
    started = clock()
    conn.row_factory = sqlite3.Row
    conn.executescript(news_index.SEARCH_SCHEMA)
    if not aliased_indices(client, alias):
        return {"search_skipped": f"{alias} がまだ無い", "search_remaining": dirty_count(conn)}
    if rebuild_running(conn):
        return {"search_skipped": "索引を作り直している", "search_remaining": dirty_count(conn)}
    indexer = Indexer(client, alias, terms=terms)
    indexed = 0
    dropped = 0
    last_commit = clock()
    out_of_time = False
    while True:
        rows = conn.execute("SELECT kind, slug, key, seq FROM search_dirty ORDER BY prio DESC, kind, slug, key LIMIT 200"
                            ).fetchall()
        if not rows:
            break
        done = False
        for batch in batches(iter(tuple(row) for row in rows),
                             lambda row: prepare(conn, data_root, row[0], row[1], row[2])):
            documents = [(doc_id, doc) for _, doc_id, doc in batch if doc_id is not None and doc is not None]
            if documents:
                indexed += indexer.put(documents)
            dropped += len(batch) - len(documents)
            # 入れ終わってから印を消す。読んでから消すまでに付け直された印（seq が違う）は残す。
            conn.executemany("DELETE FROM search_dirty WHERE kind = ? AND slug = ? AND key = ? AND seq = ?",
                             [row for row, _, _ in batch])
            if clock() - last_commit >= COMMIT_SECONDS:
                conn.commit()
                last_commit = clock()
            if clock() - started >= budget_seconds:
                out_of_time = done = True
                break
        if done:
            break
    conn.commit()
    return {
        "search_indexed": indexed,
        "search_dropped": dropped,
        "search_remaining": dirty_count(conn),
        "search_out_of_time": out_of_time,
        "search_seconds": round(clock() - started, 1),
    }


def all_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """作り直しで入れるものの鍵。添付は置き場のファイルの並び順に読む（HDD の上を行き来しないため）。

    先に全部読み取って、読み取りを閉じる。何時間も開いたままにすると、取得の worker が SQLite に書けなくなる。
    """
    keys = [("item", row[0], row[1])
            for row in conn.execute("SELECT slug, item_id FROM items ORDER BY slug, item_id").fetchall()]
    keys += [("attachment", row[0], row[1])
             for row in conn.execute("SELECT slug, url FROM attachments WHERE chars > 0 "
                                     "ORDER BY slug, ref, offset").fetchall()]
    return keys


def rebuild(conn: sqlite3.Connection, data_root: Path, client: OpenSearchClient, *, alias: str = ALIAS,
            build_id: str = "", workers: int = 1, allow_shrink: bool = False,
            terms: Callable[[tuple[str, str]], tuple[str, str]] = terms_pair,
            log: Callable[[str], None] = lambda message: print(message, file=sys.stderr, flush=True)) -> dict[str, Any]:
    """新しい世代を一から作り、全部入ったあとに別名を切り替える。"""
    started = time.monotonic()
    conn.row_factory = sqlite3.Row
    conn.executescript(news_index.SEARCH_SCHEMA)
    previous = aliased_indices(client, alias)
    index = INDEX_PREFIX + (build_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    if index in previous:
        raise RuntimeError(f"{index} は公開中です。build_id を変えてください")
    if rebuild_running(conn):
        raise RuntimeError("ほかの作り直しが動いています（落ちた残りなら、1 日たてば始められます）")
    # 作り直しの前にあった印は、ここで入れる分に含まれる。途中で付け直された印は残す。
    before = {(row["kind"], row["slug"], row["key"]): row["seq"]
              for row in conn.execute("SELECT kind, slug, key, seq FROM search_dirty").fetchall()}
    mark_rebuild(conn, True)
    try:
        return _rebuild(conn, data_root, client, index=index, previous=previous, before=before, alias=alias,
                        workers=workers, allow_shrink=allow_shrink, terms=terms, log=log, started=started)
    finally:
        mark_rebuild(conn, False)


def _rebuild(conn: sqlite3.Connection, data_root: Path, client: OpenSearchClient, *, index: str, previous: list[str],
             before: dict[tuple[str, str, str], int], alias: str, workers: int, allow_shrink: bool,
             terms: Callable[[tuple[str, str]], tuple[str, str]], log: Callable[[str], None],
             started: float) -> dict[str, Any]:
    settings = load_index_settings(shards=1, replicas=0, refresh_interval="-1")
    client.request("PUT", f"/{index}", body={"settings": settings, "mappings": MAPPING})
    indexed = 0
    dropped = 0
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        indexer = Indexer(client, index, pool, terms)
        next_report = 5000
        for batch in batches(iter(all_rows(conn)), lambda row: prepare(conn, data_root, row[0], row[1], row[2])):
            documents = [(doc_id, doc) for _, doc_id, doc in batch if doc_id is not None and doc is not None]
            dropped += len(batch) - len(documents)
            if documents:
                indexed += indexer.put(documents)
            if indexed >= next_report:
                log(f"{index}: {indexed} 件 {round(time.monotonic() - started)} 秒")
                next_report += 5000
        client.request("POST", f"/{index}/_refresh")
        client.request("PUT", f"/{index}/_settings", body={"index": {"refresh_interval": REFRESH_INTERVAL}})
        count = document_count(client, index)
        if previous and not allow_shrink:
            was = document_count(client, alias)
            if count * 2 < was:
                raise RuntimeError(f"件数が {was} から {count} に減るので、切り替えない（確かめたうえで --allow-shrink）")
        actions: list[dict[str, Any]] = [{"add": {"index": index, "alias": alias}}]
        actions += [{"remove": {"index": name, "alias": alias}} for name in previous]
        client.request("POST", "/_aliases", body={"actions": actions})
    except BaseException:
        # 公開していない作りかけの世代だけを片付ける。切り替えに失敗したときも、別名には載っていない。
        try:
            if index not in aliased_indices(client, alias):
                client.request("DELETE", f"/{index}")
        except Exception as exc:
            log(f"作りかけの {index} を片付けられませんでした: {exc}")
        raise
    finally:
        if pool is not None:
            pool.shutdown(cancel_futures=True)
    conn.executemany("DELETE FROM search_dirty WHERE kind = ? AND slug = ? AND key = ? AND seq = ?",
                     [(kind, slug, key, seq) for (kind, slug, key), seq in before.items()])
    conn.commit()
    return {
        "index": index,
        "documents": indexed,
        "dropped": dropped,
        "count": count,
        "previous": previous,
        "seconds": round(time.monotonic() - started, 1),
    }


def client_from_env(args: argparse.Namespace | None = None) -> OpenSearchClient:
    return OpenSearchClient(
        (args.opensearch_url if args else os.environ.get("OPENSEARCH_URL", "http://localhost:9200")),
        user=os.environ.get("OPENSEARCH_USER", ""),
        password=os.environ.get("OPENSEARCH_PASSWORD", ""),
        insecure_dev=os.environ.get("OPENSEARCH_INSECURE_DEV", "").lower() in {"1", "true", "yes", "on"},
    )


def index_path(work_root: Path) -> Path:
    return work_root / news_index.INDEX_NAME


def run_sync(*, data_root: Path = DEFAULT_DATA_ROOT, work_root: Path = DEFAULT_WORK_ROOT,
             budget_seconds: float = BUDGET_SECONDS, client: OpenSearchClient | None = None,
             alias: str | None = None) -> dict[str, Any]:
    """取得の回から呼ぶ。索引（SQLite）や OpenSearch が無い・届かないときは、何をしたかを返す。"""
    path = index_path(work_root)
    if not path.is_file():
        return {"search_skipped": "お知らせの索引がまだ無い"}
    conn = news_index.connect(path)
    try:
        return sync(conn, data_root, client or client_from_env(), budget_seconds=budget_seconds,
                    alias=alias or os.environ.get("MIYABE_NEWS_ALIAS", "") or ALIAS)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="お知らせの全文検索の索引（OpenSearch）")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--opensearch-url", default=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"))
    parser.add_argument("--alias", default=os.environ.get("MIYABE_NEWS_ALIAS", "") or ALIAS)
    parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
    parser.add_argument("--rebuild", action="store_true", help="新しい世代を一から作って別名を切り替える")
    parser.add_argument("--workers", type=int, default=1, help="分かち書きの並列の数（--rebuild のとき）")
    parser.add_argument("--build-id", default="", help="世代名の後ろ。既定は UTC の日時")
    parser.add_argument("--allow-shrink", action="store_true", help="件数が前の半分に満たなくても切り替える")
    parser.add_argument("--clear-rebuild-mark", action="store_true",
                        help="作り直しが落ちて残った印を消す（取得の回の更新が止まっているとき。作り直しが動いていないのを確かめてから）")
    args = parser.parse_args(argv)
    path = index_path(args.work_root)
    if not path.is_file():
        print("お知らせの索引がまだありません（tools/news/news_index.py）", file=sys.stderr)
        return 1
    conn = news_index.connect(path)
    try:
        if args.clear_rebuild_mark:
            mark_rebuild(conn, False)
            print(json.dumps({"rebuild_mark": "cleared"}))
            return 0
        client = client_from_env(args)
        if args.rebuild:
            result = rebuild(conn, args.data_root, client, alias=args.alias, build_id=args.build_id,
                             workers=max(1, args.workers), allow_shrink=args.allow_shrink)
        else:
            result = sync(conn, args.data_root, client, alias=args.alias, budget_seconds=args.budget_seconds)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
