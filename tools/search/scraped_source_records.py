from __future__ import annotations

import gzip
import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

try:
    import japanese_search_tokenizer  # type: ignore
except Exception:  # pragma: no cover
    japanese_search_tokenizer = None


TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "cp932", "shift_jis", "euc_jp")
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
MINUTES_DATE_PATTERN = re.compile(
    r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?\s*([\d０-９]{1,2})月\s*([\d０-９]{1,2})日"
)
YEAR_LABEL_PATTERN = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?")
FILE_DATE_PATTERN = re.compile(r"([0-9]{2})月([0-9]{2})日")
REIKI_DATE_PATTERN = re.compile(r'<div class="law-date">.*?\((\d{4}-\d{2}-\d{2})\)</div>', re.IGNORECASE | re.DOTALL)
REIKI_TITLE_PATTERN = re.compile(r'<div class="law-title">([^<]+)</div>', re.IGNORECASE)
REIKI_NUMBER_PATTERN = re.compile(r'<div class="law-number">([^<]+)</div>', re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"[ \t\u3000]+")
LINEBREAK_PATTERN = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class MinutesSourceMeta:
    title: str
    year_label: str
    meeting_name_hint: str | None
    source_url: str
    source_year: int | None
    source_fino: int | None


@dataclass(frozen=True)
class MinuteRecord:
    rel_path: str
    title: str
    meeting_name: str | None
    year_label: str
    held_on: str | None
    gregorian_year: int | None
    month: int | None
    day: int | None
    doc_type: str
    ext: str
    source_fino: int | None
    source_year: int | None
    source_url: str | None
    content: str
    title_terms: str
    meeting_name_terms: str
    content_terms: str
    indexed_at: str


def read_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix.lower() == ".gz":
        return gzip.decompress(raw)
    return raw


def read_text_auto(path: Path) -> str:
    raw = read_bytes(path)
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text_auto(path))
    except Exception:
        return default


def logical_path(path: Path) -> Path:
    return path.with_suffix("") if path.suffix.lower() == ".gz" else path


def logical_suffix(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    return suffixes[-1] if suffixes else ""


def existing_path(path: Path) -> Path | None:
    candidates = [path]
    if path.suffix.lower() != ".gz":
        candidates.insert(0, path.with_name(path.name + ".gz"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def normalize_space(value: str) -> str:
    return SPACE_PATTERN.sub(" ", value).strip()


def terms_text(value: str) -> str:
    if value == "":
        return ""
    if japanese_search_tokenizer is not None:
        try:
            return str(japanese_search_tokenizer.document_terms_text(value)).strip()
        except Exception:
            pass
    return " ".join(part for part in re.split(r"[\s\u3000]+", value) if part)


def html_to_text(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", value, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|tr|table|section|article|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = TAG_PATTERN.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINEBREAK_PATTERN.sub("\n\n", text)
    return text.strip()


def markdown_to_text(value: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>*`\-\+\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINEBREAK_PATTERN.sub("\n\n", text)
    return text.strip()


def minutes_source_key(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if relative.suffix.lower() == ".gz":
        relative = relative.with_suffix("")
    return relative.with_suffix("").as_posix()


def choose_minutes_source_files(downloads_dir: Path) -> list[Path]:
    preferred: dict[str, Path] = {}
    for file_path in sorted(downloads_dir.rglob("*")):
        if not file_path.is_file():
            continue
        ext = logical_suffix(file_path)
        if ext not in {".txt", ".html", ".htm"}:
            continue
        rel_stem = minutes_source_key(file_path, downloads_dir)
        current = preferred.get(rel_stem)
        if current is None or (logical_suffix(current) != ".txt" and ext == ".txt"):
            preferred[rel_stem] = file_path
    return sorted(preferred.values())


def normalize_title(file_path: Path) -> str:
    logical = logical_path(file_path)
    return logical.stem.strip() or logical.name


def decode_query_component(value: str) -> str:
    if value == "":
        return ""
    try:
        raw = unquote_to_bytes(value)
    except Exception:
        return ""
    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return normalize_space(raw.decode(encoding))
        except Exception:
            continue
    return normalize_space(raw.decode("utf-8", errors="ignore"))


def raw_query_values(url: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for part in urlsplit(url).query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        values.setdefault(key, []).append(value)
    return values


def to_ascii_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value.strip())
    if raw == "元":
        return 1
    return int(raw) if raw.isdigit() else None


def era_to_gregorian(era: str, year_text: str) -> int | None:
    era_year = japanese_year_to_int(year_text)
    if era_year is None:
        return None
    base_year = ERA_BASE_YEAR.get(era)
    return None if base_year is None else base_year + era_year


def parse_optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def first_nonempty_lines(text: str, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        clean = normalize_space(line)
        if clean:
            lines.append(clean)
        if len(lines) >= limit:
            break
    return lines


def joined_head_text(text: str, limit: int = 8) -> str:
    return "\n".join(first_nonempty_lines(text, limit=limit))


def extract_year_label(text: str, fallback: str | None = None) -> str | None:
    match = YEAR_LABEL_PATTERN.search(joined_head_text(text, limit=6))
    if not match:
        return fallback
    label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
    if match.group(3):
        label += f"・{match.group(3)}元年"
    return label


def normalize_year_label_candidate(value: str) -> str | None:
    match = YEAR_LABEL_PATTERN.fullmatch(normalize_space(value))
    if not match:
        return None
    label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
    if match.group(3):
        label += f"・{match.group(3)}元年"
    return label


def extract_held_on(text: str, title: str, source_year: int | None) -> tuple[str | None, int | None, int | None, int | None]:
    match = MINUTES_DATE_PATTERN.search(joined_head_text(text, limit=20))
    if match:
        gregorian_year = era_to_gregorian(match.group(1), match.group(2))
        month = int(to_ascii_digits(match.group(4)))
        day = int(to_ascii_digits(match.group(5)))
        if gregorian_year is not None:
            return date(gregorian_year, month, day).isoformat(), gregorian_year, month, day
    match = FILE_DATE_PATTERN.search(title)
    if match and source_year is not None:
        month = int(match.group(1))
        day = int(match.group(2))
        return date(source_year, month, day).isoformat(), source_year, month, day
    return None, source_year, None, None


def extract_meeting_name(text: str) -> str | None:
    lines = first_nonempty_lines(text, limit=5)
    if len(lines) >= 2:
        second = lines[1]
        if "－" not in second and len(second) >= 4:
            return second
    if lines:
        first = re.sub(r"－[^－]+$", "", lines[0]).strip()
        if len(first) >= 4:
            return first
    return None


def looks_like_minutes_listing_page(text: str) -> bool:
    head = joined_head_text(text, limit=12)
    markers = (
        "会議日程一覧",
        "会議検索結果一覧",
        "件の日程がヒットしました",
        "をクリックすると発言者を表示します",
    )
    matched = sum(1 for marker in markers if marker in head)
    return matched >= 2 or ("会議日程一覧" in head and re.search(r"\d+件の日程がヒットしました", head) is not None)


def trim_meta_meeting_name(label: str, title: str) -> str:
    trimmed = normalize_space(label)
    trimmed = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", trimmed)
    trimmed = re.sub(rf"[｜|－-]\s*{re.escape(normalize_space(title))}$", "", trimmed).strip()
    return normalize_space(trimmed)


def extract_meta_meeting_name(source_url: str, title: str) -> str | None:
    query = raw_query_values(source_url)
    title_hint = decode_query_component((query.get("TITL") or [""])[0])
    title_subt = decode_query_component((query.get("TITL_SUBT") or [""])[0])
    candidates = [value for value in [title_hint, trim_meta_meeting_name(title_subt, title) if title_subt else ""] if value]
    for candidate in candidates:
        if candidate != normalize_space(title):
            return candidate
    return None


def classify_doc_type(title: str, text: str, *, ext: str = "") -> str:
    if normalize_space(title).endswith("目次"):
        return "toc"
    if "会議録目次" in joined_head_text(text, limit=6):
        return "toc"
    if ext.lower() in {".html", ".htm"} and looks_like_minutes_listing_page(text):
        return "aux"
    return "minutes"


def parse_minutes_source_meta(index_json: Path) -> dict[tuple[str, str, str], MinutesSourceMeta]:
    rows = load_json(index_json, [])
    metas: dict[tuple[str, str, str], MinutesSourceMeta] = {}
    if not isinstance(rows, list):
        return metas
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "")).strip()
        year_label = normalize_space(str(row.get("year_label", "")))
        source_url = str(row.get("url", "")).strip()
        if title == "" or year_label == "" or source_url == "":
            continue
        query = parse_qs(urlsplit(source_url).query)
        source_year = parse_optional_int(row.get("source_year")) or parse_optional_int((query.get("YEAR") or [None])[0])
        source_fino = parse_optional_int(row.get("source_fino")) or parse_optional_int((query.get("FINO") or [None])[0])
        meeting_name_hint = normalize_space(str(row.get("meeting_name", "") or row.get("meeting_group", ""))) or None
        if meeting_name_hint is None:
            meeting_name_hint = extract_meta_meeting_name(source_url, title)
        meta = MinutesSourceMeta(title, year_label, meeting_name_hint, source_url, source_year, source_fino)
        metas.setdefault((year_label, title, normalize_space(meeting_name_hint or "")), meta)
        metas.setdefault((year_label, title, ""), meta)
    return metas


def fallback_year_label_from_path(file_path: Path, downloads_dir: Path) -> str | None:
    for parent in file_path.parents:
        if parent == downloads_dir:
            break
        label = normalize_year_label_candidate(parent.name)
        if label:
            return label
    if file_path.parent != downloads_dir:
        return normalize_space(file_path.parent.name) or None
    return None


def build_minutes_record(
    file_path: Path,
    downloads_dir: Path,
    meta_map: dict[tuple[str, str, str], MinutesSourceMeta],
    indexed_at: str,
) -> MinuteRecord | None:
    ext = logical_suffix(file_path)
    title = normalize_title(file_path)
    try:
        raw_text = read_text_auto(file_path)
    except Exception:
        return None
    content = html_to_text(raw_text) if ext in {".html", ".htm"} else raw_text.strip()
    if content == "":
        return None
    fallback_year_label = fallback_year_label_from_path(file_path, downloads_dir)
    extracted_year_label = extract_year_label(content, fallback=fallback_year_label) or fallback_year_label or "不明"
    meeting_name = extract_meeting_name(content)
    meta = meta_map.get((extracted_year_label, title, normalize_space(meeting_name or "")))
    if meta is None:
        meta = meta_map.get((extracted_year_label, title, ""))
    held_on, gregorian_year, month, day = extract_held_on(content, title, meta.source_year if meta else None)
    return MinuteRecord(
        rel_path=file_path.relative_to(downloads_dir).as_posix(),
        title=title,
        meeting_name=meeting_name,
        year_label=meta.year_label if meta else extracted_year_label,
        held_on=held_on,
        gregorian_year=gregorian_year,
        month=month,
        day=day,
        doc_type=classify_doc_type(title, content, ext=ext),
        ext=ext,
        source_fino=meta.source_fino if meta else None,
        source_year=meta.source_year if meta else gregorian_year,
        source_url=meta.source_url if meta else None,
        content=content,
        title_terms=terms_text(title),
        meeting_name_terms=terms_text(meeting_name or ""),
        content_terms=terms_text(content),
        indexed_at=indexed_at,
    )


def reiki_logical_key_from_path(path: Path, root: Path) -> str:
    return logical_path(path).relative_to(root).with_suffix("").as_posix()


def reiki_logical_key_from_string(value: str) -> str:
    return logical_path(Path(str(value).replace("\\", "/"))).with_suffix("").as_posix()


def collect_reiki_preferred_files(root: Path, suffixes: set[str]) -> dict[str, Path]:
    preferred: dict[str, Path] = {}
    if not root.exists():
        return preferred
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        logical = logical_path(path)
        if logical.suffix.lower() not in suffixes:
            continue
        key = reiki_logical_key_from_path(path, root)
        current = preferred.get(key)
        if current is None or (current.suffix.lower() == ".gz" and path.suffix.lower() != ".gz"):
            preferred[key] = path
    return preferred


def load_reiki_manifest_index(path: Path) -> dict[str, dict[str, Any]]:
    rows = load_json(path, [])
    if not isinstance(rows, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_file = str(row.get("source_file") or row.get("stored_source_file") or "").strip()
        if source_file == "":
            continue
        key = reiki_logical_key_from_string(source_file)
        if key:
            index[key] = row
            index.setdefault(Path(key).name, row)
    return index


def build_alias_map(files: dict[str, Path]) -> dict[str, Path]:
    alias: dict[str, Path] = {}
    for key, path in files.items():
        alias[key] = path
        alias.setdefault(Path(key).name, path)
    return alias


def decode_html_text(value: object) -> str:
    return html.unescape(str(value or "")).strip()


def extract_title_from_html(html_content: str, fallback: str) -> str:
    match = REIKI_TITLE_PATTERN.search(html_content)
    return decode_html_text(match.group(1)) if match else fallback


def extract_number_from_html(html_content: str) -> str:
    match = REIKI_NUMBER_PATTERN.search(html_content)
    return decode_html_text(match.group(1)) if match else ""


def extract_date_from_html(html_content: str) -> str:
    match = REIKI_DATE_PATTERN.search(html_content)
    return match.group(1) if match else ""


def join_strings(value: object) -> str:
    if isinstance(value, list):
        return ",".join(normalize_space(decode_html_text(item)) for item in value if normalize_space(decode_html_text(item)))
    return normalize_space(decode_html_text(value))


def normalize_kana(value: str, prefixes: list[str]) -> str:
    normalized = value.strip()
    for prefix in prefixes:
        if prefix and normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def safe_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def safe_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def normalize_document_type(value: str) -> str:
    text = normalize_space(value)
    return text if text in {"条例", "規則", "規程", "要綱"} else "その他"


def detect_document_type(title: str, number: str) -> str:
    for candidate in (number, title):
        if "条例" in candidate:
            return "条例"
        if "規則" in candidate:
            return "規則"
        if "規程" in candidate or "訓令" in candidate:
            return "規程"
        if "要綱" in candidate:
            return "要綱"
    return "その他"


def record_updated_at(*paths: Path | None) -> str:
    mtimes = [path.stat().st_mtime for path in paths if path is not None and path.exists()]
    if not mtimes:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(max(mtimes), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def reiki_sortable_prefixes(target: dict[str, Any]) -> list[str]:
    name_kana = normalize_space(str(target.get("name_kana", "")).strip()).replace(" ", "")
    return [name_kana] if name_kana else []


def build_reiki_record(
    key: str,
    html_path: Path,
    markdown_path: Path | None,
    classification_path: Path | None,
    manifest: dict[str, Any] | None,
    prefixes: list[str],
) -> dict[str, Any] | None:
    html_content = read_text_auto(html_path)
    content_text = html_to_text(html_content)
    if content_text == "" and markdown_path is not None and markdown_path.exists():
        content_text = markdown_to_text(read_text_auto(markdown_path))
    if content_text == "":
        return None
    classification = load_json(classification_path, {}) if classification_path is not None else {}
    if not isinstance(classification, dict):
        classification = {}
    manifest = manifest if isinstance(manifest, dict) else {}
    fallback_title = normalize_space(str(manifest.get("title", "")).strip()) or Path(key).name
    title = normalize_space(decode_html_text(classification.get("title", "")) or extract_title_from_html(html_content, fallback_title))
    if title == "":
        title = Path(key).name
    number = normalize_space(
        decode_html_text(classification.get("number", ""))
        or extract_number_from_html(html_content)
        or decode_html_text(manifest.get("number", ""))
    )
    reading_kana = normalize_space(decode_html_text(classification.get("readingKana", ""))) or title
    lens_eval = classification.get("lensEvaluation", {})
    lens_eval = lens_eval if isinstance(lens_eval, dict) else {}
    lens_a = lens_eval.get("lensA", {})
    lens_a = lens_a if isinstance(lens_a, dict) else {}
    lens_b = lens_eval.get("lensB", {})
    lens_b = lens_b if isinstance(lens_b, dict) else {}
    combined = lens_eval.get("combined", {})
    combined = combined if isinstance(combined, dict) else {}
    document_type = normalize_document_type(str(classification.get("documentType", "")).strip())
    if document_type == "その他":
        document_type = detect_document_type(title, number)
    responsible_department = normalize_space(str(classification.get("responsibleDepartment", "")).strip())
    combined_reason = normalize_space(str(combined.get("reason", "")).strip())
    reason = normalize_space(str(classification.get("reason", "")).strip())
    taxonomy_path = normalize_space(str(manifest.get("taxonomy_path", "")).strip())
    return {
        "filename": key,
        "title": title,
        "number": number,
        "reading_kana": reading_kana,
        "sortable_kana": normalize_kana(reading_kana, prefixes),
        "primary_class": normalize_space(str(classification.get("primaryClass", "")).strip()),
        "secondary_tags": join_strings(classification.get("secondaryTags", [])),
        "necessity_score": safe_int(classification.get("necessityScore", -1), -1),
        "fiscal_impact_score": safe_float(classification.get("fiscalImpactScore", 0.0), 0.0),
        "regulatory_burden_score": safe_float(classification.get("regulatoryBurdenScore", 0.0), 0.0),
        "policy_effectiveness_score": safe_float(classification.get("policyEffectivenessScore", 0.0), 0.0),
        "lens_tags": join_strings(classification.get("lensTags", [])),
        "lens_a_stance": normalize_space(str(lens_a.get("stance", "")).strip()),
        "lens_b_stance": normalize_space(str(lens_b.get("stance", "")).strip()),
        "combined_stance": normalize_space(str(combined.get("stance", "")).strip()),
        "combined_reason": combined_reason,
        "document_type": document_type,
        "responsible_department": responsible_department,
        "reason": reason,
        "enactment_date": normalize_space(extract_date_from_html(html_content) or str(manifest.get("enactment_date", "")).strip()),
        "analyzed_at": normalize_space(str(classification.get("analyzedAt", "")).strip()),
        "updated_at": record_updated_at(html_path, markdown_path, classification_path),
        "source_url": normalize_space(str(manifest.get("detail_url") or manifest.get("source_url") or "").strip()),
        "source_file": normalize_space(str(manifest.get("source_file", "")).strip()),
        "taxonomy_path": taxonomy_path,
        "taxonomy_paths": join_strings(manifest.get("taxonomy_paths", [])),
        "content_text": content_text,
        "content_length": len(content_text),
        "title_terms": terms_text(title),
        "reading_terms": terms_text(reading_kana),
        "content_terms": terms_text(content_text),
        "department_terms": terms_text(responsible_department),
        "combined_reason_terms": terms_text(combined_reason),
        "reason_terms": terms_text(reason),
        "secondary_terms": terms_text(join_strings(classification.get("secondaryTags", []))),
        "lens_terms": terms_text(join_strings(classification.get("lensTags", []))),
        "taxonomy_terms": terms_text(taxonomy_path),
        "has_classification": bool(classification_path is not None and classification_path.exists()),
    }
