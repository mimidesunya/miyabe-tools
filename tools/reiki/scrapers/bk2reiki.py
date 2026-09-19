#!/usr/bin/env python3
"""bk2reiki（東京法令出版「例規集検索システム」）の例規集を取得する。

西宮市（https://regulations.bk2reiki.net/）が該当する。トップにユーザーID・
パスワード欄があるが、閲覧者向けには公開用の共通 ID（internet / internet）を
ページ自身が POST して自動で検索画面へ入る。職員用の欄を使うわけではない。
市の案内ページ（nishi.or.jp の「西宮市の条例・規則」）も誰でも閲覧できる例規集
として案内している。

- 入口: `menu/menu.php` へ bk2_userid=internet / bk2_passwd=internet を POST
  （このセッションが無いと本文は 500 になる）
- 目録: `menu/search1_result.php` を条件空（現行のみ = hani50=50）で POST し、
  searchpos を 10 件ずつ進める。ヒットが無くなったら終わり。総数は #sea1_count
- 本文: `doc/docopen_honbun.php?typ=&file=&date=`。`div#honbun` に h1 と
  `div.seitei`（（昭和４５年１１月３日）（西宮市告示甲第９４号））がある

取得・正規化・manifest・進捗・レジュームの共通処理は static_catalog に委ねる。
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import reiki_targets  # noqa: E402
import static_catalog  # noqa: E402
from static_catalog import Article, ParsedArticle  # noqa: E402


PAGE_SIZE = 10
PUBLIC_LOGIN = {"bk2_flg": "0", "bk2_userid": "internet", "bk2_passwd": "internet"}
SEARCH_FIELDS = (
    "hyodai=&yogo=&hani3=&hani50=50&shuri=&kozo=&rirekiflg=1"
    "&symd1=&symd2=&shani=1&sno1=&sno2=&sno3="
    "&kymd1=&kymd2=&khani=1&kno1=&kno2=&kno3="
    "&eymd1=&eymd2=&ehani=1&eno1=&eno2=&eno3=&searchpos={pos}"
)
ITEM_RE = re.compile(
    r'<div id="ditem[^"]*"[^>]*>.*?href="\.\./doc/docopen\.php\?([^"]+)"[^>]*>(?:<img[^>]*>)?(.*?)</a>'
    r'(.*?)</p>(?:<p class="bk2_SearchMokuji">(.*?)</p>)?',
    re.DOTALL,
)
COUNT_RE = re.compile(r'#sea1_count"\)\.text\("(\d+)"\)')

# 目録で拾った体系。本文ページには体系が無い。
_TAXONOMY: dict[str, str] = {}
_BASE: dict[str, str] = {}


def _base(source_url: str) -> str:
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/", "", ""))


def _plain(value: str) -> str:
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _login(session, base: str) -> None:
    headers = {"User-Agent": static_catalog.USER_AGENT, "Referer": base}
    session.get(base, headers=headers, timeout=static_catalog.TIMEOUT)
    response = session.post(urljoin(base, "menu/menu.php"), data=PUBLIC_LOGIN, headers=headers, timeout=static_catalog.TIMEOUT)
    response.raise_for_status()


def discover(session, source_url: str) -> list[Article]:
    base = _base(source_url)
    _BASE["url"] = base
    _login(session, base)
    headers = {
        "User-Agent": static_catalog.USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": urljoin(base, "menu/menu.php"),
    }
    articles: list[Article] = []
    seen: set[str] = set()
    declared = 0
    pos = 0
    while True:
        response = session.post(
            urljoin(base, "menu/search1_result.php"),
            data=SEARCH_FIELDS.format(pos=pos),
            headers=headers,
            timeout=static_catalog.TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        text = response.text
        count = COUNT_RE.search(text)
        if count:
            declared = int(count.group(1))
        items = ITEM_RE.findall(text)
        if not items:
            break
        for query, title, _promulgation, taxonomy in items:
            params = parse_qs(html_lib.unescape(query))
            typ = (params.get("typ") or [""])[0]
            file_id = (params.get("file") or [""])[0]
            date = (params.get("date") or [""])[0]
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            url = urljoin(base, f"doc/docopen_honbun.php?typ={typ}&file={file_id}&date={date}")
            _TAXONOMY[url] = _plain(taxonomy).removeprefix("体系").strip()
            articles.append(Article(code=file_id, url=url, title=_plain(title)))
        pos += PAGE_SIZE
        if declared and pos >= declared:
            break
    if declared and len(articles) != declared:
        print(f"[WARN] 検索件数 {declared} 件に対し {len(articles)} 件しか並ばなかった", flush=True)
    return articles


def fetch(session, article: Article) -> str | None:
    base = _BASE.get("url") or _base(article.url)
    headers = {"User-Agent": static_catalog.USER_AGENT, "Referer": urljoin(base, "menu/menu.php")}
    for attempt in range(2):
        try:
            response = session.get(article.url, headers=headers, timeout=static_catalog.TIMEOUT)
            if response.status_code == 200 and 'id="honbun"' in response.text:
                response.encoding = "utf-8"
                return response.text
        except Exception as exc:
            print(f"[WARN] fetch failed {article.url}: {exc}", flush=True)
        # セッションが切れると本文は 500 で返る。入り直して 1 回だけやり直す。
        if attempt == 0:
            try:
                _login(session, base)
            except Exception as exc:
                print(f"[WARN] re-login failed: {exc}", flush=True)
                return None
    print(f"[WARN] fetch failed {article.url}", flush=True)
    return None


def parse_article(raw: str, url: str) -> ParsedArticle | None:
    soup = BeautifulSoup(raw, "html.parser")
    body = soup.find(id="honbun")
    if body is None:
        return None
    title = re.sub(r"\s+", " ", body.find("h1").get_text(" ", strip=True)).strip() if body.find("h1") else ""
    if not title:
        return None
    seitei = body.find("div", class_="seitei")
    date_text = ""
    number = ""
    if seitei is not None:
        parts = [p.strip("（）() 　") for p in re.split(r"[）)]\s*[（(]", seitei.get_text("", strip=True))]
        joined = " ".join(parts)
        date_text = static_catalog.extract_wareki(joined)
        number = next((p for p in parts if "第" in p and "号" in p and not static_catalog.extract_wareki(p)), "")
    return ParsedArticle(
        title=title,
        content_html=body.decode_contents(),
        date_text=date_text,
        number=number,
        taxonomy_path=_TAXONOMY.get(url, ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download ordinances from bk2reiki (Tokyo Horei) systems.")
    parser.add_argument("--slug", default="")
    parser.add_argument("--system-type", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    slug = args.slug.strip() or reiki_targets.default_slug_for_system("bk2reiki")
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
