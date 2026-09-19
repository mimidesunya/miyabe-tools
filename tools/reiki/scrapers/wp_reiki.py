#!/usr/bin/env python3
"""WordPress の投稿タイプ `reiki` で公開している例規集を取得する。

日吉津村（https://www.hiezu.jp/reiki/）が該当する。1 例規 1 投稿で、本文は
ぎょうせい系の例規 HTML（p.date / p.number / div.eline）をそのまま貼ったもの。

- 目録: WordPress REST API `/wp-json/wp/v2/reiki` を per_page=100 でページ送りする
  （サイトマップも同じ件数を返すが、REST なら総数 X-WP-Total も分かる）
- 本文: 投稿の公開ページ。`div.reiki-body-content` の中に本文、
  `<!-- REIKI_CAT: 第1編総規 第1章村制 -->` に体系がある

取得・正規化・manifest・進捗・レジュームの共通処理は static_catalog に委ねる。
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import reiki_targets  # noqa: E402
import static_catalog  # noqa: E402
from static_catalog import Article, ParsedArticle  # noqa: E402


PER_PAGE = 100
CATEGORY_RE = re.compile(r"<!--\s*REIKI_CAT:\s*(.*?)\s*-->")


def _site_root(source_url: str) -> str:
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/", "", ""))


def discover(session, source_url: str) -> list[Article]:
    api = _site_root(source_url) + "wp-json/wp/v2/reiki"
    articles: list[Article] = []
    seen: set[str] = set()
    page = 1
    total_pages = 1
    declared = 0
    while page <= total_pages:
        response = session.get(
            api,
            params={"per_page": PER_PAGE, "page": page, "_fields": "id,slug,link,title", "orderby": "id", "order": "asc"},
            headers={"User-Agent": static_catalog.USER_AGENT},
            timeout=static_catalog.TIMEOUT,
        )
        response.raise_for_status()
        total_pages = int(response.headers.get("X-WP-TotalPages") or 1)
        declared = int(response.headers.get("X-WP-Total") or 0)
        for item in response.json():
            code = str(item.get("slug") or item.get("id") or "").strip()
            link = str(item.get("link") or "").strip()
            if not code or not link or code in seen:
                continue
            seen.add(code)
            title = html_lib.unescape(str((item.get("title") or {}).get("rendered") or "")).strip()
            articles.append(Article(code=code, url=link, title=title))
        page += 1
    if declared and len(articles) != declared:
        print(f"[WARN] REST の総数 {declared} 件に対し {len(articles)} 件しか並ばなかった", flush=True)
    return articles


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el is not None else ""


def parse_article(raw: str, url: str) -> ParsedArticle | None:
    soup = BeautifulSoup(raw, "html.parser")
    body = soup.find("div", class_="reiki-body-content") or soup.find(id="primaryInner2")
    if body is None:
        return None
    title = _text(soup.find("h1", class_="reiki-main-title"))
    if not title:
        title = _text(body.find("p", class_=re.compile(r"^title")))
    title = title.lstrip("○ ").strip()
    if not title:
        return None

    date_text = static_catalog.extract_wareki(_text(body.find("p", class_="date")))
    number = _text(body.find("p", class_="number"))
    match = CATEGORY_RE.search(raw)
    taxonomy = re.sub(r"\s+", " > ", match.group(1).strip()) if match else ""

    return ParsedArticle(
        title=title,
        content_html=body.decode_contents(),
        date_text=date_text,
        number=number,
        taxonomy_path=taxonomy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download ordinances published as WordPress 'reiki' posts.")
    parser.add_argument("--slug", default="")
    parser.add_argument("--system-type", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    slug = args.slug.strip() or reiki_targets.default_slug_for_system("wp-reiki")
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
