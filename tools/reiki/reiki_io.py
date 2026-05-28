"""Storage and encoding helpers for reiki scraper outputs.

The reiki scrapers produce source HTML, normalized HTML, Markdown, manifest
files, and JSON metadata.  Centralizing IO here keeps compression and archive
behavior aligned with the minutes side while allowing reiki-specific paths.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "cp932", "shift_jis", "euc_jp")
ARCHIVE_MARKER = "_archive"


def gzip_path(path: Path) -> Path:
    return path if path.suffix.lower() == ".gz" else path.with_name(path.name + ".gz")


def logical_path(path: Path) -> Path:
    return path.with_suffix("") if path.suffix.lower() == ".gz" else path


def archive_root_for(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve()
    parts = resolved.parts
    for marker in ("reiki", "gijiroku"):
        if marker not in parts:
            continue
        index = len(parts) - 1 - list(reversed(parts)).index(marker)
        if index + 1 >= len(parts) - 1:
            continue
        base = Path(*parts[: index + 2])
        try:
            return base / ARCHIVE_MARKER, resolved.relative_to(base)
        except ValueError:
            continue
    return resolved.parent / ARCHIVE_MARKER, Path(resolved.name)


def archive_existing_file(path: Path, *, reason: str = "replace") -> Path | None:
    # Replacement is common during update checks.  Archive the old artifact next
    # to the municipality tree so a bad scrape can be inspected after the fact.
    try:
        candidate = path.resolve()
        if ARCHIVE_MARKER in candidate.parts or not candidate.is_file():
            return None
        archive_root, relative = archive_root_for(candidate)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_reason = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in reason).strip("_") or "replace"
        destination = archive_root / f"{stamp}_{safe_reason}" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, destination)
        return destination
    except Exception as exc:
        print(f"[WARN] failed to archive old file before {reason}: {path} [{type(exc).__name__}] {exc}", flush=True)
        return None


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


def write_bytes(path: Path, data: bytes, *, compress: bool = False) -> Path:
    final_path = gzip_path(path) if compress else path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    existing = existing_path(path)
    archived_existing: Path | None = None
    if existing is not None:
        try:
            if read_bytes(existing) != data:
                archive_existing_file(existing, reason="overwrite")
                archived_existing = existing.resolve()
        except Exception:
            archive_existing_file(existing, reason="overwrite")
            archived_existing = existing.resolve()
    if compress:
        with gzip.open(final_path, "wb", compresslevel=6) as handle:
            handle.write(data)
        plain_path = logical_path(final_path)
        if plain_path != final_path and plain_path.exists():
            if archived_existing != plain_path.resolve():
                archive_existing_file(plain_path, reason="delete")
            plain_path.unlink()
    else:
        final_path.write_bytes(data)
        gz_path = gzip_path(final_path)
        if gz_path != final_path and gz_path.exists():
            if archived_existing != gz_path.resolve():
                archive_existing_file(gz_path, reason="delete")
            gz_path.unlink()
    return final_path


def write_text(path: Path, text: str, *, encoding: str = "utf-8", compress: bool = False) -> Path:
    return write_bytes(path, text.encode(encoding), compress=compress)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text_auto(path))
    except Exception:
        return default


def write_json(path: Path, payload: Any, *, compress: bool = False) -> Path:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return write_text(path, text + "\n", compress=compress)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(read_bytes(path))


def existing_path(path: Path) -> Path | None:
    candidates = [path]
    gz_candidate = gzip_path(path)
    if gz_candidate != path:
        candidates.insert(0, gz_candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def collect_matching_files(root: Path, patterns: list[str]) -> list[Path]:
    found: dict[Path, None] = {}
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path.is_file():
                found[path] = None
    return sorted(found.keys())


def save_state(path: Path, payload: dict[str, Any]) -> None:
    # 進捗 JSON は親バッチがポーリングで読むので、途中の壊れた内容を見せないよう原子的に差し替える。
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def update_progress_state(path: Path, *, current: int, total: int, unit: str = "ordinance") -> None:
    save_state(
        path,
        {
            "version": 1,
            "progress_current": max(0, int(current)),
            "progress_total": max(0, int(total)),
            "progress_unit": str(unit).strip() or "ordinance",
        },
    )
