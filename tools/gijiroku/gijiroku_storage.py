"""Storage primitives for scraper outputs.

Scrapers write compressed text/JSON through this module so replacement,
archiving, encoding fallback, and digest calculation stay consistent across
different source systems.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


TEXT_ENCODINGS = ("utf-8", "cp932", "shift_jis", "euc_jp")
ARCHIVE_MARKER = "_archive"


def gzip_path(path: Path) -> Path:
    return path if path.suffix.lower() == ".gz" else path.with_name(path.name + ".gz")


def logical_path(path: Path) -> Path:
    return path.with_suffix("") if path.suffix.lower() == ".gz" else path


def existing_output(path: Path) -> Path | None:
    candidates = [gzip_path(path)]
    if gzip_path(path) != path:
        candidates.append(path)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def existing_named_outputs(directory: Path, stem: str) -> list[Path]:
    try:
        if not directory.exists():
            return []
    except OSError:
        return []
    try:
        return sorted(
            [path for path in directory.glob(stem + ".*") if path.is_file()],
            key=lambda path: path.name,
        )
    except OSError:
        return []


def archive_root_for(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve()
    parts = resolved.parts
    for marker in ("gijiroku", "reiki"):
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
    # Keep replaced files near the municipality data they came from.  This makes
    # remote debugging possible without requiring a separate backup location.
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
    return raw.decode("utf-8", errors="ignore")


def write_bytes(path: Path, data: bytes, *, compress: bool = False) -> Path:
    final_path = gzip_path(path) if compress else path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    existing = existing_output(path)
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
    data = text.encode(encoding)
    return write_bytes(path, data, compress=compress)


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


def logical_suffix(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    return suffixes[-1] if suffixes else ""


def source_key(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if relative.suffix.lower() == ".gz":
        relative = relative.with_suffix("")
    return relative.with_suffix("").as_posix()


def item_signature(payload: Any) -> str:
    if is_dataclass(payload):
        payload = asdict(payload)
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def disambiguated_stem(stem: str, discriminator: str, occurrence_index: int) -> str:
    """Keep the first output path stable and suffix later same-name collisions."""
    stem = str(stem).strip() or "meeting"
    if occurrence_index <= 0:
        return stem
    token = hashlib.sha1(str(discriminator or stem).encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{token}"


def load_state(path: Path) -> dict[str, Any]:
    state = load_json(path, {"version": 1, "items": {}})
    if not isinstance(state, dict):
        return {"version": 1, "items": {}}
    if not isinstance(state.get("items"), dict):
        state["items"] = {}
    state.setdefault("version", 1)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def update_progress_state(path: Path, *, current: int, total: int, unit: str = "meeting") -> None:
    state = load_state(path)
    state["progress_current"] = max(0, int(current))
    state["progress_total"] = max(0, int(total))
    state["progress_unit"] = str(unit).strip() or "meeting"
    save_state(path, state)
