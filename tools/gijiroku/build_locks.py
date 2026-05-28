#!/usr/bin/env python3
"""自治体ごとの OpenSearch 更新を守る file lock。

複数の scrape worker が同時に完了しても、同じ slug の alias 配下文書を書き換える
index 更新は 1 つだけでよい。DB や Celery result backend に頼らず、この lock で
自治体単位の更新競合を防ぐ。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def lock_root() -> Path:
    return project_root() / "data" / "background_tasks" / "gijiroku_build_locks"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_lock_path(slug: str) -> Path:
    return lock_root() / f"{slug}.lock"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def acquire_build_lock(
    slug: str,
    *,
    owner: str,
    wait_seconds: float = 0.0,
    poll_seconds: float = 1.0,
    stale_seconds: float = 4 * 60 * 60,
    on_wait: Callable[[], None] | None = None,
) -> Path | None:
    path = build_lock_path(slug)
    ensure_parent(path)
    deadline = time.time() + max(0.0, wait_seconds)

    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # 古い lock は、前回の builder が途中終了した可能性が高い。
            # ここで取り除き、1 つの失敗プロセスがその slug を永久に塞がないようにする。
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_seconds:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                return None
            if on_wait is not None:
                try:
                    on_wait()
                except Exception:
                    pass
            time.sleep(max(0.1, poll_seconds))
            continue

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "slug": slug,
                    "owner": owner,
                    "pid": os.getpid(),
                    "acquired_at": now_iso(),
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
        return path


def release_build_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
