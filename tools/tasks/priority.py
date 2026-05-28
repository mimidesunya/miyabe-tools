#!/usr/bin/env python3
"""会議録・例規集で共有するスクレイピング優先度計算。

どちらのバッチも、未完了を先に再開し、総数不明を実行対象に残し、
直近で成功した完了済みはスキップし、古くなった完了済みを再確認する。
分野ごとの差分は task 名、表示用 count field、追加の進捗取得元だけなので、
計算本体はこの 1 ファイルに集約する。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import freshness_metadata


# priority_score が大きいほど先に実行し、0 は今回のキューに載せない。
STOP_RETURN_CODES = {-15, -2, 130, 143}
_TASK_STATUS_CACHE: dict[str, dict[str, Any]] = {}
ProgressReader = Callable[[dict[str, Any]], tuple[int, int]]


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


# item の progress_current / progress_total を整数タプルにする。
def item_progress(item: dict[str, Any]) -> tuple[int, int]:
    try:
        current = max(0, int(item.get("progress_current")))
        total = max(0, int(item.get("progress_total")))
    except Exception:
        return 0, 0
    return current, total


# 前回結果が、停止ではなく実エラーで失敗した自治体かを判定する。
def previous_item_failed_with_error(task_name: str, slug: str) -> bool:
    item = task_item(task_name, slug)
    message = str(item.get("message", "")).strip()
    if message.startswith("停止"):
        return False
    if str(item.get("index_status") or "").strip() == "failed":
        return True
    if str(item.get("status", "")).strip() != "failed":
        return False
    try:
        returncode = int(item.get("returncode"))
    except Exception:
        return True
    return returncode not in STOP_RETURN_CODES


# 正常終了した item だけから、再取得スキップ判定に使える成功時刻を取り出す。
def successful_item_finished_at(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip()
    if status not in {"done", "ok", "snapshot"}:
        return ""
    try:
        returncode = int(item.get("returncode"))
    except Exception:
        return ""
    if returncode != 0:
        return ""
    if status == "snapshot":
        # snapshot は既存成果物から復元した state なので finished_at は空になる。
        # 実際の最終確認時刻である last_checked_at を優先し、古い成果物を
        # snapshot 作成時刻だけで「新しい」と誤判定しないようにする。
        return str(item.get("last_checked_at") or item.get("finished_at") or "").strip()
    return str(item.get("finished_at") or item.get("last_checked_at") or "").strip()


# 取得完了かつ 30 日以内に成功していれば、今回の scrape 対象から外せるか判定する。
def recently_completed_successfully(
    task_name: str,
    slug: str,
    current_count: int,
    total_count: int,
) -> tuple[bool, str]:
    if total_count <= 0 or current_count != total_count:
        return False, ""

    fallback_finished_at = ""
    for candidate_task_name in [task_name, f"{task_name}_snapshot"]:
        finished_at = successful_item_finished_at(task_item(candidate_task_name, slug))
        if finished_at and not fallback_finished_at:
            fallback_finished_at = finished_at
        finished = freshness_metadata.parse_datetime_text(finished_at)
        if finished is None:
            continue

        age = freshness_metadata.now_tokyo() - finished
        if age < timedelta(days=freshness_metadata.FRESHNESS_SKIP_DAYS):
            return True, finished_at

    return False, fallback_finished_at


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


# 会議録の scrape_state.json など、task state 以外の進捗候補を読むための標準 reader。
def scrape_state_progress(target: dict[str, Any]) -> tuple[int, int]:
    state_path = Path(target.get("work_dir", "")) / "scrape_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    if not isinstance(payload, dict):
        return 0, 0
    validation = payload.get("validation")
    if isinstance(validation, dict) and str(validation.get("mode") or "") == "classified_scrape_result":
        return item_progress(validation)
    return item_progress(payload)


class PriorityCalculator:
    """分野ごとの差分だけを受け取り、共通の優先度計算 API を提供する。"""

    def __init__(
        self,
        task_name: str,
        *,
        count_field: str,
        extra_progress_reader: ProgressReader | None = None,
    ) -> None:
        self.task_name = str(task_name).strip()
        self.snapshot_task_name = f"{self.task_name}_snapshot"
        self.reflect_task_name = f"{self.task_name}_reflect"
        self.count_field = str(count_field).strip()
        self.extra_progress_reader = extra_progress_reader

    # 優先度計算に使う進捗を、実行中 state・成功 snapshot・任意の補助 state から選ぶ。
    def priority_progress(self, slug: str, target: dict[str, Any] | None = None) -> tuple[int, int]:
        # もっとも「未完了を見逃しにくい」進捗を採用する。
        candidates = [
            item_progress(task_item(self.task_name, slug)),
            item_progress(task_item(self.snapshot_task_name, slug)),
        ]
        if target is not None and self.extra_progress_reader is not None:
            candidates.append(self.extra_progress_reader(target))
        return max(candidates, key=lambda value: (value[1] > 0, value[0] < value[1], value[0], value[1]))

    # target に対して、優先度ラベル・スコア・進捗・鮮度情報をまとめる。
    def target_priority_info(self, target: dict[str, Any]) -> dict[str, Any]:
        # 自治体 1 件を、優先度グループ・数値スコア・表示用ラベルへ変換する。
        slug = str(target.get("slug", "")).strip()
        current_count, total_count = self.priority_progress(slug, target)
        ratio = (current_count / total_count) if total_count > 0 else 0.0

        failed_task_name = ""
        if previous_item_failed_with_error(self.task_name, slug):
            failed_task_name = self.task_name
        elif previous_item_failed_with_error(self.reflect_task_name, slug):
            failed_task_name = self.reflect_task_name

        previously_failed = failed_task_name != ""
        if previously_failed:
            freshness = freshness_metadata.item_freshness(self.task_name, target)
            failed_item = task_item(failed_task_name, slug)
            return {
                "priority_group": 5,
                "priority_score": 0,
                "priority_label": "previous_failed",
                "progress_ratio": ratio,
                "current_count": current_count,
                "total_count": total_count,
                self.count_field: current_count,
                "finished_at": str(failed_item.get("finished_at") or "").strip(),
                "previously_failed": True,
                **freshness,
            }

        freshness = freshness_metadata.item_freshness(self.task_name, target)
        freshness_date = freshness_metadata.parse_date(freshness.get("freshness_date"))
        is_fresh = (
            freshness_date is not None
            and freshness_date >= freshness_metadata.today_tokyo() - timedelta(days=freshness_metadata.FRESHNESS_SKIP_DAYS)
        )
        recently_complete, finished_at = recently_completed_successfully(
            self.task_name,
            slug,
            current_count,
            total_count,
        )

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
            self.count_field: current_count,
            "finished_at": finished_at,
            "previously_failed": previously_failed,
            **freshness,
        }

    # sort/PriorityTargetQueue が使う安定した並び順キーを返す。
    def priority_sort_key(self, target: dict[str, Any]) -> tuple[Any, ...]:
        # PriorityTargetQueue は小さい key から取り出すため、score は符号を反転する。
        info = self.target_priority_info(target)
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
    def sort_targets_by_priority(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(targets, key=self.priority_sort_key)

    # PriorityTargetQueue へ渡すための key 関数。
    def priority_queue_key(self, target: dict[str, Any]) -> tuple[Any, ...]:
        return self.priority_sort_key(target)

    # 鮮度確認を省略する理由があれば表示用文言として返す。
    def update_check_skip_reason(self, target: dict[str, Any]) -> str:
        return freshness_metadata.update_check_skip_reason(self.task_name, target)
