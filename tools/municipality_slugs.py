from __future__ import annotations

import re
from urllib.parse import urlsplit


def sanitize_slug_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    token = re.sub(r"-{2,}", "-", token)
    return token


def homepage_slug_token(homepage_url: str) -> str:
    host = (urlsplit(homepage_url).hostname or "").lower().strip(".")
    labels = [label for label in host.split(".") if label]
    while labels and re.fullmatch(r"www\d*", labels[0]):
        labels = labels[1:]
    if not labels:
        return ""

    generic_labels = {"city", "town", "village", "vill", "pref", "metro", "lg", "gov"}
    if len(labels) >= 2 and labels[0] in generic_labels:
        return sanitize_slug_token(labels[1])
    return sanitize_slug_token(labels[0])


def tenant_slug_token(source_url: str) -> str:
    parts = urlsplit(source_url)
    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0].lower() == "tenant":
        return sanitize_slug_token(path_parts[1])
    return ""


def dbsr_slug_token(source_url: str) -> str:
    host = (urlsplit(source_url).hostname or "").lower().strip(".")
    if not host.endswith(".dbsr.jp"):
        return ""

    labels = [label for label in host.split(".") if label]
    if len(labels) < 3:
        return ""

    core = labels[:-2]
    while core and re.fullmatch(r"www\d*", core[0]):
        core = core[1:]
    if not core:
        return ""

    if core[0] in {"city", "town", "village", "pref", "ward"} and len(core) >= 2:
        return sanitize_slug_token(core[1])
    return sanitize_slug_token(core[0])


def host_slug_token(source_url: str) -> str:
    host = (urlsplit(source_url).hostname or "").lower()
    host_label = sanitize_slug_token(host.split(".", 1)[0])
    if host_label == "" or re.fullmatch(r"www\d*", host_label):
        return "municipality"
    return host_label


def preferred_romaji_token(source_url: str, homepage_url: str = "") -> str:
    homepage_token = homepage_slug_token(homepage_url)
    if homepage_token:
        return homepage_token

    tenant_token = tenant_slug_token(source_url)
    if tenant_token:
        return tenant_token

    dbsr_token = dbsr_slug_token(source_url)
    if dbsr_token:
        return dbsr_token

    return host_slug_token(source_url)


def code_name_slug(code: str, source_url: str, homepage_url: str = "") -> str:
    normalized_code = re.sub(r"[^0-9]", "", code)
    if normalized_code == "":
        normalized_code = "00000"
    token = preferred_romaji_token(source_url, homepage_url)
    return f"{normalized_code}-{token}"
