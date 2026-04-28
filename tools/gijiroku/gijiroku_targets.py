#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))
from municipality_slugs import code_name_slug, sanitize_slug_token


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data"
WORK_ROOT = WORKSPACE_ROOT / "work"
SYSTEM_FAMILY_ALIASES = {
    "gijiroku.com": {"gijiroku.com", "voices"},
    "kaigiroku.net": {"kaigiroku.net"},
    "dbsr": {"dbsr", "db-search", "kaigiroku-indexphp"},
    "kensakusystem": {"kensakusystem"},
    "amivoice": {"amivoice"},
    "voicetechno": {"voicetechno"},
    "msearch": {"msearch"},
    "kami-city-pdf": {"kami-city-pdf"},
    "独自": {"独自"},
}
SYSTEM_FAMILY_BY_TYPE = {
    system_type: family
    for family, system_types in SYSTEM_FAMILY_ALIASES.items()
    for system_type in system_types
}


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
            index[code] = {
                "url": str(row.get("url", "")).strip(),
                "system_type": str(row.get("system_type", "")).strip(),
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
) -> dict:
    # 会議録スクレイパも保存先と議会名を slug / master から決め打ちし、個別 override は持たない。
    name = str((master_entry or {}).get("name", "")).strip() or slug
    assembly_name = f"{name}議会"
    data_dir = f"gijiroku/{slug}"
    downloads_dir = f"gijiroku/{slug}/downloads"
    index_json_path = f"gijiroku/{slug}/meetings_index.json"
    db_path = f"{data_dir}/minutes.sqlite"

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
        "data_dir": build_data_path(data_dir),
        "work_dir": build_work_path(data_dir),
        "downloads_dir": build_work_path(downloads_dir),
        "index_json_path": build_work_path(index_json_path),
        "db_path": build_data_path(db_path),
    }


def iter_gijiroku_targets(expected_system: str | None = None) -> list[dict]:
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
            )
        )

    return targets


def default_slug_for_system(expected_system: str | None = None) -> str:
    config = load_config()
    preferred_slug = str(config.get("DEFAULT_SLUG", "")).strip()
    if preferred_slug:
        try:
            target = load_gijiroku_target(preferred_slug, expected_system=expected_system)
            return str(target["slug"])
        except ValueError:
            pass

    all_targets = iter_gijiroku_targets(expected_system=expected_system)
    if all_targets:
        return str(all_targets[0]["slug"])

    if expected_system is None:
        raise ValueError("No municipalities were found in data/municipalities.")
    raise ValueError(f"No municipality found for system_type={expected_system!r}")


def load_gijiroku_target(slug: str, expected_system: str | None = None) -> dict:
    for target in iter_gijiroku_targets(expected_system=expected_system):
        if gijiroku_target_matches_slug(target, slug):
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
    if path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def derive_robots_txt_url(source_url: str) -> str:
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))
