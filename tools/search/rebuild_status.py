#!/usr/bin/env python3
"""OpenSearch 全量 rebuild の進捗 state 書き込み。

build_opensearch_index.py から分離した「進捗 UI 用」state 更新層。
data/background_tasks/search_rebuild.json を更新し、Web 側の進捗表示に使う。

注意:
- これは UI 補助であり、index の正しさには影響しない（失敗しても rebuild 自体は進む）。
- 進捗書き込みは bulk flush 毎ではなく PROGRESS_WRITE_INTERVAL_SECONDS 間隔に間引く
  （高頻度 write が rebuild を遅くしないため）。
- batch_status が import できない最小構成では全関数が no-op になる。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

try:
    from tools.tasks import status as batch_status  # type: ignore
except Exception:  # pragma: no cover - 進捗 UI は補助機能なので失敗しても続行する
    batch_status = None


_LAST_PROGRESS_WRITE_MONOTONIC = 0.0
PROGRESS_WRITE_INTERVAL_SECONDS = 2.0


def search_rebuild_status_start(*, build_id: str, doc_type: str, total_count: int) -> dict[str, Any] | None:
    if batch_status is None:
        return None
    total_count = max(0, int(total_count))
    try:
        total_cache = batch_status.status_root() / "search_rebuild_total_count.json"
        total_cache.write_text(json.dumps({"total_count": total_count}, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] search rebuild total count cache write failed: {exc}", file=sys.stderr, flush=True)
    state = {
        "task": "search_rebuild",
        "run_id": build_id,
        "running": True,
        "started_at": batch_status.now_text(),
        "finished_at": "",
        "heartbeat_at": batch_status.now_text(),
        "updated_at": batch_status.now_text(),
        "running_label": "検索インデックス更新",
        "doc_type": doc_type,
        "current_stage": "",
        "current_index": "",
        "current_slug": "",
        "current_municipality_code": "",
        "current_municipality_name": "",
        "current_document_title": "",
        "published_slug_count": 0,
        "published_municipality_count": 0,
        "published_current_slug": "",
        "published_current_municipality_name": "",
        "processed_count": 0,
        "total_count": total_count,
        "completed_count": 0,
        "active_count": 1,
        "pending_count": 0,
        "worker_capacity": 1,
        "worker_active_count": 1,
        "worker_idle_count": 0,
        "index_capacity": 1,
        "index_active_count": 1,
        "index_idle_count": 0,
        "index_queue_count": 0,
        "items": {},
    }
    batch_status.write_state("search_rebuild", state)
    batch_status.invalidate_runtime_caches(include_homepage_payload=True)
    return state


def search_rebuild_status_progress(
    state: dict[str, Any] | None,
    *,
    stage: str,
    index_name: str,
    processed_count: int,
    source: dict[str, Any],
    current_slug_processed_count: int,
    current_slug_total_count: int,
) -> None:
    if batch_status is None or state is None:
        return
    # 進捗 state は UI 補助なので、bulk flush のたびではなく一定間隔でだけ書く。
    global _LAST_PROGRESS_WRITE_MONOTONIC
    now = time.monotonic()
    if now - _LAST_PROGRESS_WRITE_MONOTONIC < PROGRESS_WRITE_INTERVAL_SECONDS:
        return
    _LAST_PROGRESS_WRITE_MONOTONIC = now
    next_processed = max(0, int(processed_count))
    previous_processed = max(0, int(state.get("processed_count") or 0))
    state["current_stage"] = stage
    state["current_index"] = index_name
    state["current_slug"] = str(source.get("slug") or "").strip()
    state["current_municipality_code"] = str(source.get("municipality_code") or "").strip()
    state["current_municipality_name"] = str(source.get("municipality_name") or "").strip()
    state["current_document_title"] = str(source.get("title") or "").strip()
    state["current_slug_processed_count"] = max(0, int(current_slug_processed_count))
    state["current_slug_total_count"] = max(0, int(current_slug_total_count))
    state["processed_count"] = next_processed
    state["completed_count"] = next_processed
    state["updated_at"] = batch_status.now_text()
    batch_status.write_state("search_rebuild", state)
    if next_processed // 1000 != previous_processed // 1000:
        batch_status.invalidate_runtime_caches(include_homepage_payload=True)


def search_rebuild_status_finish(state: dict[str, Any] | None, *, ok: bool, message: str = "") -> None:
    if batch_status is None or state is None:
        return
    state["running"] = False
    state["finished_at"] = batch_status.now_text()
    state["heartbeat_at"] = state["finished_at"]
    state["updated_at"] = state["finished_at"]
    state["active_count"] = 0
    state["worker_active_count"] = 0
    state["worker_idle_count"] = 1
    state["index_active_count"] = 0
    state["index_idle_count"] = 1
    state["status"] = "done" if ok else "failed"
    state["message"] = message
    batch_status.write_state("search_rebuild", state)
    batch_status.invalidate_runtime_caches(include_homepage_payload=True)


def search_rebuild_status_slug_published(
    state: dict[str, Any] | None,
    *,
    source: dict[str, Any],
    published_slug_count: int,
    published_municipality_count: int,
) -> None:
    if batch_status is None or state is None:
        return
    state["published_slug_count"] = max(0, int(published_slug_count))
    state["published_municipality_count"] = max(0, int(published_municipality_count))
    state["published_current_slug"] = str(source.get("slug") or "").strip()
    state["published_current_municipality_name"] = str(source.get("municipality_name") or "").strip()
    state["updated_at"] = batch_status.now_text()
    batch_status.write_state("search_rebuild", state)
    batch_status.invalidate_runtime_caches(include_homepage_payload=True)
