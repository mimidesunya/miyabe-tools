#!/usr/bin/env python3
"""joureikun / legalcrud 形式の例規集を取得する。

両者は同一エンジン（crestec joureikun, 例規くん）で、URL は別ドメインだが
HTML 構造は共通:
- 目次: {base}/aggregate/catalog/index.html が個別例規 ../../act/{id}.html を列挙
- 本文: {base}/act/{id}.html に <div class="mainTitle">/<div class="date">/
        <div class="contentWrap"> を持つ

共通パイプライン（取得・正規化・manifest・進捗・レジューム）は
static_catalog に委ね、ここでは目次の辿り方と本文の取り出し方だけを与える。
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


ACT_HREF_RE = re.compile(r"""["']([^"']*act/[^"'/]+\.html?)["']""", re.IGNORECASE)


def _catalog_url(source_url: str) -> str:
    base = source_url if source_url.endswith("/") else source_url + "/"
    return urljoin(base, "aggregate/catalog/index.html")


def discover(session, source_url: str) -> list[Article]:
    catalog_url = _catalog_url(source_url)
    html = static_catalog.fetch_text(session, catalog_url, referer=source_url)
    if html is None:
        raise RuntimeError(f"failed to fetch catalog: {catalog_url}")
    articles: list[Article] = []
    seen: set[str] = set()
    for href in ACT_HREF_RE.findall(html):
        detail_url = urljoin(catalog_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)
        code = Path(detail_url.split("?", 1)[0]).stem
        articles.append(Article(code=code, url=detail_url))
    return articles


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el is not None else ""


def parse_article(raw: str, url: str) -> ParsedArticle | None:
    soup = BeautifulSoup(raw, "html.parser")
    content_el = soup.find("div", class_="contentWrap")
    if content_el is None:
        return None

    title = _text(soup.find("div", class_="mainTitle"))
    if not title:
        name_el = soup.find(id="name")
        title = _text(name_el).lstrip("○ ").strip()

    raw_date = _text(soup.find("div", class_="date"))
    wareki = static_catalog.extract_wareki(raw_date)
    # div.date は「平成17年8月19日総務省告示第955号」のように公布日＋番号が
    # 連結されている。日付欄には和暦のみ、番号欄には残りを振り分ける。
    if wareki and wareki in raw_date:
        date_text = wareki
        number = raw_date.split(wareki, 1)[1].strip()
    else:
        date_text = raw_date if wareki else ""
        number = "" if wareki else raw_date

    # 体系・分野表示（nav 内）を taxonomy として拾う。
    taxonomy = ""
    taikei = soup.find("div", class_="taikei")
    if taikei is not None:
        taxonomy = " / ".join(
            part for part in (_text(li) for li in taikei.find_all("li")) if part
        )[:500]

    content_html = content_el.decode_contents()
    return ParsedArticle(
        title=title,
        content_html=content_html,
        date_text=date_text,
        number=number,
        taxonomy_path=taxonomy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download ordinances from joureikun/legalcrud systems.")
    parser.add_argument("--slug", default="", help="Municipality slug resolved from data/municipalities")
    parser.add_argument("--system-type", default="", help="joureikun または legalcrud")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="取得件数の上限（試験用）")
    args = parser.parse_args()

    expected_system = args.system_type.strip() or None
    slug = args.slug.strip() or reiki_targets.default_slug_for_system(expected_system or "joureikun")
    # system_type が未指定でも、slug から解決した target の type をそのまま使う。
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
