"""例規集スクレイパ成果物の保存・文字コード処理ヘルパ。

例規集スクレイパは source HTML、正規化 HTML、Markdown、manifest、JSON メタデータを
生成する。IO をここへ集約し、例規集固有のパスを扱いつつ、圧縮とアーカイブの
振る舞いを会議録側と揃える。
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
    # 更新確認では既存成果物の置換がよく起きる。
    # 失敗した取得を後から調査できるよう、古い成果物は自治体ツリーの近くへ退避する。
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


# 取得元が申告する母数。scrape_state.json は実行のたびに消されるので分けて置く。
SOURCE_COVERAGE_FILE = "source_coverage.json"


def source_coverage_path(work_root: Path) -> Path:
    return Path(work_root) / SOURCE_COVERAGE_FILE


def load_source_coverage(work_root: Path) -> dict | None:
    """取得元が申告した母数の記録を読む。無ければ None。"""
    try:
        payload = json.loads(source_coverage_path(work_root).read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def mark_walk_started(work_root: Path, when: str) -> None:
    """これから取得元を歩き直す、という印を残す。

    前回 complete の自治体は、歩き直しの途中で殺されても complete のまま
    残る。取得元が増えていても完了に見えるので、始めた時刻を控えておく。
    """
    payload = load_source_coverage(work_root) or {}
    payload = {**payload, "walk_started_at": when}
    save_source_coverage(work_root, payload)


def effective_coverage_complete(payload: dict | None) -> bool:
    """記録どおり取り切れていると言えるか。

    complete でも、歩き直しを始めたまま終われていないなら当てにできない。
    """
    if not isinstance(payload, dict) or not payload.get("complete"):
        return False
    started = str(payload.get("walk_started_at") or "")
    observed = str(payload.get("observed_at") or "")
    return not (started and started > observed)


def save_source_coverage(work_root: Path, payload: dict) -> Path:
    """母数の記録を保存する。

    完了判定に使うので、**フラットな総数ひとつでは足りない**。検索型は
    分割の葉ごとに「上限に張り付いたか」を持ち、目録型は「最後まで歩けたか」を
    持つ。取得元がそもそも母数を申告しない場合は、未確認ではなく
    `declares=False` として記録する（読めなかったのとは意味が違う）。
    """
    path = source_coverage_path(work_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)
    return path


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
