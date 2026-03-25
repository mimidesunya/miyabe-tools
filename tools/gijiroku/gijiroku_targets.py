#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data"
WORK_ROOT = WORKSPACE_ROOT / "work"


def project_root() -> Path:
    return WORKSPACE_ROOT


def load_config() -> dict:
    for candidate in (DATA_ROOT / "config.json", DATA_ROOT / "config.example.json"):
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else {}
    return {}


def load_configured_municipalities() -> dict[str, dict]:
    config = load_config()
    municipalities = config.get("MUNICIPALITIES", {})
    return municipalities if isinstance(municipalities, dict) else {}


def load_municipality_master_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    path = WORKSPACE_ROOT / "work" / "municipalities" / "municipality_master.tsv"
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
                "full_name": str(row.get("full_name", "")).strip(),
            }
    return index


def load_local_minutes_url_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    path = WORKSPACE_ROOT / "work" / "municipalities" / "assembly_minutes_system_urls.tsv"
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


def configured_slug_by_code() -> dict[str, str]:
    slug_map: dict[str, str] = {}
    for slug, entry in load_configured_municipalities().items():
        if not isinstance(slug, str) or not isinstance(entry, dict):
            continue
        code = str(entry.get("code", "")).strip()
        if code and code not in slug_map:
            slug_map[code] = slug.strip()
    return slug_map


def sanitize_slug_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    token = re.sub(r"-{2,}", "-", token)
    return token


def kaigiroku_tenant_slug_token(source_url: str) -> str:
    parts = urlsplit(source_url)
    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0].lower() == "tenant":
        return sanitize_slug_token(path_parts[1])
    return ""


def fallback_slug_for_minutes(code: str, source_url: str) -> str:
    tenant_token = kaigiroku_tenant_slug_token(source_url)
    if tenant_token:
        return f"{tenant_token}-{code}"

    host = (urlsplit(source_url).hostname or "").lower()
    host_label = sanitize_slug_token(host.split(".", 1)[0])
    if host_label == "" or re.fullmatch(r"www\d*", host_label):
        return f"jis-{code}"
    return f"{host_label}-{code}"


def build_target_entry(
    *,
    slug: str,
    code: str,
    source_url: str,
    system_type: str,
    municipality_entry: dict | None,
    master_entry: dict[str, str] | None,
) -> dict:
    entry = municipality_entry if isinstance(municipality_entry, dict) else {}
    gijiroku_config = entry.get("gijiroku", {})
    if not isinstance(gijiroku_config, dict):
        gijiroku_config = {}

    name = str(entry.get("name", "")).strip()
    if name == "":
        name = str((master_entry or {}).get("name", "")).strip() or slug
    assembly_name = str(gijiroku_config.get("assembly_name", "")).strip()
    if assembly_name == "":
        assembly_name = f"{name}議会"

    data_dir = str(gijiroku_config.get("data_dir", f"gijiroku/{slug}")).strip()
    downloads_dir = str(gijiroku_config.get("downloads_dir", f"gijiroku/{slug}/downloads")).strip()
    index_json_path = str(gijiroku_config.get("index_json_path", f"gijiroku/{slug}/meetings_index.json")).strip()
    db_path = str(gijiroku_config.get("db_path", f"{data_dir}/minutes.sqlite")).strip()

    return {
        "slug": slug,
        "name": name,
        "assembly_name": assembly_name,
        "code": code,
        "entity_type": str((master_entry or {}).get("entity_type", "")).strip(),
        "full_name": str((master_entry or {}).get("full_name", "")).strip() or name,
        "system_type": system_type,
        "source_url": source_url,
        "base_url": derive_base_url(source_url),
        "robots_txt_url": derive_robots_txt_url(source_url),
        "data_dir": build_data_path(data_dir),
        "work_dir": build_work_path(data_dir),
        "downloads_dir": build_work_path(downloads_dir),
        "index_json_path": build_work_path(index_json_path),
        "db_path": build_data_path(db_path),
    }


def iter_gijiroku_targets(expected_system: str | None = None, configured_only: bool = False) -> list[dict]:
    municipalities = load_configured_municipalities()
    url_index = load_local_minutes_url_index()
    master_index = load_municipality_master_index()
    slugs_by_code = configured_slug_by_code()
    targets: list[dict] = []
    seen_codes: set[str] = set()

    for code, url_entry in sorted(url_index.items()):
        system_type = str(url_entry.get("system_type", "")).strip()
        if expected_system is not None and system_type != expected_system:
            continue

        source_url = str(url_entry.get("url", "")).strip()
        if source_url == "":
            continue

        configured_slug = slugs_by_code.get(code, "")
        if configured_slug:
            slug = configured_slug
            municipality_entry = municipalities.get(slug)
        else:
            if configured_only:
                continue
            slug = fallback_slug_for_minutes(code, source_url)
            municipality_entry = None

        master_entry = master_index.get(code)
        targets.append(
            build_target_entry(
                slug=slug,
                code=code,
                source_url=source_url,
                system_type=system_type,
                municipality_entry=municipality_entry,
                master_entry=master_entry,
            )
        )
        seen_codes.add(code)

    return targets


def default_slug_for_system(expected_system: str | None = None) -> str:
    config = load_config()
    municipalities = load_configured_municipalities()
    if municipalities:
        preferred_slug = str(config.get("DEFAULT_SLUG", "")).strip()
        if preferred_slug and preferred_slug in municipalities:
            try:
                target = load_gijiroku_target(preferred_slug, expected_system=expected_system)
                return str(target["slug"])
            except ValueError:
                pass

    configured_targets = iter_gijiroku_targets(expected_system=expected_system, configured_only=True)
    if configured_targets:
        return str(configured_targets[0]["slug"])

    all_targets = iter_gijiroku_targets(expected_system=expected_system, configured_only=False)
    if all_targets:
        return str(all_targets[0]["slug"])

    if expected_system is None:
        raise ValueError("No municipalities are configured.")
    raise ValueError(f"No municipality found for system_type={expected_system!r}")


def load_gijiroku_target(slug: str, expected_system: str | None = None) -> dict:
    for target in iter_gijiroku_targets(expected_system=expected_system, configured_only=False):
        if str(target.get("slug", "")).strip() == slug:
            return target

    municipalities = load_configured_municipalities()
    if slug in municipalities:
        entry = municipalities.get(slug)
        if not isinstance(entry, dict):
            raise ValueError(f"Municipality slug not found in config: {slug}")
        code = str(entry.get("code", "")).strip()
        if code == "":
            raise ValueError(f"Municipality code is missing for slug: {slug}")
        url_index = load_local_minutes_url_index()
        system_type = str(url_index.get(code, {}).get("system_type", "")).strip()
        if expected_system is not None and system_type != expected_system:
            raise ValueError(
                f"Municipality slug {slug} uses system_type={system_type!r}, expected {expected_system!r}"
            )
        raise ValueError(f"Municipality code {code} is missing from work/municipalities/assembly_minutes_system_urls.tsv")

    raise ValueError(f"Municipality slug not found: {slug}")


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
