#!/usr/bin/env python3
"""失効した例規集の取得元 URL を引き直す。

北海道町村会の例規集データベース（houmu.h-chosonkai.gr.jp/~reikidb）は、
版番号を URL の中に持つ。

    /~reikidb/data/{choson_no}/{版}/reiki.html

自治体が新版を出すと版番号が繰り上がり、**旧ディレクトリごと 404 になる**。
目録も本文も 1 件残らず取れなくなるが、前回のマニフェストは残るので
「取得元は 518 件あると言っている、収録も 518 件」という記録だけが生き、
表示上は件数がそろって見える。2026-09-06 の点検では登録 105 件のうち
21 件がこの形で失効し、およそ 6,600 件が更新されないままだった。

登録簿 `data/municipalities/reiki_system_urls.tsv` は git 管理なので、
巡回の途中で書き換えない。引き直した URL は
`work/reiki/source_url_overrides.json` へ積み、次に対象を読むときに
差し替える（読み出しは reiki_targets 側）。上書きは「置き換える前の URL」を
一緒に覚えているので、TSV が直れば自動で外れる。

TSV そのものを直すのは `dev/municipalities/resolve_h_chosonkai_urls.py`。
このモジュールはその解決処理を共有し、巡回中の自動復旧に使う。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

sys.path.append(str(Path(__file__).parent))
import reiki_targets


USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0)"
REQUEST_TIMEOUT = 30

# 版番号を URL に持ち、版が上がると旧 URL が消える取得元。
H_CHOSONKAI_HOST = "houmu.h-chosonkai.gr.jp"
H_CHOSONKAI_ENTRY = "https://houmu.h-chosonkai.gr.jp/~reikidb/"
RECOVERABLE_HOSTS = frozenset({H_CHOSONKAI_HOST})

# 入口ページの自治体リンク。表示は「かな 漢字」の 2 行。
CHOSON_LINK_RE = re.compile(r'(?is)<a[^>]*href="[^"]*\?choson_no=(\d+)"[^>]*>(.*?)</a>')
# 自治体ページから読む現行の例規集入口。reiki.html と reiki_menu.html がある。
DATA_URL_RE = re.compile(r'(?i)["\']([^"\']*?/data/(\d+)/(\d+)/reiki(?:_menu)?\.html?)["\']')

# 取得元が消えたと判断する応答。403 は入れない。町村会の入口は
# ディレクトリ一覧を 403 で返すので、生きている URL まで巻き込む。
DEAD_STATUS_CODES = frozenset({404, 410})


def source_url_host(source_url: str) -> str:
    return (urlsplit(str(source_url or "").strip()).hostname or "").lower()


def is_recoverable_source_url(source_url: str) -> bool:
    """引き直しの手順を持っている取得元かどうか。"""
    return source_url_host(source_url) in RECOVERABLE_HOSTS


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def source_url_is_dead(source_url: str, session: requests.Session | None = None) -> bool:
    """取得元が消えているなら True。判断できない失敗は False にする。

    通信の失敗まで「消えた」と読むと、一時的な不通で URL を引き直して
    しまう。消えたと言い切れる応答（404 / 410）だけを消えたと扱う。
    """
    requester = session or build_session()
    try:
        response = requester.get(
            source_url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except Exception:
        return False
    return response.status_code in DEAD_STATUS_CODES


def _decode(response: requests.Response) -> str:
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def fetch_h_chosonkai_entry(session: requests.Session) -> str:
    """入口ページを読む。ここで CakePHP のセッションも受け取る。

    `?choson_no=N` はセッションが無いと自治体を選べず入口へ戻される。
    http で当てると https へ転送される際にクエリごと落ちるので、
    最初から https で当てる。
    """
    response = session.get(H_CHOSONKAI_ENTRY, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return _decode(response)


def h_chosonkai_choson_numbers(entry_html: str) -> dict[str, int]:
    """入口ページから 自治体名（漢字）→ choson_no を作る。"""
    numbers: dict[str, int] = {}
    for choson_no, label in CHOSON_LINK_RE.findall(entry_html):
        text = re.sub(r"<[^>]+>", " ", label)
        text = re.sub(r"\s+", " ", text).strip()
        if text == "":
            continue
        kanji = text.split(" ")[-1]
        if kanji:
            numbers.setdefault(kanji, int(choson_no))
    return numbers


def h_chosonkai_url_for_choson_no(session: requests.Session, choson_no: int) -> str:
    """自治体ページを開き、現行の版を含む例規集 URL を返す。"""
    response = session.get(
        urljoin(H_CHOSONKAI_ENTRY, f"?choson_no={choson_no}"),
        headers={"Referer": H_CHOSONKAI_ENTRY},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    html = _decode(response)
    for path, page_choson_no, _edition in DATA_URL_RE.findall(html):
        # 入口へ戻されると他の自治体のリンクを拾う。自治体番号で確かめる。
        if int(page_choson_no) != int(choson_no):
            continue
        return urljoin(response.url or H_CHOSONKAI_ENTRY, path)
    return ""


def resolve_h_chosonkai_source_url(
    name: str,
    session: requests.Session | None = None,
    entry_html: str = "",
) -> str:
    """自治体名から、現行の例規集 URL を引き直す。取れなければ空文字。"""
    municipality_name = str(name or "").strip()
    if municipality_name == "":
        return ""
    requester = session or build_session()
    try:
        html = entry_html or fetch_h_chosonkai_entry(requester)
    except Exception as error:
        print(f"[WARN] 町村会の入口ページを読めませんでした: {error}", flush=True)
        return ""
    choson_no = h_chosonkai_choson_numbers(html).get(municipality_name)
    if choson_no is None:
        return ""
    try:
        return h_chosonkai_url_for_choson_no(requester, choson_no)
    except Exception as error:
        print(f"[WARN] {municipality_name} の自治体ページを読めませんでした: {error}", flush=True)
        return ""


def resolve_source_url(
    name: str,
    source_url: str,
    session: requests.Session | None = None,
    entry_html: str = "",
) -> str:
    """取得元の系統に応じて URL を引き直す。手順が無ければ空文字。"""
    if source_url_host(source_url) == H_CHOSONKAI_HOST:
        return resolve_h_chosonkai_source_url(name, session=session, entry_html=entry_html)
    return ""


# --- 実行時の上書き --------------------------------------------------------


def load_overrides() -> dict[str, dict[str, str]]:
    return reiki_targets.load_source_url_overrides()


def record_override(code: str, previous_url: str, resolved_url: str) -> None:
    """引き直した URL を実行時の上書きとして残す。

    `previous_url` を一緒に持つのは、TSV が人手で直ったときに上書きを
    自動で外すため。読み出し側は TSV の値が一致するときだけ差し替える。
    """
    normalized_code = str(code or "").strip()
    if normalized_code == "" or str(resolved_url or "").strip() == "":
        return
    path = reiki_targets.source_url_overrides_path()
    overrides = load_overrides()
    overrides[normalized_code] = {
        "url": str(resolved_url).strip(),
        "replaces": str(previous_url or "").strip(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def ensure_source_url(target: dict, session: requests.Session | None = None) -> dict:
    """取得元が消えていれば引き直し、差し替えた対象を返す。

    版番号を URL に持つ取得元だけを見る。それ以外は通信しない。
    """
    source_url = str(target.get("source_url", "")).strip()
    if source_url == "" or not is_recoverable_source_url(source_url):
        return target

    requester = session or build_session()
    if not source_url_is_dead(source_url, session=requester):
        return target

    name = str(target.get("name", "")).strip()
    resolved_url = resolve_source_url(name, source_url, session=requester)
    if resolved_url == "" or resolved_url == source_url:
        print(
            f"[WARN] {name} の取得元 URL が失効していますが、引き直せませんでした: {source_url}",
            flush=True,
        )
        return target
    if source_url_is_dead(resolved_url, session=requester):
        print(
            f"[WARN] {name} の引き直した URL も開けません: {resolved_url}",
            flush=True,
        )
        return target

    record_override(str(target.get("code", "")), source_url, resolved_url)
    print(
        f"[WARN] {name} の取得元 URL が失効していたので引き直しました: "
        f"{source_url} -> {resolved_url} "
        f"（reiki_system_urls.tsv も更新してください）",
        flush=True,
    )
    slug = str(target.get("slug", "")).strip()
    if slug == "":
        return target
    try:
        return reiki_targets.load_reiki_target(slug)
    except ValueError:
        return target
