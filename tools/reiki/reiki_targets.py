#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))
from municipality_slugs import code_name_slug


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
                "name_romaji": str(row.get("name_romaji", "")).strip(),
            }
    return index


def load_municipality_homepage_index() -> dict[str, str]:
    index: dict[str, str] = {}
    path = WORKSPACE_ROOT / "work" / "municipalities" / "municipality_homepages.csv"
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


def load_local_reiki_url_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    path = WORKSPACE_ROOT / "work" / "municipalities" / "reiki_system_urls.tsv"
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


def build_public_data_url(relative_path: str) -> str:
    normalized = normalize_relative_path(relative_path)
    if normalized == "":
        return "/data"
    return "/data/" + normalized


def configured_slug_by_code() -> dict[str, str]:
    slug_map: dict[str, str] = {}
    for slug, entry in load_configured_municipalities().items():
        if not isinstance(slug, str) or not isinstance(entry, dict):
            continue
        code = str(entry.get("code", "")).strip()
        if code and code not in slug_map:
            slug_map[code] = slug.strip()
    return slug_map


def fallback_slug_for_reiki(
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


def derive_taikei_entry_url(source_url: str) -> str:
    source_url = source_url.strip()
    if source_url == "":
        raise ValueError("Missing taikei source URL.")

    parts = urlsplit(source_url)
    path = parts.path or "/"
    lower_path = path.lower()

    if "/reiki_taikei/" in lower_path:
        return source_url

    if lower_path.endswith("/") or lower_path.endswith("/reiki_menu.html") or lower_path.endswith("/reiki_menu.htm"):
        target_path = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
        target_path += "reiki_taikei/taikei_default.html"
        return urlunsplit((parts.scheme or "https", parts.netloc, target_path, "", ""))

    if lower_path.endswith("/index.html") or lower_path.endswith("/index.htm"):
        target_path = path.rsplit("/", 1)[0] + "/reiki_taikei/taikei_default.html"
        return urlunsplit((parts.scheme or "https", parts.netloc, target_path, "", ""))

    return source_url


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
    reiki_config = entry.get("reiki", {})
    if not isinstance(reiki_config, dict):
        reiki_config = {}

    name = str(entry.get("name", "")).strip()
    if name == "":
        name = str((master_entry or {}).get("name", "")).strip() or slug

    base_source_dir = str(reiki_config.get("source_dir", reiki_config.get("data_dir", f"reiki/{slug}/source"))).strip()
    clean_html_dir = str(reiki_config.get("clean_html_dir", f"reiki/{slug}/html")).strip()
    classification_dir = str(reiki_config.get("classification_dir", f"reiki/{slug}/json")).strip()
    image_dir = str(reiki_config.get("image_dir", f"reiki/{slug}/images")).strip()
    markdown_dir = str(reiki_config.get("markdown_dir", f"reiki/{slug}/markdown")).strip()
    db_path = str(reiki_config.get("db_path", f"reiki/{slug}/ordinances.sqlite")).strip()
    work_root = build_work_path(str(Path(base_source_dir).parent))

    return {
        "slug": slug,
        "name": name,
        "full_name": str((master_entry or {}).get("full_name", "")).strip() or name,
        "code": code,
        "entity_type": str((master_entry or {}).get("entity_type", "")).strip(),
        "system_type": system_type,
        "source_url": source_url,
        "entry_url": derive_taikei_entry_url(source_url) if system_type == "taikei" else source_url,
        "data_root": build_data_path(f"reiki/{slug}"),
        "work_root": work_root,
        "source_dir": build_work_path(base_source_dir),
        "html_dir": build_data_path(clean_html_dir),
        "classification_dir": build_data_path(classification_dir),
        "image_dir": build_data_path(image_dir),
        "image_public_url": build_public_data_url(image_dir),
        "markdown_dir": build_work_path(markdown_dir),
        "db_path": build_data_path(db_path),
    }


def iter_reiki_targets(expected_system: str | None = None, configured_only: bool = False) -> list[dict]:
    municipalities = load_configured_municipalities()
    url_index = load_local_reiki_url_index()
    master_index = load_municipality_master_index()
    homepage_index = load_municipality_homepage_index()
    slugs_by_code = configured_slug_by_code()
    targets: list[dict] = []

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
            municipality_entry = None

        master_entry = master_index.get(code)
        if configured_slug:
            slug = configured_slug
        else:
            slug = fallback_slug_for_reiki(
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
                municipality_entry=municipality_entry,
                master_entry=master_entry,
            )
        )

    return targets


def default_slug_for_system(expected_system: str | None = None) -> str:
    config = load_config()
    municipalities = load_configured_municipalities()
    if municipalities:
        preferred_slug = str(config.get("DEFAULT_SLUG", "")).strip()
        if preferred_slug and preferred_slug in municipalities:
            try:
                target = load_reiki_target(preferred_slug, expected_system=expected_system)
                return str(target["slug"])
            except ValueError:
                pass

    configured_targets = iter_reiki_targets(expected_system=expected_system, configured_only=True)
    if configured_targets:
        return str(configured_targets[0]["slug"])

    all_targets = iter_reiki_targets(expected_system=expected_system, configured_only=False)
    if all_targets:
        return str(all_targets[0]["slug"])

    if expected_system is None:
        raise ValueError("No municipalities are configured.")
    raise ValueError(f"No municipality found for system_type={expected_system!r}")


def load_reiki_target(slug: str, expected_system: str | None = None) -> dict:
    for target in iter_reiki_targets(expected_system=expected_system, configured_only=False):
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
        url_index = load_local_reiki_url_index()
        system_type = str(url_index.get(code, {}).get("system_type", "")).strip()
        if expected_system is not None and system_type != expected_system:
            raise ValueError(
                f"Municipality slug {slug} uses system_type={system_type!r}, expected {expected_system!r}"
            )
        raise ValueError(f"Municipality code {code} is missing from work/municipalities/reiki_system_urls.tsv")

    raise ValueError(f"Municipality slug not found: {slug}")
