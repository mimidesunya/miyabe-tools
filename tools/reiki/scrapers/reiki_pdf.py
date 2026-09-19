#!/usr/bin/env python3
"""例規集全体を 1 本の PDF で公開している自治体から、例規を 1 件ずつ切り出す。

海士町（3142 ページ）と知夫村（2937 ページ）が該当する。どちらも加除式の例規集を
そのまま PDF にしたもので、しおりに「○海士町公告式条例」のような例規ごとの
見出しが付いている。

- 目録: 登録 URL のページから例規集 PDF を探して落とす（PDF の URL は改版のたびに
  変わるので登録簿には案内ページを置く）。しおりのうち「○」で始まるものが例規。
  「様式第１号」などは直前の例規の一部、「第１編」「第２章」は見出し
- 本文: 例規の開始ページから次の例規の開始ページまで。同じページで次の例規が
  始まるときは、その題名の行の手前で切る
- 公布日・番号: 題名の直後の「（昭和27年４月２日海士町条例第103号）」

取得・正規化・manifest・進捗・レジュームの共通処理は static_catalog に委ねる。
個票は URL の GET ではなく PDF からの切り出しなので fetch を渡す。
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import io
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin

import pypdf

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import reiki_targets  # noqa: E402
import static_catalog  # noqa: E402
from static_catalog import Article, ParsedArticle  # noqa: E402


PDF_TIMEOUT = 300
PDF_LINK_RE = re.compile(r"""href=["']([^"']+?\.pdf)["'][^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL)
PDF_URL_RE = re.compile(r"""https?://[^"'\s<>]+?\.pdf""", re.IGNORECASE)
HEADING_RE = re.compile(r"^第[0-9０-９一二三四五六七八九十百]+[編章節款]\s")
PROMULGATION_RE = re.compile(r"^[（(](.+)[）)]$")
# 新しい段落の頭。PDF の折り返しは段落の途中でも入るので、これ以外は前の行へつなぐ。
PARAGRAPH_START_RE = re.compile(
    r"^(?:[　\s]|[（(]|第[0-9０-９一二三四五六七八九十百]+[条章節款編]|[0-9０-９]+\s|[⑴-⒇]|"
    r"附\s*則|別表|様式|別記|目次|○|備考|"
    # 沿革の注記は「改正（平８条例第７号）」「改正 平成８年…」の形。本文の「改正する。」は続き。
    r"(?:改正|追加|全改|繰上げ|繰下げ|削除)[\s　（(])"
)

# 目録を作ったときに落とした PDF。個票はここから切り出す。
_DOCUMENT: dict[str, object] = {}


def _compact(value: str) -> str:
    return re.sub(r"[\s　]+", "", value or "")


def find_pdf_url(session, source_url: str) -> str:
    if source_url.lower().split("?", 1)[0].endswith(".pdf"):
        return source_url
    raw = static_catalog.fetch_text(session, source_url)
    if raw is None:
        raise RuntimeError(f"failed to fetch page: {source_url}")
    # Nuxt などは URL の「/」を JSON 内でエスケープ（u002F）して埋め込む。
    page = raw.replace("\\u002F", "/")
    candidates: list[tuple[str, str]] = []
    for href, label in PDF_LINK_RE.findall(page):
        candidates.append((urljoin(source_url, html_lib.unescape(href)), re.sub(r"<[^>]+>", "", label)))
    for url in PDF_URL_RE.findall(page):
        candidates.append((html_lib.unescape(url), ""))
    for url, label in candidates:
        if "例規" in label or "例規" in unquote(url):
            return url
    unique = list(dict.fromkeys(url for url, _ in candidates))
    if len(unique) == 1:
        return unique[0]
    raise RuntimeError(f"例規集の PDF が見つからない: {source_url} (候補 {len(unique)} 件)")


class PdfBook:
    """しおりとページの文字。同じページを例規ごとに何度も読むので、行は覚えておく。"""

    def __init__(self, data: bytes):
        self.reader = pypdf.PdfReader(io.BytesIO(data))
        self.page_count = len(self.reader.pages)
        self._lines: dict[int, list[str]] = {}

    def toc(self) -> list[tuple[int, str, int]]:
        found: list[tuple[int, str, int]] = []

        def walk(items, level: int) -> None:
            for item in items:
                if isinstance(item, list):
                    walk(item, level + 1)
                    continue
                try:
                    page = self.reader.get_destination_page_number(item) + 1
                except Exception:
                    page = 0
                found.append((level, str(item.title or ""), page))

        walk(self.reader.outline, 1)
        return found

    def lines(self, page_number: int) -> list[str]:
        if page_number not in self._lines:
            try:
                text = self.reader.pages[page_number - 1].extract_text() or ""
            except Exception as exc:
                print(f"[WARN] page {page_number}: {exc}", flush=True)
                text = ""
            self._lines[page_number] = text.splitlines()
        return self._lines[page_number]


def _page_lines(doc: PdfBook, page_number: int) -> list[str]:
    return doc.lines(page_number)


def _title_line_index(lines: list[str], title: str) -> int:
    """題名が始まる行を返す。題名は 2 行に折り返すことがある。

    廃止編には「固定資産税の課税免除に関する条例…を廃止する規則」のように書き出しの
    同じ題名が並ぶので、まず題名全体で照らす。しおりの題名は本文と字が違うことがある
    （「 」の中身が抜けるなど）ので、全体で見つからなければ書き出しで照らす。
    """
    full = _compact(title)
    prefix = full[:12]
    # 抜けた「 」の手前までで照らす。
    gap = full.find("「」")
    if 0 <= gap < len(prefix):
        prefix = full[: gap + 1]
    for want in (full, prefix):
        for index, line in enumerate(lines):
            if not line.strip().startswith("○"):
                continue
            joined = _compact("".join(lines[index : index + 4]))
            if joined.startswith(want):
                return index
    return -1


def build_entries(doc) -> list[dict]:
    toc = doc.toc()
    starts = [(title.strip(), page) for _level, title, page in toc if title.strip().startswith("○") and page > 0]
    entries = []
    for index, (title, page) in enumerate(starts):
        next_title, next_page = starts[index + 1] if index + 1 < len(starts) else ("", doc.page_count + 1)
        entries.append({"title": title, "start": page, "next_title": next_title, "next_page": next_page})
    return entries


def extract_entry_lines(doc, entry: dict) -> list[str]:
    start = int(entry["start"])
    next_page = int(entry["next_page"])
    last = min(max(start, next_page), doc.page_count)
    lines: list[str] = []
    for page in range(start, last + 1):
        page_lines = _page_lines(doc, page)
        begin = 0
        end = len(page_lines)
        if page == start:
            found = _title_line_index(page_lines, entry["title"])
            begin = found if found >= 0 else 0
        if page == next_page and entry["next_title"]:
            found = _title_line_index(page_lines, entry["next_title"])
            if found < 0 and page != start:
                # 次の例規はこのページの頭から。1 行も要らない。
                end = 0
            elif found >= 0:
                end = found
        elif page == next_page:
            end = 0
        if page == start and page == next_page and end <= begin:
            end = len(page_lines)
        lines.extend(page_lines[begin:end])
    # 次の例規の手前にある「第３章 表彰」のような見出しは、この例規のものではない。
    while lines and (not lines[-1].strip() or HEADING_RE.match(lines[-1].strip())):
        lines.pop()
    return lines


def _paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    # 空行は段落の切れ目。題名・公布日・沿革・本文の間は空行で区切られている。
    after_blank = True
    for line in lines:
        text = line.rstrip()
        if not text.strip():
            after_blank = True
            continue
        joinable = paragraphs and not after_blank and not PARAGRAPH_START_RE.match(text)
        after_blank = False
        if joinable:
            paragraphs[-1] += text.strip()
        else:
            paragraphs.append(text.strip())
    return paragraphs


def discover(session, source_url: str) -> list[Article]:
    pdf_url = find_pdf_url(session, source_url)
    print(f"[INFO] PDF: {pdf_url}", flush=True)
    response = session.get(pdf_url, headers={"User-Agent": static_catalog.USER_AGENT}, timeout=PDF_TIMEOUT)
    response.raise_for_status()
    doc = PdfBook(response.content)
    entries = build_entries(doc)
    _DOCUMENT["doc"] = doc
    _DOCUMENT["entries"] = {}
    articles: list[Article] = []
    for entry in entries:
        # 改版でページはずれるので、題名と公布日・番号から識別子を作る。
        # その次の「改正 …」行は改正のたびに伸びるので入れない。
        head = _paragraphs(extract_entry_lines(doc, entry)[:8])[:2]
        code = hashlib.sha1("|".join(_compact(p) for p in head).encode("utf-8")).hexdigest()[:16]
        url = f"{pdf_url}#page={entry['start']}"
        # 同じページで始まる例規は URL が同じになるので、識別子で引く。
        _DOCUMENT["entries"][code] = entry
        articles.append(Article(code=code, url=url, title=entry["title"].lstrip("○").strip()))
    return articles


def fetch(_session, article: Article) -> str | None:
    doc = _DOCUMENT.get("doc")
    entry = (_DOCUMENT.get("entries") or {}).get(article.code)
    if doc is None or entry is None:
        return None
    paragraphs = _paragraphs(extract_entry_lines(doc, entry))
    if not paragraphs:
        return None
    return "\n".join(f"<p>{html_lib.escape(p, quote=False)}</p>" for p in paragraphs)


def parse_article(raw: str, url: str) -> ParsedArticle | None:
    paragraphs = [html_lib.unescape(p) for p in re.findall(r"<p>(.*?)</p>", raw, re.DOTALL)]
    if not paragraphs or not paragraphs[0].startswith("○"):
        return None
    title = paragraphs[0].lstrip("○").strip()
    body_start = 1
    date_text = ""
    number = ""
    if len(paragraphs) > 1:
        match = PROMULGATION_RE.match(paragraphs[1].strip())
        if match:
            body_start = 2
            inner = match.group(1)
            date_text = static_catalog.extract_wareki(inner)
            number = inner.split(date_text, 1)[1].strip() if date_text and date_text in inner else inner.strip()
    content = "\n".join(f"<p>{html_lib.escape(p, quote=False)}</p>" for p in paragraphs[body_start:])
    if not content:
        return None
    return ParsedArticle(title=title, content_html=content, date_text=date_text, number=number)


def main() -> int:
    parser = argparse.ArgumentParser(description="Split a single-PDF ordinance book into ordinances.")
    parser.add_argument("--slug", default="")
    parser.add_argument("--system-type", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    slug = args.slug.strip() or reiki_targets.default_slug_for_system("reiki-pdf")
    target = reiki_targets.load_reiki_target(slug)
    return static_catalog.run(
        slug=slug,
        expected_system=str(target["system_type"]),
        discover=discover,
        parse_article=parse_article,
        fetch=fetch,
        force=args.force,
        check_updates=args.check_updates,
        limit=args.limit,
        # 手元の PDF から切り出すだけで、取得元へは 1 回しか行かない。
        delay=0,
    )


if __name__ == "__main__":
    raise SystemExit(main())
