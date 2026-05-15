#!/usr/bin/env python3
"""OpenSearch index settings and mappings for Miyabe document search."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


SEARCH_TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = SEARCH_TOOL_DIR / "index_settings.json"
DEFAULT_MAPPINGS_PATH = SEARCH_TOOL_DIR / "index_mappings.json"


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"JSON object expected: {path}")
    return loaded


def load_index_settings(
    *,
    shards: int = 1,
    replicas: int = 0,
    refresh_interval: str = "-1",
    path: Path = DEFAULT_SETTINGS_PATH,
) -> dict[str, Any]:
    settings = copy.deepcopy(_load_json(path).get("settings", {}))
    index_settings = settings.setdefault("index", {})
    index_settings["number_of_shards"] = max(1, int(shards))
    index_settings["number_of_replicas"] = max(0, int(replicas))
    index_settings["refresh_interval"] = str(refresh_interval)
    return settings


def load_index_mappings(path: Path = DEFAULT_MAPPINGS_PATH) -> dict[str, Any]:
    return copy.deepcopy(_load_json(path))


def build_index_body(
    *,
    shards: int = 1,
    replicas: int = 0,
    refresh_interval: str = "-1",
) -> dict[str, Any]:
    return {
        "settings": load_index_settings(
            shards=shards,
            replicas=replicas,
            refresh_interval=refresh_interval,
        ),
        "mappings": load_index_mappings(),
    }
