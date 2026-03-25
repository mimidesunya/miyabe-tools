#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path


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


def default_slug_for_system(expected_system: str | None = None) -> str:
    config = load_config()
    municipalities = config.get("MUNICIPALITIES", {})
    if not isinstance(municipalities, dict) or not municipalities:
        raise ValueError("No municipalities are configured.")

    url_index = load_local_reiki_url_index()

    def slug_matches(slug: str) -> bool:
        entry = municipalities.get(slug)
        if not isinstance(entry, dict):
            return False
        if expected_system is None:
            return True
        code = str(entry.get("code", "")).strip()
        if code == "":
            return False
        return str(url_index.get(code, {}).get("system_type", "")).strip() == expected_system

    preferred_slug = str(config.get("DEFAULT_SLUG", "")).strip()
    if preferred_slug and preferred_slug in municipalities and slug_matches(preferred_slug):
        return preferred_slug

    for slug in municipalities.keys():
        if isinstance(slug, str) and slug.strip() and slug_matches(slug):
            return slug.strip()

    raise ValueError(f"No municipality found for system_type={expected_system!r}")


def load_reiki_target(slug: str, expected_system: str | None = None) -> dict:
    config = load_config()
    municipalities = config.get("MUNICIPALITIES", {})
    if not isinstance(municipalities, dict):
        raise ValueError("MUNICIPALITIES is not configured.")

    entry = municipalities.get(slug)
    if not isinstance(entry, dict):
        raise ValueError(f"Municipality slug not found in config: {slug}")

    code = str(entry.get("code", "")).strip()
    if code == "":
        raise ValueError(f"Municipality code is missing for slug: {slug}")

    reiki_config = entry.get("reiki", {})
    if not isinstance(reiki_config, dict):
        reiki_config = {}

    base_source_dir = str(reiki_config.get("source_dir", reiki_config.get("data_dir", f"reiki/{slug}/source"))).strip()
    clean_html_dir = str(reiki_config.get("clean_html_dir", f"reiki/{slug}/html")).strip()
    classification_dir = str(reiki_config.get("classification_dir", f"reiki/{slug}/json")).strip()
    image_dir = str(reiki_config.get("image_dir", f"reiki/{slug}/images")).strip()
    markdown_dir = str(reiki_config.get("markdown_dir", f"reiki/{slug}/markdown")).strip()    db_path = str(reiki_config.get("db_path", f"reiki/{slug}/ordinances.sqlite")).strip()

    url_index = load_local_reiki_url_index()
    url_entry = url_index.get(code)
    if not isinstance(url_entry, dict):
        raise ValueError(f"Municipality code {code} is missing from work/municipalities/reiki_system_urls.tsv")

    system_type = str(url_entry.get("system_type", "")).strip()
    if expected_system is not None and system_type != expected_system:
        raise ValueError(
            f"Municipality slug {slug} uses system_type={system_type!r}, expected {expected_system!r}"
        )

    name = str(entry.get("name", slug)).strip() or slug
    work_root = build_work_path(str(Path(base_source_dir).parent))

    return {
        "slug": slug,
        "name": name,
        "code": code,
        "system_type": system_type,
        "source_url": str(url_entry.get("url", "")).strip(),
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
