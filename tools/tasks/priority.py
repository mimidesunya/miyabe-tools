#!/usr/bin/env python3
"""会議録・例規集で共有するスクレイピング優先度計算。

どちらのバッチも、未完了を先に再開し、総数不明を実行対象に残し、
直近で成功した完了済みはスキップし、古くなった完了済みを再確認する。
分野ごとの差分は task 名、表示用 count field、追加の進捗取得元だけなので、
計算本体はこの 1 ファイルに集約する。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import freshness_metadata
from tools.tasks import input_fingerprint as input_generation
from tools.tasks import status as batch_status

# 走査記録の読み方は 1 箇所に置く。ここで書き写すと、公開画面・監査・キューで
# 別々の答えを出すことになる（実際にそうなっていた）。
_GIJIROKU_TOOLS = Path(__file__).resolve().parents[1] / "gijiroku"
if str(_GIJIROKU_TOOLS) not in sys.path:
    sys.path.append(str(_GIJIROKU_TOOLS))
_REIKI_TOOLS = Path(__file__).resolve().parents[1] / "reiki"
if str(_REIKI_TOOLS) not in sys.path:
    sys.path.append(str(_REIKI_TOOLS))
try:
    from reiki_io import effective_coverage_complete as reiki_coverage_complete
except Exception:
    def reiki_coverage_complete(payload: Any) -> bool:
        if not isinstance(payload, dict) or not payload.get("complete"):
            return False
        try:
            if int(payload.get("version") or 0) < 2:
                return False
        except (TypeError, ValueError):
            return False
        started = str(payload.get("walk_started_at") or "")
        observed = str(payload.get("observed_at") or "")
        return not (started and started > observed)

try:
    from gijiroku_storage import effective_walk_state
except Exception:  # 会議録の道具が無い環境でも優先度計算は動かす
    # 手抜きの代替を置くと、判定が二種類になって食い違う。
    # gijiroku_storage.effective_walk_state と同じ規則をそのまま書く。
    def effective_walk_state(payload: Any) -> str:
        if not isinstance(payload, dict) or not payload:
            return "unknown"
        try:
            if int(payload.get("rule_version") or 0) < 2:
                return "stale_rule"
        except (TypeError, ValueError):
            return "stale_rule"
        state = str(payload.get("state") or "").strip() or "unknown"
        started = str(payload.get("walk_started_at") or "")
        updated = str(payload.get("updated_at") or "")
        if state == "complete" and started and started > updated:
            return "rewalking"
        return state


# priority_score が大きいほど先に実行し、0 は今回のキューに載せない。
STOP_RETURN_CODES = {-15, -2, 130, 143}

# 取得漏れがごく一部だけの失敗は、通常巡回から永久に外す previous_failed ではなく
# incomplete として扱い、次回サイクルで残りを取りに行く。
# 1 文書の取得失敗で自治体全体が巡回対象から消えるのを避けるための許容量。
# 表示側は failed のままなので、部分収録であることは隠さない。
RESIDUAL_FAILURE_MAX_COUNT = 5
RESIDUAL_FAILURE_MAX_RATIO = 0.01
_TASK_STATUS_CACHE: dict[str, dict[str, Any]] = {}
ProgressReader = Callable[[dict[str, Any]], tuple[int, int]]


# background_tasks JSON を読み、同じプロセス内ではキャッシュして使い回す。
def task_status(task_name: str) -> dict[str, Any]:
    if task_name in _TASK_STATUS_CACHE:
        return _TASK_STATUS_CACHE[task_name]
    # status.py と別の保存先を組み立てると、復旧ツールやテストが切り替えた正本を
    # 読まず、古い失敗を永久に保持し得る。同じ resolver を必ず使う。
    path = batch_status.status_path(task_name)
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


# 取得済みが大半で、残りがごく一部だけの失敗かを判定する。
def residual_failure_only(item: dict[str, Any]) -> bool:
    current, total = item_progress(item)
    if total <= 0 or current <= 0 or current >= total:
        return False
    missing = total - current
    allowance = max(RESIDUAL_FAILURE_MAX_COUNT, int(total * RESIDUAL_FAILURE_MAX_RATIO))
    return missing <= allowance


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
    if residual_failure_only(item):
        # 残り数件だけの取得漏れは incomplete として次回サイクルで拾う。
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


# 失敗した自治体を自動で再試行するまでの待ち時間。
# すぐにやり直すと壊れた取得元を叩き続けるので間を置く。とはいえ間を置かないと
# 一度の失敗で永久に再取得されなくなる（実際に会議録 45・例規 6 自治体が
# 3 か月以上、巡回対象から外れたままだった）。
FAILED_RETRY_DAYS = 7


def failure_is_retryable(finished_at: str) -> bool:
    """失敗から十分に時間が経っていれば、自動で 1 度やり直してよい。"""
    finished = freshness_metadata.parse_datetime_text(finished_at)
    if finished is None:
        # 呼び出し側が durable な観測時刻すら確定できなかった場合に、永久な
        # score=0 へ戻す方が危険なので fail-open にする。
        return True
    return (freshness_metadata.now_tokyo() - finished) >= timedelta(days=FAILED_RETRY_DAYS)


def _valid_failure_time(value: object) -> str:
    """待ち時間の基準にできる、現在以前の時刻だけを返す。"""
    text = str(value or "").strip()
    parsed = freshness_metadata.parse_datetime_text(text)
    if parsed is None or parsed > freshness_metadata.now_tokyo():
        # 未来時刻を採用すると、時計ずれや壊れた移行値ひとつで永久待ちになる。
        return ""
    return text


def _persist_failure_observation(
    task_name: str,
    slug: str,
    item: dict[str, Any],
    observed_at: str,
    basis: str,
) -> bool:
    """初回に採った代替時刻を、以後動かない item field として固定する。"""
    state = batch_status.read_state(task_name)
    items = state.get("items") if isinstance(state, dict) else None
    stored = items.get(slug) if isinstance(items, dict) else None
    if not isinstance(stored, dict):
        return False

    existing = _valid_failure_time(stored.get("failure_observed_at"))
    if existing:
        item["failure_observed_at"] = existing
        item["failure_observed_basis"] = str(stored.get("failure_observed_basis") or "first_observed")
        return True

    stored["failure_observed_at"] = observed_at
    stored["failure_observed_basis"] = basis
    item["failure_observed_at"] = observed_at
    item["failure_observed_basis"] = basis
    batch_status.write_state(task_name, state)

    # write_state は運用継続のため例外を握る。読み戻して確認できなければ、
    # 次回も now を採り直して永久待ちになるので呼び出し側を fail-open にする。
    persisted = batch_status.read_state(task_name)
    persisted_items = persisted.get("items") if isinstance(persisted, dict) else None
    persisted_item = persisted_items.get(slug) if isinstance(persisted_items, dict) else None
    if not isinstance(persisted_item, dict):
        return False
    if _valid_failure_time(persisted_item.get("failure_observed_at")) != observed_at:
        return False
    _TASK_STATUS_CACHE[task_name] = persisted
    return True


def failure_reference_time(task_name: str, slug: str, item: dict[str, Any]) -> str:
    """欠けた finished_at に代わる、durable な失敗観測時刻を返す。"""
    for key in ("finished_at", "updated_at", "last_checked_at", "failure_observed_at"):
        candidate = _valid_failure_time(item.get(key))
        if candidate:
            return candidate

    path = batch_status.status_path(task_name)
    try:
        observed_at = batch_status.format_timestamp_text(path.stat().st_mtime)
        basis = "state_mtime"
    except OSError:
        observed_at = batch_status.now_text()
        basis = "first_observed"
    if not _valid_failure_time(observed_at):
        observed_at = batch_status.now_text()
        basis = "first_observed"

    if _persist_failure_observation(task_name, slug, item, observed_at, basis):
        return observed_at
    return ""


# 取得完了かつ 30 日以内に成功していれば、今回の scrape 対象から外せるか判定する。
def recently_completed_successfully(
    task_name: str,
    slug: str,
    current_count: int,
    total_count: int,
    target: dict[str, Any],
) -> tuple[bool, str]:
    if total_count <= 0 or current_count != total_count:
        return False, ""

    fallback_finished_at = ""
    for candidate_task_name in [task_name, f"{task_name}_snapshot"]:
        candidate_item = task_item(candidate_task_name, slug)
        candidate_current, candidate_total = item_progress(candidate_item)
        if (candidate_current, candidate_total) != (current_count, total_count):
            # 以前の件数上限付き実行（例: 25/25）の成功時刻を、後から判明した
            # 全件数（例: 1675/1675）の成功時刻として流用しない。
            continue
        if not input_generation.fingerprint_matches_published(task_name, target, candidate_item):
            # 件数と時刻が同じでも、URLや取得方式を直した後の成功証明にはならない。
            continue
        finished_at = successful_item_finished_at(candidate_item)
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
    work_dir = Path(target.get("work_dir", ""))
    state_path = work_dir / "scrape_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    validation = payload.get("validation")
    if isinstance(validation, dict) and str(validation.get("mode") or "") == "classified_scrape_result":
        current_count, total_count = item_progress(validation)
    else:
        current_count, total_count = item_progress(payload)

    # 走査の記録は source_coverage.json を見る。scrape_state.json は実行の頭で
    # 消されるので、殺された実行のあとはここが空になり、再投入の判断ができない。
    source_coverage = payload.get("source_coverage")
    try:
        durable = json.loads((work_dir / "source_coverage.json").read_text(encoding="utf-8"))
    except Exception:
        durable = None
    if isinstance(durable, dict) and durable:
        if not isinstance(source_coverage, dict) or str(
            source_coverage.get("updated_at") or ""
        ) <= str(durable.get("updated_at") or ""):
            source_coverage = durable
    walk_state = effective_walk_state(source_coverage)
    if (
        isinstance(source_coverage, dict)
        and str(source_coverage.get("mode") or "") == "source_discovery_coverage"
        and walk_state
        in {
            "partial_planned",
            "partial_limit",
            "partial_error",
            "partial_recent_only",
            # 一覧が長すぎて制限時間で降りた。続きから歩けるので、
            # 30 日の間隔を待たずに次のサイクルへ戻す。
            "partial_time",
            # 歩き直しの途中で死んだ、古い規則で書かれた、のどちらも
            # 取り切れた証拠にはならない。
            "rewalking",
            "stale_rule",
        }
    ):
        # 件数上限や一覧ページの失敗がある実行を「25/25 完了」のように扱わない。
        # 進捗が読めなくても、走査が未完了なら再投入したい。
        # 直近分の一覧しか出ない取得元（partial_recent_only）も同じ扱いにする。
        # 既存 snapshot よりこの incomplete 候補が優先され、次回バッチへ再投入される。
        return current_count, max(total_count, current_count + 1)

    system_family = str(target.get("system_family") or target.get("system_type") or "").strip()
    has_explicit_coverage = (
        isinstance(source_coverage, dict)
        and str(source_coverage.get("mode") or "") == "source_discovery_coverage"
        and walk_state == "complete"
    )
    # 走査記録を書く系統。書くようにした分だけここに足す。書かない系統を
    # 入れると、記録が無いまま永久に未完了として再投入し続けることになる。
    RECORDS_WALK = {
        "dbsr",
        "db-search",
        "kaigiroku-indexphp",
        "kaigiroku.net",
        "gijiroku.com",
        "voices",
        "kensakusystem",
        "独自",
        "static-kaigiroku-dir",
        "kami-city-pdf",
        "site-gikai-pdf",
        "amivoice",
        "msearch",
    }
    if system_family in RECORDS_WALK and not has_explicit_coverage:
        # 走査の記録を書く系統なのに complete が無いなら、まだ歩き切れていない。
        # 保存件数と検索件数が一致していても、取得元の全一覧を走査した記録が
        # なければ meetings_index の件数を使って未完了候補にし、上限なしの
        # 再走査で complete/partial_error を確定させる。
        # kaigiroku.net と gijiroku.com も記録を書くようになったので、
        # 殺された再走査がキューに戻らないまま完了扱いになるのを防ぐ。
        try:
            index_payload = json.loads(Path(str(target.get("index_json_path") or "")).read_text(encoding="utf-8"))
            if isinstance(index_payload, list):
                current_count = max(current_count, len(index_payload))
        except Exception:
            pass
        if current_count > 0:
            return current_count, max(total_count, current_count + 1)
    return current_count, total_count


def reiki_coverage_progress(target: dict[str, Any]) -> tuple[int, int]:
    """例規の走査記録から、取り切れたかどうかを進捗として返す。

    例規のスクレイパは取り切れなくても `n/n` で正常終了する。進捗だけを
    見ていると、取り切れなかった区間が残っていてもキューは完了と読む。
    会議録で塞いだのと同じ穴が、例規側に残っていた。
    """
    # 例規の target は work_dir ではなく work_root を持つ。
    # 間違えると常に (0, 0) を返し、この判定は何もしないことになる。
    work_root = Path(str(target.get("work_root") or target.get("work_dir") or ""))
    try:
        payload = json.loads((work_root / "source_coverage.json").read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    if not isinstance(payload, dict) or not payload:
        return 0, 0
    if not payload.get("declares", True):
        # 取得元に母数の概念が無い（目録型）。ここでは判断しない。
        return 0, 0
    try:
        collected = max(0, int(payload.get("collected") or 0))
    except (TypeError, ValueError):
        collected = 0
    # 判定は helper に任せる。ここで書き写すと、版や歩き直しの規則が
    # 増えたときに片方だけ古くなる。
    if reiki_coverage_complete(payload):
        return collected, collected
    # 取り切れていない。完了より必ず小さい母数にして、次回へ回す。
    return collected, collected + 1


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
        # 同一プロセス内では slug ごとに 1 回だけ計算する。
        # バッチ起動時は選定・ソート・キュー投入の 3 箇所から呼ばれるが、
        # 実行順は起動時点の状態で固定する仕様なのでキャッシュしてよい。
        self._info_cache: dict[str, dict[str, Any]] = {}

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
        slug = str(target.get("slug", "")).strip()
        cached = self._info_cache.get(slug)
        if cached is not None:
            return cached
        info = self._compute_priority_info(slug, target)
        if slug:
            self._info_cache[slug] = info
        return info

    def _compute_priority_info(self, slug: str, target: dict[str, Any]) -> dict[str, Any]:
        # 自治体 1 件を、優先度グループ・数値スコア・表示用ラベルへ変換する。
        current_count, total_count = self.priority_progress(slug, target)
        ratio = (current_count / total_count) if total_count > 0 else 0.0

        # 実エラーで失敗した自治体は、すぐには自動再実行せず手動の対処を待つ。
        failed_task_name = ""
        if previous_item_failed_with_error(self.task_name, slug):
            failed_task_name = self.task_name
        elif previous_item_failed_with_error(self.reflect_task_name, slug):
            failed_task_name = self.reflect_task_name

        if failed_task_name:
            failed_item = task_item(failed_task_name, slug)
            if not input_generation.fingerprint_matches_observed_input(
                self.task_name,
                target,
                failed_item,
            ):
                # registryを直した後まで旧URLの失敗待ちを引き継ぐと、修正した入力を
                # 最大7日試せない。入力世代が変わった失敗は現在世代を止めない。
                failed_task_name = ""

        freshness = freshness_metadata.item_freshness(self.task_name, target)
        retry_after_failure = False
        if failed_task_name:
            failed_item = task_item(failed_task_name, slug)
            failed_at = failure_reference_time(failed_task_name, slug, failed_item)
            if not failure_is_retryable(failed_at):
                return {
                    "priority_group": 5,
                    "priority_score": 0,
                    "priority_label": "previous_failed",
                    "progress_ratio": ratio,
                    "current_count": current_count,
                    "total_count": total_count,
                    self.count_field: current_count,
                    "finished_at": failed_at,
                    "previously_failed": True,
                    **freshness,
                }
            # 十分に時間が経ったので、通常の対象として 1 度やり直す。
            retry_after_failure = True

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
            target,
        )
        if retry_after_failure:
            # 失敗の待機上限を7日にしても、30日 freshness をもう一度通すと
            # index失敗などが最大30日眠る。待ち終えた失敗は今回の対象に戻す。
            recently_complete = False

        if total_count > 0 and current_count < total_count:
            priority_group = 1
            priority_label = "incomplete"
        elif total_count <= 0:
            priority_group = 2
            priority_label = "unknown_total"
        elif recently_complete:
            priority_group = 4
            priority_label = "recent_complete"
        else:
            priority_group = 3
            priority_label = "fresh_but_due" if is_fresh else "stale_complete"

        score = priority_score(
            priority_group=priority_group,
            progress_ratio=ratio,
            current_count=current_count,
            freshness_date=freshness_date,
            last_checked_at=str(freshness.get("last_checked_at") or ""),
        )

        return {
            "priority_group": priority_group,
            "priority_score": score,
            # 失敗から時間を置いてやり直す対象は、通常の対象と区別して見せる。
            "priority_label": "failed_retry" if retry_after_failure else priority_label,
            "progress_ratio": ratio,
            "current_count": current_count,
            "total_count": total_count,
            self.count_field: current_count,
            "finished_at": finished_at,
            "previously_failed": retry_after_failure,
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
