#!/usr/bin/env python3
"""jourei-v5（crestec 例規くん 旧 v5 HTML 版）の例規集を取得する。

現行 joureikun と同じベンダだが、出力が IE 時代のフレームセット構成:
- 目次: {base}/aggregate/catalog/index.htm はフレームセットで、実体は
        {base}/aggregate/catalog/result/catalog.htm が ../../../act/frame/frame{id}.htm を列挙
- 本文: {base}/act/content/content{id}.htm に titleName/name・公布日表（td align=right）・本文がある
        （frame/head/topic/type の各フレームは UI 用なので content だけ取得すれば足りる）

取得・正規化・manifest・進捗・レジュームの共通処理は static_catalog に委ねる。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import reiki_targets  # noqa: E402
import static_catalog  # noqa: E402
from static_catalog import Article, ParsedArticle  # noqa: E402


FRAME_HREF_RE = re.compile(r"""["'][^"']*act/frame/frame([0-9A-Za-z]+)\.html?["']""", re.IGNORECASE)
DATE_LINE_RE = re.compile(r"[（(]\s*((?:明治|大正|昭和|平成|令和)[^（）()]*?第[0-9０-９]+号)\s*[）)]")


def _base_dir(source_url: str) -> str:
    # source_url は .../JoureiV5HTMLContents/index.htm または .../JoureiV5HTMLContents/
    return source_url if source_url.endswith("/") else source_url.rsplit("/", 1)[0] + "/"


def discover(session, source_url: str) -> list[Article]:
    base = _base_dir(source_url)
    catalog_url = urljoin(base, "aggregate/catalog/result/catalog.htm")
    html = static_catalog.fetch_text(session, catalog_url, referer=source_url)
    if html is None:
        raise RuntimeError(f"failed to fetch catalog: {catalog_url}")
    articles: list[Article] = []
    seen: set[str] = set()
    for act_id in FRAME_HREF_RE.findall(html):
        if act_id in seen:
            continue
        seen.add(act_id)
        detail_url = urljoin(base, f"act/content/content{act_id}.htm")
        articles.append(Article(code=act_id, url=detail_url))
    return articles


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el is not None else ""


def parse_article(raw: str, url: str) -> ParsedArticle | None:
    soup = BeautifulSoup(raw, "html.parser")
    name_el = soup.find(attrs={"name": "name"}) or soup.find(class_="titleName")
    title = _text(name_el).lstrip("○ ").strip()

    body = soup.find("form") or soup.body or soup
    text = body.get_text(" ", strip=True) if body is not None else ""
    date_text = ""
    number = ""
    match = DATE_LINE_RE.search(text)
    if match:
        combined = match.group(1)
        wareki = static_catalog.extract_wareki(combined)
        if wareki and wareki in combined:
            date_text = wareki
            number = combined.split(wareki, 1)[1].strip()
    if not title:
        return None

    content_html = body.decode_contents() if hasattr(body, "decode_contents") else str(body)
    return ParsedArticle(
        title=title,
        content_html=content_html,
        date_text=date_text,
        number=number,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download ordinances from jourei-v5 systems.")
    parser.add_argument("--slug", default="")
    parser.add_argument("--system-type", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    slug = args.slug.strip() or reiki_targets.default_slug_for_system("jourei-v5")
    target = reiki_targets.load_reiki_target(slug)
    return static_catalog.run(
        slug=slug,
        expected_system=str(target["system_type"]),
        discover=discover,
        parse_article=parse_article,
        force=args.force,
        check_updates=args.check_updates,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
