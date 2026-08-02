#!/usr/bin/env python3
"""会議録スクレイピング対象を読み込み、実行しやすい形へ正規化する。

正本は data/municipalities/assembly_minutes_system_urls.tsv。
自治体マスタと結合して slug や data/work パスを導出し、system_type の別名差分を
バッチスケジューラから隠す。
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))
from municipality_slugs import code_name_slug, sanitize_slug_token
from gijiroku.crawl_policy import policy_fingerprint


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data"
WORK_ROOT = WORKSPACE_ROOT / "work"
SYSTEM_FAMILY_ALIASES = {
    # 同じ内部構造の公開システムが、調査 TSV では別名で記録されることがある。
    # バッチ側では provider の実体ごとに 1 つのスクレイパへ寄せる。
    "gijiroku.com": {"gijiroku.com", "voices"},
    "kaigiroku.net": {"kaigiroku.net"},
    "dbsr": {"dbsr", "db-search", "kaigiroku-indexphp"},
    "kensakusystem": {"kensakusystem"},
    "amivoice": {"amivoice"},
    "voicetechno": {"voicetechno"},
    "msearch": {"msearch"},
    "kami-city-pdf": {"kami-city-pdf"},
    "site-gikai-pdf": {"site-gikai-pdf"},
    "static-kaigiroku-dir": {"static-kaigiroku-dir"},
    "独自": {"独自"},
}
SYSTEM_FAMILY_BY_TYPE = {
    system_type: family
    for family, system_types in SYSTEM_FAMILY_ALIASES.items()
    for system_type in system_types
}

CRAWL_STATUS_ENABLED = "enabled"
CRAWL_STATUS_EXCLUDED = "excluded"
CRAWL_STATUS_UNRESOLVED = "unresolved"
CRAWL_STATUS_REVIEW_REQUIRED = "review_required"
VALID_CRAWL_STATUSES = {
    CRAWL_STATUS_ENABLED,
    CRAWL_STATUS_EXCLUDED,
    CRAWL_STATUS_UNRESOLVED,
    CRAWL_STATUS_REVIEW_REQUIRED,
}


class CrawlPolicyBlockedError(ValueError):
    """対象は登録済みだが、取得ポリシーにより実行できない。"""


def project_root() -> Path:
    # 既存の一括バッチ群は repo ルート基準で batch/work/logs を組み立てる。
    # target loader を薄くした後も、ここだけは互換 API を残しておく。
    return WORKSPACE_ROOT


def canonical_minutes_system_type(system_type: str) -> str:
    normalized = str(system_type).strip()
    if normalized == "":
        return ""
    return SYSTEM_FAMILY_BY_TYPE.get(normalized, normalized)


def accepted_minutes_system_types(expected_system: str | None) -> set[str] | None:
    normalized = str(expected_system or "").strip()
    if normalized == "":
        return None
    if normalized in SYSTEM_FAMILY_ALIASES:
        return set(SYSTEM_FAMILY_ALIASES[normalized])
    if normalized in SYSTEM_FAMILY_BY_TYPE:
        return {normalized}
    return {normalized}


def load_config() -> dict:
    for candidate in (DATA_ROOT / "config.json", DATA_ROOT / "config.example.json"):
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else {}
    return {}


def load_municipality_master_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    path = DATA_ROOT / "municipalities" / "municipality_master.tsv"
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not isinstance(row, dict):
                continue
            code = str(row.get("jis_code", "")).strip()
            if code == "":
                continue
            index[code] = {
                "entity_type": str(row.get("entity_type", "")).strip(),
                "pref_name": str(row.get("pref_name", "")).strip(),
                "name": str(row.get("name", "")).strip(),
                "name_kana": str(row.get("name_kana", "")).strip(),
                "full_name": str(row.get("full_name", "")).strip(),
                "name_romaji": str(row.get("name_romaji", "")).strip(),
            }
    return index


def load_municipality_homepage_index() -> dict[str, str]:
    index: dict[str, str] = {}
    path = DATA_ROOT / "municipalities" / "municipality_homepages.csv"
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not isinstance(row, dict):
                continue
            code = str(row.get("jis_code", "")).strip()
            url = str(row.get("url", "")).strip()
            if code and url and code not in index:
                index[code] = url
    return index


def effective_crawl_policy(row: dict[str, str]) -> dict[str, str]:
    """保存済み判断を読み、レジストリ変更後なら監査待ちへ落とす。"""
    source_url = str(row.get("url", "")).strip()
    crawl_status = str(row.get("crawl_status", "")).strip()
    if crawl_status not in VALID_CRAWL_STATUSES:
        # 旧3列TSVとの後方互換。
        crawl_status = CRAWL_STATUS_ENABLED if source_url else CRAWL_STATUS_UNRESOLVED

    stored_fingerprint = str(row.get("policy_fingerprint", "")).strip()
    if "policy_fingerprint" in row and source_url and stored_fingerprint != policy_fingerprint(row):
        return {
            "crawl_status": CRAWL_STATUS_REVIEW_REQUIRED,
            "exclusion_reason": "registry_changed",
            "exclusion_detail": "URLまたはsystem_type変更後のrobots監査待ち",
            "policy_checked_at": "",
            "policy_fingerprint": stored_fingerprint,
        }
    return {
        "crawl_status": crawl_status,
        "exclusion_reason": str(row.get("exclusion_reason", "")).strip(),
        "exclusion_detail": str(row.get("exclusion_detail", "")).strip(),
        "policy_checked_at": str(row.get("policy_checked_at", "")).strip(),
        "policy_fingerprint": stored_fingerprint,
    }


def load_local_minutes_url_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    path = DATA_ROOT / "municipalities" / "assembly_minutes_system_urls.tsv"
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not isinstance(row, dict):
                continue
            code = str(row.get("jis_code", "")).strip()
            if code == "":
                continue
            source_url = str(row.get("url", "")).strip()
            system_type = str(row.get("system_type", "")).strip()
            policy = effective_crawl_policy(row)
            index[code] = {
                "url": source_url,
                "system_type": system_type,
                **policy,
            }
    return index


def normalize_relative_path(relative_path: str) -> str:
    return relative_path.replace("\\", "/").strip("/")


def build_data_path(relative_path: str) -> Path:
    normalized = normalize_relative_path(relative_path)
    if normalized == "":
        return DATA_ROOT
    return DATA_ROOT / Path(normalized)


def build_work_path(relative_path: str) -> Path:
    normalized = normalize_relative_path(relative_path)
    if normalized == "":
        return WORK_ROOT
    return WORK_ROOT / Path(normalized)


def fallback_slug_for_minutes(
    code: str,
    source_url: str,
    homepage_url: str = "",
    *,
    master_entry: dict[str, str] | None = None,
) -> str:
    entry = master_entry or {}
    return code_name_slug(
        code,
        source_url,
        homepage_url,
        name=str(entry.get("name", "")).strip(),
        entity_type=str(entry.get("entity_type", "")).strip(),
        name_romaji=str(entry.get("name_romaji", "")).strip(),
    )


def canonical_slug_for_minutes(
    code: str,
    source_url: str,
    homepage_url: str = "",
    *,
    master_entry: dict[str, str] | None = None,
) -> str:
    return fallback_slug_for_minutes(
        code,
        source_url,
        homepage_url,
        master_entry=master_entry or {},
    )


def build_target_entry(
    *,
    slug: str,
    code: str,
    source_url: str,
    system_type: str,
    master_entry: dict[str, str] | None,
    crawl_status: str = CRAWL_STATUS_ENABLED,
    exclusion_reason: str = "",
    exclusion_detail: str = "",
    policy_checked_at: str = "",
    policy_fingerprint_value: str = "",
) -> dict:
    # 会議録スクレイパも保存先と議会名を slug / master から決め打ちし、個別 override は持たない。
    name = str((master_entry or {}).get("name", "")).strip() or slug
    assembly_name = f"{name}議会"
    data_dir = f"gijiroku/{slug}"
    downloads_dir = f"gijiroku/{slug}/downloads"
    index_json_path = f"gijiroku/{slug}/meetings_index.json"

    return {
        "slug": slug,
        "name": name,
        "name_kana": str((master_entry or {}).get("name_kana", "")).strip(),
        "assembly_name": assembly_name,
        "code": code,
        "entity_type": str((master_entry or {}).get("entity_type", "")).strip(),
        "full_name": str((master_entry or {}).get("full_name", "")).strip() or name,
        "name_romaji": str((master_entry or {}).get("name_romaji", "")).strip(),
        "system_type": system_type,
        "system_family": canonical_minutes_system_type(system_type),
        "source_url": source_url,
        "base_url": derive_base_url(source_url),
        "robots_txt_url": derive_robots_txt_url(source_url),
        "crawl_status": crawl_status,
        "crawl_enabled": crawl_status == CRAWL_STATUS_ENABLED,
        "exclusion_reason": exclusion_reason,
        "exclusion_detail": exclusion_detail,
        "policy_checked_at": policy_checked_at,
        "policy_fingerprint": policy_fingerprint_value,
        "data_dir": build_data_path(data_dir),
        "work_dir": build_work_path(data_dir),
        "downloads_dir": build_work_path(downloads_dir),
        "index_json_path": build_work_path(index_json_path),
    }


def iter_gijiroku_targets(
    expected_system: str | None = None,
    *,
    include_inactive: bool = True,
) -> list[dict]:
    """URL登録済み対象を返す。

    検索・既存データ整理から登録情報が消えないよう、既定ではrobots除外も含める。
    新規取得に使う呼び出し元は iter_scrapeable_gijiroku_targets() を使う。
    """
    url_index = load_local_minutes_url_index()
    master_index = load_municipality_master_index()
    homepage_index = load_municipality_homepage_index()
    accepted_system_types = accepted_minutes_system_types(expected_system)
    targets: list[dict] = []

    for code, url_entry in sorted(url_index.items()):
        system_type = str(url_entry.get("system_type", "")).strip()
        if accepted_system_types is not None and system_type not in accepted_system_types:
            continue

        source_url = str(url_entry.get("url", "")).strip()
        if source_url == "":
            continue

        crawl_status = str(url_entry.get("crawl_status", CRAWL_STATUS_ENABLED)).strip()
        if not include_inactive and crawl_status != CRAWL_STATUS_ENABLED:
            continue

        master_entry = master_index.get(code)
        slug = canonical_slug_for_minutes(
            code,
            source_url,
            homepage_index.get(code, ""),
            master_entry=master_entry,
        )
        targets.append(
            build_target_entry(
                slug=slug,
                code=code,
                source_url=source_url,
                system_type=system_type,
                master_entry=master_entry,
                crawl_status=crawl_status,
                exclusion_reason=str(url_entry.get("exclusion_reason", "")).strip(),
                exclusion_detail=str(url_entry.get("exclusion_detail", "")).strip(),
                policy_checked_at=str(url_entry.get("policy_checked_at", "")).strip(),
                policy_fingerprint_value=str(url_entry.get("policy_fingerprint", "")).strip(),
            )
        )

    return targets


def iter_scrapeable_gijiroku_targets(expected_system: str | None = None) -> list[dict]:
    """robots監査で明示的に enabled となった対象だけを返す。"""
    return iter_gijiroku_targets(expected_system=expected_system, include_inactive=False)


def default_slug_for_system(expected_system: str | None = None) -> str:
    config = load_config()
    preferred_slug = str(config.get("DEFAULT_SLUG", "")).strip()
    if preferred_slug:
        try:
            target = load_gijiroku_target(preferred_slug, expected_system=expected_system)
            return str(target["slug"])
        except ValueError:
            pass

    all_targets = iter_scrapeable_gijiroku_targets(expected_system=expected_system)
    if all_targets:
        return str(all_targets[0]["slug"])

    if expected_system is None:
        raise ValueError("No municipalities were found in data/municipalities.")
    raise ValueError(f"No municipality found for system_type={expected_system!r}")


def load_gijiroku_target(
    slug: str,
    expected_system: str | None = None,
    *,
    allow_inactive: bool = False,
) -> dict:
    for target in iter_gijiroku_targets(expected_system=expected_system):
        if gijiroku_target_matches_slug(target, slug):
            if not allow_inactive and str(target.get("crawl_status", "")) != CRAWL_STATUS_ENABLED:
                reason = str(target.get("exclusion_reason", "")).strip() or "crawl_policy"
                detail = str(target.get("exclusion_detail", "")).strip()
                suffix = f" ({detail})" if detail else ""
                raise CrawlPolicyBlockedError(
                    f"Municipality is not enabled for crawling: {slug} / {reason}{suffix}"
                )
            return target

    raise ValueError(f"Municipality slug not found: {slug}")


def gijiroku_target_matches_slug(target: dict, slug: str) -> bool:
    candidate = str(slug).strip()
    if candidate == "":
        return False

    target_slug = str(target.get("slug", "")).strip()
    code = str(target.get("code", "")).strip()
    name_romaji = sanitize_slug_token(str(target.get("name_romaji", "")).strip())
    aliases = {target_slug}
    if code:
        aliases.add(code)
    if name_romaji:
        aliases.add(name_romaji)
        if code:
            aliases.add(f"{code}-{name_romaji}")
    return candidate in aliases


def derive_base_url(source_url: str) -> str:
    parts = urlsplit(source_url)
    path = parts.path or "/"
    tenant_match = re.match(r"^(.*?/tenant/[^/]+/)", path, flags=re.I)
    if tenant_match:
        base_path = tenant_match.group(1)
    elif path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def derive_robots_txt_url(source_url: str) -> str:
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))
