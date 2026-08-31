"""スクレイピング入力と公開済み世代の対応を判定する。"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from tools.gijiroku import crawl_policy
from tools.reiki import reiki_targets


FINGERPRINT_VERSION = 1
LEGACY_SCRAPER_GENERATION = 1
DEFAULT_SCRAPER_GENERATIONS = {"minutes": 1, "reiki": 1}


def normalize_doc_type(doc_type: object) -> str:
    normalized = str(doc_type or "").strip().casefold()
    if normalized in {"minutes", "gijiroku"}:
        return "minutes"
    if normalized == "reiki":
        return "reiki"
    raise ValueError(f"unsupported document type: {doc_type!r}")


def configured_scraper_generation(doc_type: object) -> int:
    normalized = normalize_doc_type(doc_type)
    env_name = f"MIYABE_{normalized.upper()}_SCRAPER_GENERATION"
    try:
        generation = int(str(os.getenv(env_name, DEFAULT_SCRAPER_GENERATIONS[normalized])).strip())
    except (TypeError, ValueError):
        generation = DEFAULT_SCRAPER_GENERATIONS[normalized]
    return max(1, generation)


def _source_url(target: dict[str, Any]) -> str:
    return str(target.get("source_url") or target.get("url") or "").strip()


def _minutes_payload(target: dict[str, Any]) -> dict[str, Any]:
    source_url = _source_url(target)
    system_type = crawl_policy.canonical_system_type(
        str(target.get("system_family") or target.get("system_type") or "").strip().casefold()
    )
    policy_row = {"url": source_url, "system_type": system_type}
    return {
        "source_url": source_url,
        "system_type": system_type,
        "required_urls": crawl_policy.required_crawl_urls(policy_row),
        # 必須URLを導く規則が変わった場合も、入力世代を同じにしない。
        "crawl_policy_version": crawl_policy.POLICY_VERSION,
    }


def _reiki_entry_url(target: dict[str, Any], system_type: str, source_url: str) -> str:
    explicit = str(target.get("entry_url") or "").strip()
    if explicit:
        return explicit
    if system_type in reiki_targets.TAIKEI_LIKE_SYSTEMS:
        return reiki_targets.derive_taikei_entry_url(source_url)
    return source_url


def _reiki_payload(target: dict[str, Any]) -> dict[str, Any]:
    source_url = _source_url(target)
    system_type = str(target.get("system_type") or "").strip().casefold()
    return {
        "source_url": source_url,
        "system_type": system_type,
        "required_urls": list(
            dict.fromkeys([source_url, _reiki_entry_url(target, system_type, source_url)])
        ),
    }


def input_fingerprint(
    doc_type: object,
    target: dict[str, Any],
    *,
    scraper_generation: int | None = None,
) -> str:
    """URL・正規化した取得方式・必要URL・スクレイパ世代を一つのhashにする。"""
    normalized = normalize_doc_type(doc_type)
    payload = _minutes_payload(target) if normalized == "minutes" else _reiki_payload(target)
    payload.update(
        {
            "fingerprint_version": FINGERPRINT_VERSION,
            "doc_type": normalized,
            "scraper_generation": max(
                1,
                int(scraper_generation)
                if scraper_generation is not None
                else configured_scraper_generation(normalized),
            ),
        }
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# 索引を「まだ載っていない」と読むのはこの値のときだけ。
#
# `queued` は別キューへ投げた直後という意味で、失敗ではない。これを未公開と
# 読むと、取得が終わった自治体が毎回 30 日を待たずに選び直され、**全国が
# 毎周期やり直しになる**（本番の記録では会議録 186 件・例規 27 件が
# この値のまま残っていた）。索引が落ちたときに投げ直すのは
# `tools/tasks/index_outbox.py` の役目で、取得のやり直しではない。
UNPUBLISHED_INDEX_STATUSES = {"failed"}


def fingerprint_matches_published(
    doc_type: object,
    target: dict[str, Any],
    item: dict[str, Any],
) -> bool:
    """成功確認済みitemが、現在の入力と同じ世代を公開したかを返す。"""
    index_status = str(item.get("index_status") or "").strip().lower()
    if index_status in UNPUBLISHED_INDEX_STATUSES:
        return False

    return fingerprint_matches_observed_input(doc_type, target, item)


def fingerprint_matches_observed_input(
    doc_type: object,
    target: dict[str, Any],
    item: dict[str, Any],
) -> bool:
    """成功・失敗を問わず、その試行が使った入力世代との一致だけを返す。"""

    expected = input_fingerprint(doc_type, target)
    observed = str(item.get("scrape_input_fingerprint") or item.get("input_fingerprint") or "").strip()
    if observed:
        return observed == expected

    # fingerprint導入前の全自治体を一斉に再取得しない。旧item自身のURL/typeを
    # 世代1として再計算すれば、導入前でもregistry差分だけは検出できる。
    legacy_source = _source_url(item)
    legacy_system = str(item.get("system_type") or "").strip()
    if not legacy_source or not legacy_system:
        return False
    legacy = input_fingerprint(
        doc_type,
        item,
        scraper_generation=LEGACY_SCRAPER_GENERATION,
    )
    return legacy == expected
