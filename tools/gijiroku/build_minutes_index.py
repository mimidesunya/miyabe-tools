#!/usr/bin/env python3
"""Build a lean SQLite full-text index for scraped Kawasaki council minutes."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
DATE_PATTERN = re.compile(
    r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?\s*([\d０-９]{1,2})月\s*([\d０-９]{1,2})日"
)
YEAR_LABEL_PATTERN = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?")
FILE_DATE_PATTERN = re.compile(r"([0-9]{2})月([0-9]{2})日")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_config(root: Path) -> dict:
    for candidate in (root / "data" / "config.json", root / "data" / "config.example.json"):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def data_path(root: Path, relative: str) -> Path:
    return root / "data" / Path(relative.replace("\\", "/"))


def default_slug(root: Path) -> str:
    config = load_config(root)
    value = str(config.get("DEFAULT_SLUG", "")).strip()
    if value:
        return value
    municipalities = config.get("MUNICIPALITIES", {})
    if isinstance(municipalities, dict) and municipalities:
        first = next(iter(municipalities.keys()), "")
        if isinstance(first, str):
            return first.strip()
    return "kawasaki"


def municipality_gijiroku_paths(root: Path, slug: str) -> tuple[Path, Path, Path]:
    config = load_config(root)
    municipalities = config.get("MUNICIPALITIES", {})
    entry = municipalities.get(slug, {}) if isinstance(municipalities, dict) else {}
    feature = entry.get("gijiroku", {}) if isinstance(entry, dict) else {}
    if not isinstance(feature, dict):
        feature = {}

    if slug == "kawasaki":
        default_data_dir = "gijiroku/kawasaki_council"
    else:
        default_data_dir = f"gijiroku/{slug}"

    data_dir_rel = str(feature.get("data_dir", default_data_dir)).strip()
    downloads_rel = str(feature.get("downloads_dir", f"{data_dir_rel}/downloads")).strip()
    index_json_rel = str(feature.get("index_json_path", f"{data_dir_rel}/meetings_index.json")).strip()
    output_db_rel = str(feature.get("db_path", f"{data_dir_rel}/minutes.sqlite")).strip()

    return (
        data_path(root, downloads_rel),
        data_path(root, index_json_rel),
        data_path(root, output_db_rel),
    )


@dataclass(frozen=True)
class SourceMeta:
    title: str
    year_label: str
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
    indexed_at: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scraped議事録をSQLite FTS5に登録します。"
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="自治体slug。未指定時は config の DEFAULT_SLUG を使います。",
    )
    parser.add_argument(
        "--downloads-dir",
        type=Path,
        default=None,
        help="スクレイプ済みファイルのディレクトリ",
    )
    parser.add_argument(
        "--index-json",
        type=Path,
        default=None,
        help="meetings_index.json のパス（任意）",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=None,
        help="作成するSQLite DBパス",
    )
    args = parser.parse_args()
    root = project_root()
    args.slug = (args.slug or default_slug(root)).strip()
    default_downloads, default_index_json, default_output_db = municipality_gijiroku_paths(root, args.slug)
    if args.downloads_dir is None:
        args.downloads_dir = default_downloads
    if args.index_json is None:
        args.index_json = default_index_json
    if args.output_db is None:
        args.output_db = default_output_db
    return args


def read_text_auto(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def html_to_text(html: str) -> str:
    text = re.sub(r"<script[\\s\\S]*?</script>", "", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\\s\\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_title(file_path: Path) -> str:
    return file_path.stem.strip() or file_path.name


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", value).strip()


def to_ascii_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value.strip())
    if raw == "元":
        return 1
    if raw.isdigit():
        return int(raw)
    return None


def era_to_gregorian(era: str, year_text: str) -> int | None:
    era_year = japanese_year_to_int(year_text)
    if era_year is None:
        return None
    base_year = ERA_BASE_YEAR.get(era)
    if base_year is None:
        return None
    return base_year + era_year


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text)


def first_nonempty_lines(text: str, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        clean = normalize_space(line)
        if clean:
            lines.append(clean)
        if len(lines) >= limit:
            break
    return lines


def extract_year_label(text: str, fallback: str | None = None) -> str | None:
    head = "\n".join(first_nonempty_lines(text, limit=6))
    match = YEAR_LABEL_PATTERN.search(head)
    if match:
        label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
        if match.group(3):
            label += f"・{match.group(3)}元年"
        return label
    return fallback


def extract_held_on(text: str, title: str, source_year: int | None) -> tuple[str | None, int | None, int | None, int | None]:
    head = "\n".join(first_nonempty_lines(text, limit=20))
    match = DATE_PATTERN.search(head)
    if match:
        gregorian_year = era_to_gregorian(match.group(1), match.group(2))
        month = int(to_ascii_digits(match.group(4)))
        day = int(to_ascii_digits(match.group(5)))
        if gregorian_year is not None:
            held_on = date(gregorian_year, month, day).isoformat()
            return held_on, gregorian_year, month, day

    match = FILE_DATE_PATTERN.search(title)
    if match and source_year is not None:
        month = int(match.group(1))
        day = int(match.group(2))
        held_on = date(source_year, month, day).isoformat()
        return held_on, source_year, month, day

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


def classify_doc_type(title: str, text: str) -> str:
    if title.endswith("目次"):
        return "toc"
    head = "\n".join(first_nonempty_lines(text, limit=6))
    if "会議録目次" in head:
        return "toc"
    return "minutes"


def parse_source_meta(index_json: Path) -> dict[tuple[str, str], SourceMeta]:
    if not index_json.exists():
        return {}

    try:
        rows = json.loads(index_json.read_text(encoding="utf-8"))
    except Exception:
        return {}

    metas: dict[tuple[str, str], SourceMeta] = {}
    if not isinstance(rows, list):
        return metas

    for row in rows:
        if not isinstance(row, dict):
            continue

        title = str(row.get("title", "")).strip()
        year_label = normalize_space(str(row.get("year_label", "")))
        source_url = str(row.get("url", "")).strip()
        if not title or not year_label or not source_url:
            continue

        query = parse_qs(urlsplit(source_url).query)
        source_year = parse_optional_int(query.get("YEAR", [None])[0])
        source_fino = parse_optional_int(query.get("FINO", [None])[0])
        key = (year_label, title)
        metas.setdefault(
            key,
            SourceMeta(
                title=title,
                year_label=year_label,
                source_url=source_url,
                source_year=source_year,
                source_fino=source_fino,
            ),
        )
    return metas


def choose_source_files(downloads_dir: Path) -> list[Path]:
    preferred: dict[str, Path] = {}
    for file_path in sorted(downloads_dir.rglob("*")):
        if not file_path.is_file():
            continue

        ext = file_path.suffix.lower()
        if ext not in {".txt", ".html", ".htm"}:
            continue

        rel_stem = file_path.relative_to(downloads_dir).with_suffix("").as_posix()
        current = preferred.get(rel_stem)
        if current is None:
            preferred[rel_stem] = file_path
            continue

        if current.suffix.lower() != ".txt" and ext == ".txt":
            preferred[rel_stem] = file_path
    return sorted(preferred.values())


def build_record(file_path: Path, downloads_dir: Path, meta_map: dict[tuple[str, str], SourceMeta], indexed_at: str) -> MinuteRecord | None:
    ext = file_path.suffix.lower()
    title = normalize_title(file_path)

    try:
        raw_text = read_text_auto(file_path)
    except Exception:
        return None

    content = html_to_text(raw_text) if ext in {".html", ".htm"} else raw_text.strip()
    if not content:
        return None

    fallback_year_label = file_path.parent.name if file_path.parent != downloads_dir else None
    extracted_year_label = extract_year_label(content, fallback=fallback_year_label)
    if extracted_year_label is None:
        extracted_year_label = fallback_year_label or "不明"

    meta = meta_map.get((extracted_year_label, title))
    year_label = meta.year_label if meta else extracted_year_label
    held_on, gregorian_year, month, day = extract_held_on(content, title, meta.source_year if meta else None)
    meeting_name = extract_meeting_name(content)
    doc_type = classify_doc_type(title, content)
    rel_path = file_path.relative_to(downloads_dir).as_posix()

    return MinuteRecord(
        rel_path=rel_path,
        title=title,
        meeting_name=meeting_name,
        year_label=year_label,
        held_on=held_on,
        gregorian_year=gregorian_year,
        month=month,
        day=day,
        doc_type=doc_type,
        ext=ext,
        source_fino=meta.source_fino if meta else None,
        source_year=meta.source_year if meta else gregorian_year,
        source_url=meta.source_url if meta else None,
        content=content,
        indexed_at=indexed_at,
    )


def schema_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(schema_path().read_text(encoding="utf-8"))


def build_index(downloads_dir: Path, index_json: Path, output_db: Path) -> tuple[int, int]:
    if not downloads_dir.exists():
        raise FileNotFoundError(f"downloads dir not found: {downloads_dir}")

    output_db.parent.mkdir(parents=True, exist_ok=True)
    meta_map = parse_source_meta(index_json)

    conn = sqlite3.connect(output_db)
    init_db(conn)

    indexed = 0
    skipped = 0
    indexed_at = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()

    for file_path in choose_source_files(downloads_dir):
        record = build_record(file_path, downloads_dir, meta_map, indexed_at)
        if record is None:
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO minutes (
                rel_path, title, meeting_name, year_label, held_on,
                gregorian_year, month, day, doc_type, ext,
                source_fino, source_year, source_url, content, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.rel_path,
                record.title,
                record.meeting_name,
                record.year_label,
                record.held_on,
                record.gregorian_year,
                record.month,
                record.day,
                record.doc_type,
                record.ext,
                record.source_fino,
                record.source_year,
                record.source_url,
                record.content,
                record.indexed_at,
            ),
        )
        row_id = cur.lastrowid

        if record.doc_type == "minutes":
            cur.execute(
                "INSERT INTO minutes_fts (rowid, title, meeting_name, content) VALUES (?, ?, ?, ?)",
                (row_id, record.title, record.meeting_name or "", record.content),
            )

        indexed += 1

    conn.commit()
    conn.close()
    return indexed, skipped


def main() -> int:
    args = parse_args()
    indexed, skipped = build_index(args.downloads_dir, args.index_json, args.output_db)
    print(f"[DONE] indexed={indexed} skipped={skipped} db={args.output_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
