"""自治体の新着情報のフィード（RSS 1.0・RSS 2.0・Atom）を読む部品。

取得はしない。取った中身（バイト列）を項目の一覧にする。全国の調査
（`doc/news-feed-survey.md`）で見た形に合わせてある。

- 文字コードは XML 宣言を先に見る。ElementTree は Shift_JIS・EUC-JP の宣言を
  読めないので、文字列にしてから渡す。
- 素の `&` や `&nbsp;` のような HTML の名前付き文字で XML として壊れている
  フィードがあるので、読めなければ直して読み直す。
- 日付は `dc:date`・`pubDate`・Atom の `published`/`updated` を読み、日本時間に
  そろえる。時差の書いていない日時は日本時間と見なす。
- 分類は `category`・`dc:subject`、一部の CMS の `category01〜03` を集める。
  催しの開始・終了日（`event_sdate`・`event_edate`）も読む。
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

JST = timezone(timedelta(hours=9))

# 概要として残す長さ。本文は後の段でリンク先から取る。
SUMMARY_MAX_CHARS = 1000

ENCODING_ALIASES = {
    "shift-jis": "cp932",
    "shift_jis": "cp932",
    "sjis": "cp932",
    "x-sjis": "cp932",
    "windows-31j": "cp932",
    "euc-jp": "euc_jp",
    "x-euc-jp": "euc_jp",
}

XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>")
XML_ENCODING_RE = re.compile(rb"<\?xml[^>]*encoding\s*=\s*[\"']([\w.:-]+)[\"']", re.I)
CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.I)
# XML では使えない制御文字。混ざっていると全体が読めなくなる。
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
TAG_RE = re.compile(r"<[^>]+>")
# 英数字の直後に始まる 2 つ目の http(s)://。`?url=https://` のような正しい形は拾わない。
EMBEDDED_URL_RE = re.compile(r"[A-Za-z0-9]https?://")
SPACE_RE = re.compile(r"\s+")
JP_DATE_RE = re.compile(
    r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"
    r"(?:[\sT]*(\d{1,2})[:時](\d{1,2})(?:[:分](\d{1,2}))?)?"
)

COMPACT_RFC822_RE = re.compile(
    r"^[A-Za-z]{3},?\s*(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*([+-]\d{2}:?\d{2}|GMT|UTC)?\s*$"
)

# 項目の日付として先に見る欄。`updated`・`modified` は最後の手段。
DATE_FIELDS = ("pubdate", "date", "published", "issued", "created", "updated", "modified")
SUMMARY_FIELDS = ("description", "summary")
CONTENT_FIELDS = ("encoded", "content")
CATEGORY_FIELDS = ("category", "subject", "category01", "category02", "category03")


class FeedError(ValueError):
    """フィードとして読めない中身。"""


@dataclass
class FeedItem:
    title: str
    url: str
    published_at: str = ""  # 日本時間の ISO 8601。読めなければ空
    date: str = ""  # 日本時間の YYYY-MM-DD
    date_raw: str = ""
    summary: str = ""
    categories: list[str] = field(default_factory=list)
    event_start: str = ""
    event_end: str = ""
    guid: str = ""

    def item_id(self, feed_url: str = "") -> str:
        """項目を見分ける印。リンクがあればリンクだけで決める。"""
        if self.url:
            key = self.url
        else:
            key = "\n".join((feed_url, self.guid or self.title, self.date_raw))
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def fingerprint(self) -> str:
        """同じ項目の題名か日付が変わったかを見る印。概要の変化は数えない。"""
        key = "\n".join((self.title, self.published_at or self.date_raw))
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


@dataclass
class ParsedFeed:
    format: str  # rss1 / rss2 / atom
    title: str
    items: list[FeedItem]

    @property
    def newest(self) -> str:
        dates = [item.date for item in self.items if item.date]
        return max(dates) if dates else ""


# ─────────────────────────────────────
# 文字
# ─────────────────────────────────────

def _codec(name: str) -> str:
    name = name.strip().lower()
    return ENCODING_ALIASES.get(name, name)


def decode_feed(raw: bytes, content_type: str = "") -> str:
    """フィードのバイト列を文字列にする。XML 宣言・Content-Type・UTF-8 の順に見る。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    candidates: list[str] = []
    match = XML_ENCODING_RE.search(raw[:400])
    if match:
        candidates.append(_codec(match.group(1).decode("ascii", "ignore")))
    match = CHARSET_RE.search(content_type or "")
    if match:
        candidates.append(_codec(match.group(1)))
    candidates.append("utf-8")
    for codec in candidates:
        try:
            text = raw.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
        return text
    # 宣言と中身が食い違うとき。化けの少ない方を採る。
    best = ""
    best_bad = -1
    for codec in dict.fromkeys(candidates + ["cp932", "euc_jp"]):
        try:
            text = raw.decode(codec, "replace")
        except LookupError:
            continue
        bad = text.count("�")
        if best_bad < 0 or bad < best_bad:
            best, best_bad = text, bad
    return best


def clean_text(value: str, limit: int = 0) -> str:
    """HTML の印を外し、文字参照を戻し、空白をそろえる。"""
    text = html.unescape(TAG_RE.sub(" ", html.unescape(value or "")))
    text = SPACE_RE.sub(" ", text.replace("　", " ")).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


# ─────────────────────────────────────
# 日付
# ─────────────────────────────────────

def parse_date(value: str) -> datetime | None:
    """フィードの日付を日本時間の datetime にする。読めなければ None。"""
    text = (value or "").strip()
    if not text:
        return None
    parsed: datetime | None = None
    if re.match(r"^[A-Za-z]{3},?\s", text) or re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", text):
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            parsed = None
    if parsed is None:
        # 空白の抜けた RFC 822（`tue, 10Nov202614:00:00 +09:00`）。
        match = COMPACT_RFC822_RE.match(text)
        if match:
            day, month, year, clock, zone = match.groups()
            spaced = f"{day} {month.title()} {year} {clock} {zone.replace(':', '') if zone else ''}".strip()
            try:
                parsed = parsedate_to_datetime(spaced)
            except (TypeError, ValueError, IndexError):
                parsed = None
    if parsed is None:
        iso = text.replace("Z", "+00:00").replace("/", "-")
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            parsed = None
    if parsed is None:
        match = JP_DATE_RE.search(text)
        if not match:
            return None
        year, month, day, hour, minute, second = match.groups()
        try:
            parsed = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


# ─────────────────────────────────────
# XML
# ─────────────────────────────────────

def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _attr(element: ElementTree.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local(key) == name:
            return str(value).strip()
    return ""


def looks_like_feed(raw: bytes) -> bool:
    head = raw[:1000].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return False
    return any(mark in head for mark in (b"<rss", b"<feed", b"<rdf:rdf", b"<rdf "))


def _parse_xml(text: str) -> ElementTree.Element:
    text = CONTROL_RE.sub("", XML_DECL_RE.sub("", text.lstrip("﻿"), count=1))
    try:
        return ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError):
        pass
    # 素の & や &nbsp; で壊れているもの。&amp; にしておけば、題名を読むときに戻る。
    try:
        return ElementTree.fromstring(BARE_AMP_RE.sub("&amp;", text))
    except (ElementTree.ParseError, ValueError) as exc:
        raise FeedError(f"XML として読めません: {exc}") from exc


def _absolute(url: str, base: str) -> str:
    url = html.unescape((url or "").strip())
    if not url:
        return ""
    # サイトの URL の後ろに外部の URL をつないでしまうフィードがある
    # （`https://www.city.example.jphttps://www.meti.go.jp/...`）。後ろの方が本物。
    embedded = EMBEDDED_URL_RE.search(url, 1)
    if embedded:
        url = url[embedded.start() + 1:]
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _item_link(element: ElementTree.Element, children: list[ElementTree.Element], base: str) -> str:
    links = [child for child in children if _local(child.tag) == "link"]
    for link in links:
        href = link.get("href")
        if href is not None:
            if (link.get("rel") or "alternate") == "alternate":
                found = _absolute(href, base)
                if found:
                    return found
            continue
        found = _absolute(_text(link), base)
        if found:
            return found
    # RSS 1.0 は rdf:about に、RSS 2.0 は guid に入っていることがある。
    found = _absolute(_attr(element, "about"), base)
    if found:
        return found
    for child in children:
        if _local(child.tag) == "guid" and (child.get("isPermaLink") or "true").lower() != "false":
            found = _absolute(_text(child), "")
            if found:
                return found
    for child in children:
        if _local(child.tag) == "id":
            found = _absolute(_text(child), "")
            if found:
                return found
    return ""


def _read_item(element: ElementTree.Element, base: str) -> FeedItem:
    children = list(element)
    first: dict[str, ElementTree.Element] = {}
    for child in children:
        first.setdefault(_local(child.tag), child)

    date_raw = ""
    when: datetime | None = None
    for name in DATE_FIELDS:
        if name in first:
            date_raw = _text(first[name])
            when = parse_date(date_raw)
            if when is not None:
                break

    summary = ""
    for name in SUMMARY_FIELDS + CONTENT_FIELDS:
        if name in first:
            summary = clean_text(_text(first[name]), SUMMARY_MAX_CHARS)
            if summary:
                break

    categories: list[str] = []
    for child in children:
        if _local(child.tag) not in CATEGORY_FIELDS:
            continue
        value = clean_text(_text(child) or child.get("label") or child.get("term") or "")
        if value and value not in categories:
            categories.append(value)

    event_start = parse_date(_text(first.get("event_sdate")))
    event_end = parse_date(_text(first.get("event_edate")))
    guid_element = first.get("guid") if "guid" in first else first.get("id")
    return FeedItem(
        title=clean_text(_text(first.get("title")), 500),
        url=_item_link(element, children, base),
        published_at=when.isoformat() if when else "",
        date=when.date().isoformat() if when else "",
        date_raw=date_raw[:80],
        summary=summary,
        categories=categories[:12],
        event_start=event_start.date().isoformat() if event_start else "",
        event_end=event_end.date().isoformat() if event_end else "",
        guid=_text(guid_element)[:300],
    )


def parse_feed(raw: bytes, *, content_type: str = "", base_url: str = "") -> ParsedFeed:
    """フィードを読む。フィードでなければ `FeedError`。"""
    if not looks_like_feed(raw):
        raise FeedError("フィードではありません（HTML か空）")
    root = _parse_xml(decode_feed(raw, content_type))
    kind = _local(root.tag)
    if kind not in ("rss", "rdf", "feed"):
        raise FeedError(f"知らない形です: {kind}")
    feed_format = {"rss": "rss2", "rdf": "rss1", "feed": "atom"}[kind]

    title = ""
    for element in root.iter():
        name = _local(element.tag)
        if name == "title":
            title = clean_text(_text(element), 200)
            break
        if name in ("item", "entry"):
            break

    items = [
        _read_item(element, base_url)
        for element in root.iter()
        if _local(element.tag) in ("item", "entry")
    ]
    items = [item for item in items if item.title or item.url]
    return ParsedFeed(format=feed_format, title=title, items=items)
