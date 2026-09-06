#!/usr/bin/env python3
"""会議録レジストリの取得ポリシーと変更検出を共通化する。"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urljoin, urlsplit, urlunsplit


POLICY_VERSION = 1

# robots.txt を取得可否の根拠にしない。議会の会議録と例規は誰でも検証できる
# べき公的記録で、robots.txt は法的な制限ではなく検索エンジン向けの慣行に
# すぎない、という運営判断による（2026-08-28。2026-09-06 に再確認）。
# 議事録と法令は国民の財産であり、公開されている以上は取得する。
#
# 個別の自治体で robots.txt が拒否していても、この方針は変えない。
# 2026-09-06 の点検では浦幌町の robots.txt が会議録 PDF の置き場所
# （/assets/images/ と *.pdf）を拒否していたが、取得する判断とした。
#
# 相手側への配慮は robots ではなく、レート制限と正直な User-Agent で行う。
# video_only（本文が存在しない）や login_required（認証が必要）の除外は
# robots とは別の理由なので、この設定では解除されない。
ENFORCE_ROBOTS = False


def canonical_system_type(system_type: str) -> str:
    normalized = str(system_type).strip()
    if normalized == "voices":
        return "gijiroku.com"
    if normalized in {"db-search", "kaigiroku-indexphp"}:
        return "dbsr"
    return normalized


def origin_url(url: str, path: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))


def robots_txt_url(url: str) -> str:
    return origin_url(url, "/robots.txt")


def directory_base_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    base_path = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def required_crawl_urls(row: dict[str, str]) -> list[str]:
    """現在のスクレイパが取得開始時に必ず使う既知経路を返す。"""
    source_url = str(row.get("url", "")).strip()
    if not source_url:
        return []

    family = canonical_system_type(str(row.get("system_type", "")))
    urls = [source_url]
    if family == "kaigiroku.net":
        # UIは /tenant/ で許可されていても、本文取得に必須のAPIは別経路。
        urls.append(origin_url(source_url, "/dnp/search/"))
    elif family == "dbsr":
        urls.append(urljoin(directory_base_url(source_url), "100000?Template=search-library"))
    elif family == "gijiroku.com":
        base = directory_base_url(source_url)
        urls.extend(
            [
                urljoin(base, "g08v_viewh.asp"),
                urljoin(base, "g08v_views.asp"),
                urljoin(base, "CGI/voiweb.exe?ACT=100&KTYP=2,3,0&KGTP=1,2&SORT=0"),
            ]
        )
    elif family == "amivoice":
        base = source_url if source_url.endswith("/") else source_url + "/"
        urls.append(urljoin(base, "search.exe?process=list_vcsm"))

    return list(dict.fromkeys(urls))


def policy_fingerprint(row: dict[str, str]) -> str:
    """URL・system_type・必須経路から安定した変更検出値を作る。"""
    source_url = str(row.get("url", "")).strip()
    payload = {
        "version": POLICY_VERSION,
        "url": source_url,
        "system_type": str(row.get("system_type", "")).strip(),
        "required_urls": required_crawl_urls(row),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def policy_fingerprint_is_current(row: dict[str, str]) -> bool:
    return str(row.get("policy_fingerprint", "")).strip() == policy_fingerprint(row)
