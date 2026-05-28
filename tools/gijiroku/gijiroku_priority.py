#!/usr/bin/env python3
"""Priority calculation for assembly minutes batch scheduling.

The batch should resume incomplete municipalities first, avoid recently
completed ones, and still revisit stale completed data.  This module converts
background task state and freshness metadata into one sortable score.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import freshness_metadata


# priority_score が大きいほど先に実行し、0 は今回のキューに載せない。
STOP_RETURN_CODES = {-15, -2, 130, 143}
_TASK_STATUS_CACHE: dict[str, dict[str, Any]] = {}


# background_tasks JSON を読み、同じプロセス内ではキャッシュして使い回す。
def task_status(task_name: str) -> dict[str, Any]:
    if task_name in _TASK_STATUS_CACHE:
        return _TASK_STATUS_CACHE[task_name]
    path = Path(__file__).resolve().parents[2] / "data" / "background_tasks" / f"{task_name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _TASK_STATUS_CACHE[task_name] = payload
    return payload


# task state から自治体 1 件の item を取り出す。
def task_item(task_name: str, slug: str) -> dict[str, Any]:
    payload = task_status(task_name)
    item = payload.get("items", {}).get(slug)
    return item if isinstance(item, dict) else {}


# 前回結果が、停止ではなく実エラーで失敗した自治体かを判定する。
def previous_item_failed_with_error(slug: str) -> bool:
    item = task_item("gijiroku", slug)
    if str(item.get("status", "")).strip() != "failed":
        return False
    message = str(item.get("message", "")).strip()
    if message.startswith("停止"):
        return False
    try:
        returncode = int(item.get("returncode"))
    except Exception:
        return True
    return returncode not in STOP_RETURN_CODES


# item の progress_current / progress_total を整数タプルにする。
def item_progress(item: dict[str, Any]) -> tuple[int, int]:
    try:
        current = max(0, int(item.get("progress_current")))
        total = max(0, int(item.get("progress_total")))
    except Exception:
        return 0, 0
    return current, total


# 正常終了した item だけから finished_at を取り出す。
def successful_item_finished_at(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip()
    if status not in {"done", "ok"}:
        return ""
    try:
        returncode = int(item.get("returncode"))
    except Exception:
        return ""
    if returncode != 0:
        return ""
    return str(item.get("finished_at") or "").strip()


# 取得完了かつ 30 日以内に成功していれば、今回の scrape 対象から外せるか判定する。
def recently_completed_successfully(slug: str, current_count: int, total_count: int) -> tuple[bool, str]:
    if total_count <= 0 or current_count != total_count:
        return False, ""

    finished_at = successful_item_finished_at(task_item("gijiroku", slug))
    finished = freshness_metadata.parse_datetime_text(finished_at)
    if finished is None:
        return False, finished_at

    age = freshness_metadata.now_tokyo() - finished
    return age < timedelta(days=freshness_metadata.FRESHNESS_SKIP_DAYS), finished_at


# 優先度グループと進捗から、priority queue 用の数値スコアを作る。
def priority_score(
    *,
    priority_group: int,
    progress_ratio: float,
    current_count: int,
    freshness_date,
    last_checked_at: str,
    previously_failed: bool,
) -> int:
    # group 1: 取得未完了、group 2: 総数不明、group 3: 完了済みだが再確認対象。
    # group 4 は 30 日以内に正常完了しているため、score=0 でスキップする。
    if priority_group >= 4:
        return 0

    base_by_group = {
        1: 3_000_000_000,
        2: 2_000_000_000,
        3: 1_000_000_000,
    }
    score = base_by_group.get(priority_group, 0)
    if previously_failed:
        score += 50_000_000

    if priority_group == 1:
        score += int(progress_ratio * 1_000_000)
        score += min(current_count, 999_999)
    elif priority_group == 3:
        today = freshness_metadata.today_tokyo()
        if freshness_date is not None:
            score += max(0, min((today - freshness_date).days, 99_999))
        if not last_checked_at:
            score += 10_000

    return score


# 会議録の scrape_state.json から実ファイルベースの進捗を読む。
def state_file_progress(target: dict[str, Any]) -> tuple[int, int]:
    state_path = Path(target.get("work_dir", "")) / "scrape_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    if not isinstance(payload, dict):
        return 0, 0
    return item_progress(payload)


# 優先度計算に使う進捗を、state/snapshot/scrape_state から選ぶ。
def priority_progress(slug: str, target: dict[str, Any] | None = None) -> tuple[int, int]:
    # 実行中 state、成功スナップショット、実ファイル由来 scrape_state のうち、
    # もっとも「未完了を見逃しにくい」進捗を優先度計算に使う。
    candidates = [
        item_progress(task_item("gijiroku", slug)),
        item_progress(task_item("gijiroku_snapshot", slug)),
    ]
    if target is not None:
        candidates.append(state_file_progress(target))
    return max(candidates, key=lambda value: (value[1] > 0, value[0] < value[1], value[0], value[1]))


# target に対して、優先度ラベル・スコア・進捗・鮮度情報をまとめる。
def target_priority_info(target: dict[str, Any]) -> dict[str, Any]:
    # 自治体 1 件を、優先度グループ・数値スコア・表示用ラベルへ変換する。
    slug = str(target.get("slug", "")).strip()
    current_count, total_count = priority_progress(slug, target)
    ratio = (current_count / total_count) if total_count > 0 else 0.0

    previously_failed = previous_item_failed_with_error(slug)
    freshness = freshness_metadata.item_freshness("gijiroku", target)
    freshness_date = freshness_metadata.parse_date(freshness.get("freshness_date"))
    is_fresh = (
        freshness_date is not None
        and freshness_date >= freshness_metadata.today_tokyo() - timedelta(days=freshness_metadata.FRESHNESS_SKIP_DAYS)
    )
    recently_complete, finished_at = recently_completed_successfully(slug, current_count, total_count)

    if total_count > 0 and current_count < total_count:
        priority_group = 1
        priority_label = "incomplete_failed" if previously_failed else "incomplete"
    elif total_count <= 0:
        priority_group = 2
        priority_label = "unknown_total_failed" if previously_failed else "unknown_total"
    elif recently_complete:
        priority_group = 4
        priority_label = "recent_complete_failed" if previously_failed else "recent_complete"
    else:
        priority_group = 3
        if is_fresh:
            priority_label = "fresh_but_due_failed" if previously_failed else "fresh_but_due"
        else:
            priority_label = "stale_complete_failed" if previously_failed else "stale_complete"

    score = priority_score(
        priority_group=priority_group,
        progress_ratio=ratio,
        current_count=current_count,
        freshness_date=freshness_date,
        last_checked_at=str(freshness.get("last_checked_at") or ""),
        previously_failed=previously_failed,
    )

    return {
        "priority_group": priority_group,
        "priority_score": score,
        "priority_label": priority_label,
        "progress_ratio": ratio,
        "current_count": current_count,
        "total_count": total_count,
        "downloaded_count": current_count,
        "finished_at": finished_at,
        "previously_failed": previously_failed,
        **freshness,
    }


# sort/PriorityTargetQueue が使う安定した並び順キーを返す。
def priority_sort_key(target: dict[str, Any]) -> tuple[Any, ...]:
    # PriorityTargetQueue は小さい key から取り出すため、score は符号を反転する。
    info = target_priority_info(target)
    freshness_date = str(info.get("freshness_date") or "")
    last_checked_at = str(info.get("last_checked_at") or "")
    return (
        -int(info["priority_score"]),
        freshness_date if int(info["priority_group"]) == 3 else "",
        last_checked_at if int(info["priority_group"]) == 3 else "",
        -float(info["progress_ratio"]),
        -int(info["current_count"]),
        str(target.get("name", "")),
        str(target.get("slug", "")),
    )


# target 一覧を優先度順に並べて返す。
def sort_targets_by_priority(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(targets, key=priority_sort_key)


# PriorityTargetQueue へ渡すための key 関数。
def priority_queue_key(target: dict[str, Any]) -> tuple[Any, ...]:
    return priority_sort_key(target)


# 鮮度確認を省略する理由があれば表示用文言として返す。
def update_check_skip_reason(target: dict[str, Any]) -> str:
    return freshness_metadata.update_check_skip_reason("gijiroku", target)
