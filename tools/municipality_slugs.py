from __future__ import annotations

import re
from urllib.parse import urlsplit


KNOWN_ENTITY_SUFFIXES = ("-shi", "-ku", "-cho", "-machi", "-mura", "-son", "-to", "-do", "-fu", "-ken")
ROMAJI_OVERRIDES_BY_CODE = {
    "01000": "hokkaido",
    "01695": "shikotan-mura",
    "01696": "tomari-mura",
    "01697": "ruyobetsu-mura",
    "01698": "rubetsu-mura",
    "01699": "shana-mura",
    "01700": "shibetoro-mura",
}


def normalized_jis_code(code: str) -> str:
    normalized = re.sub(r"[^0-9]", "", code)
    return normalized or "00000"


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


def apply_entity_suffix(token: str, name: str, entity_type: str) -> str:
    normalized = sanitize_slug_token(token)
    if normalized == "":
        return ""
    if normalized.endswith(KNOWN_ENTITY_SUFFIXES):
        return normalized

    normalized_name = str(name).strip()
    normalized_entity_type = str(entity_type).strip()

    if normalized_entity_type == "prefecture":
        if normalized_name == "北海道":
            return normalized
        if normalized_name.endswith("都"):
            return f"{normalized}-to"
        if normalized_name.endswith("府"):
            return f"{normalized}-fu"
        if normalized_name.endswith("県"):
            return f"{normalized}-ken"
        return normalized

    if normalized_entity_type == "special_ward" or normalized_name.endswith("区"):
        return f"{normalized}-ku"
    if normalized_name.endswith("市"):
        return f"{normalized}-shi"
    if normalized_name.endswith("町"):
        return f"{normalized}-cho"
    if normalized_name.endswith("村"):
        return f"{normalized}-mura"
    return normalized


def preferred_name_romaji(
    *,
    code: str,
    name: str,
    entity_type: str,
    source_url: str = "",
    homepage_url: str = "",
    name_romaji: str = "",
) -> str:
    normalized_code = re.sub(r"[^0-9]", "", code)
    if normalized_code in ROMAJI_OVERRIDES_BY_CODE:
        return ROMAJI_OVERRIDES_BY_CODE[normalized_code]

    explicit_name_romaji = sanitize_slug_token(name_romaji)
    if explicit_name_romaji:
        return explicit_name_romaji

    base_token = preferred_romaji_token(source_url, homepage_url)
    if base_token:
        return apply_entity_suffix(base_token, name, entity_type)

    return ""


def code_name_slug(
    code: str,
    source_url: str,
    homepage_url: str = "",
    *,
    name: str = "",
    entity_type: str = "",
    name_romaji: str = "",
) -> str:
    normalized_code = normalized_jis_code(code)
    token = code_name_slug_token(
        code=normalized_code,
        source_url=source_url,
        homepage_url=homepage_url,
        name=name,
        entity_type=entity_type,
        name_romaji=name_romaji,
    )
    return f"{normalized_code}-{token}"


def code_name_slug_token(
    code: str,
    source_url: str,
    homepage_url: str = "",
    *,
    name: str = "",
    entity_type: str = "",
    name_romaji: str = "",
) -> str:
    normalized_code = normalized_jis_code(code)
    token = preferred_name_romaji(
        code=normalized_code,
        name=name,
        entity_type=entity_type,
        source_url=source_url,
        homepage_url=homepage_url,
        name_romaji=name_romaji,
    )
    if token == "":
        token = preferred_romaji_token(source_url, homepage_url)
    return sanitize_slug_token(token) or "municipality"
