from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# Celery beat が「次の周期タスクを投入してよいか」を判断するための補助関数群。
# 実際の進捗は background_tasks/*.json を正とし、ここでは投入抑制と再実行判定だけを行う。
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_STALE_SECONDS = 15 * 60
STATUS_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GIJIROKU_SUPPORTED_SYSTEMS = {
    "gijiroku.com",
    "voices",
    "kaigiroku.net",
    "dbsr",
    "db-search",
    "kaigiroku-indexphp",
    "kensakusystem",
    "kami-city-pdf",
    "site-gikai-pdf",
    "static-kaigiroku-dir",
}
REIKI_SUPPORTED_SYSTEMS = {"d1-law", "taikei", "g-reiki"}


# 環境変数を文字列として読む。空文字なら default に戻す。
def env_text(name: str, default: str) -> str:
    value = str(os.getenv(name, default)).strip()
    return value or default


# 環境変数を整数として読み、下限値で丸める。
def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, value)


# 環境変数を小数として読み、下限値で丸める。
def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    return max(minimum, value)


# 環境変数を真偽値として読む。認識できない値は default に戻す。
def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


# task 名から background_tasks JSON のパスを返す。
def background_task_path(task_name: str) -> Path:
    return ROOT / "data" / "background_tasks" / f"{task_name}.json"


# 失敗後の再投入待ちを記録する marker JSON のパスを返す。
def retry_marker_path(task_name: str) -> Path:
    return ROOT / "data" / "background_tasks" / "celery_retry_markers" / f"{task_name}.json"


# background_tasks JSON を読み込む。壊れていれば空 dict として扱う。
def load_background_task_status(task_name: str) -> dict:
    path = background_task_path(task_name)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


# retry marker JSON を読み込む。無ければ「待ちなし」として空 dict を返す。
def load_retry_marker(task_name: str) -> dict:
    path = retry_marker_path(task_name)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


# state 内の時刻文字列を UNIX timestamp に変換する。
def parse_status_timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if text == "":
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    for parser in (datetime.fromisoformat, lambda raw: datetime.strptime(raw, STATUS_TIME_FORMAT)):
        try:
            parsed = parser(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
            return parsed.timestamp()
        except Exception:
            continue
    return None


# state の finished/heartbeat/updated/started のうち最新時刻を返す。
def latest_status_timestamp(task_name: str) -> float | None:
    payload = load_background_task_status(task_name)
    candidates: list[float] = []
    for key in ("finished_at", "heartbeat_at", "updated_at", "started_at"):
        parsed = parse_status_timestamp(payload.get(key))
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        return None
    return max(candidates)


# 指定秒数後まで周期投入を抑制する retry marker を保存する。
def set_retry_marker(task_name: str, delay_seconds: int) -> None:
    # バッチが失敗した直後に beat が連続投入しないよう、簡単なクールダウンを置く。
    path = retry_marker_path(task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": task_name,
        "next_retry_at": time.time() + max(1, delay_seconds),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 成功時や再実行開始時に retry marker を削除する。
def clear_retry_marker(task_name: str) -> None:
    path = retry_marker_path(task_name)
    try:
        path.unlink()
    except FileNotFoundError:
        return


# retry marker の next_retry_at が未来なら、まだ投入禁止と判定する。
def retry_marker_active(task_name: str) -> bool:
    payload = load_retry_marker(task_name)
    next_retry_at = payload.get("next_retry_at")
    try:
        return float(next_retry_at) > time.time()
    except (TypeError, ValueError):
        return False


# state 上の実行中タスクが、heartbeat 的にも生きているか判定する。
def task_is_running(task_name: str, *, stale_seconds: int = DEFAULT_STALE_SECONDS) -> bool:
    # running=true でも heartbeat が古ければ「動作中」とは見なさない。
    # 異常終了時は task_is_stale_running 側でメタ情報の復旧対象にする。
    payload = load_background_task_status(task_name)
    if not bool(payload.get("running")):
        return False
    heartbeat = parse_status_timestamp(payload.get("heartbeat_at") or payload.get("updated_at"))
    if heartbeat is None:
        return True
    return (time.time() - heartbeat) <= max(0, stale_seconds)


# running=true だが heartbeat が古い、復旧対象のタスクか判定する。
def task_is_stale_running(task_name: str, *, stale_seconds: int = DEFAULT_STALE_SECONDS) -> bool:
    payload = load_background_task_status(task_name)
    if not bool(payload.get("running")):
        return False
    heartbeat = parse_status_timestamp(payload.get("heartbeat_at") or payload.get("updated_at"))
    if heartbeat is None:
        return True
    return (time.time() - heartbeat) > max(0, stale_seconds)


# item から進捗 current/total を安全に取り出す。
def _item_progress(item: object) -> tuple[int, int]:
    if not isinstance(item, dict):
        return 0, 0
    try:
        current = max(0, int(item.get("progress_current") or 0))
        total = max(0, int(item.get("progress_total") or 0))
    except Exception:
        return 0, 0
    return current, total


# Celery worker 上でも tools 配下の target 定義を import できるよう sys.path を補う。
def _ensure_tool_import_paths() -> None:
    for path in (ROOT / "tools", ROOT / "tools" / "gijiroku", ROOT / "tools" / "reiki"):
        text = str(path)
        if text not in sys.path:
            sys.path.append(text)


# 実行中 state と snapshot から slug ごとの既知 item を集める。
def _known_status_items(task_name: str) -> dict[str, dict]:
    # 実行中 JSON と成功スナップショットを合わせて、自治体ごとの最新既知状態を見る。
    # 実行中の一部 state だけで前回成功分を見失わないため。
    known: dict[str, dict] = {}
    for status_name in (task_name, f"{task_name}_snapshot"):
        payload = load_background_task_status(status_name)
        items = payload.get("items")
        if not isinstance(items, dict):
            continue
        for slug, item in items.items():
            if isinstance(item, dict):
                known[str(slug)] = item
    return known


# 現在の設定でスクレイピング対象になる自治体 slug 一覧を返す。
def _iter_supported_target_slugs(task_name: str) -> list[str]:
    # 未登録の自治体が残っていないかを見るため、現在サポート対象の slug 一覧を作る。
    _ensure_tool_import_paths()
    try:
        if task_name == "gijiroku":
            from tools.gijiroku import gijiroku_targets

            slugs: list[str] = []
            for target in gijiroku_targets.iter_gijiroku_targets():
                system_type = str(target.get("system_type") or "").strip()
                system_family = gijiroku_targets.canonical_minutes_system_type(system_type)
                if system_type in GIJIROKU_SUPPORTED_SYSTEMS or system_family in GIJIROKU_SUPPORTED_SYSTEMS:
                    slug = str(target.get("slug") or "").strip()
                    if slug:
                        slugs.append(slug)
            return slugs
        if task_name == "reiki":
            from tools.reiki import reiki_targets

            slugs = []
            for target in reiki_targets.iter_reiki_targets():
                if str(target.get("system_type") or "").strip() not in REIKI_SUPPORTED_SYSTEMS:
                    continue
                slug = str(target.get("slug") or "").strip()
                if slug:
                    slugs.append(slug)
            return slugs
    except Exception:
        return []
    return []


# 周期を短くすべき未完了・未登録・失敗 item が残っているか判定する。
def task_has_remaining_work(task_name: str) -> bool:
    # 「対象が未登録」「失敗」「総数不明」「取得数が総数未満」は残作業あり。
    # 残作業がある場合は通常周期より短い間隔で再投入を許可する。
    known_items = _known_status_items(task_name)
    target_slugs = _iter_supported_target_slugs(task_name)
    if target_slugs:
        for slug in target_slugs:
            item = known_items.get(slug)
            if item is None:
                return True
            if str(item.get("status") or "").strip() == "failed":
                return True
            current, total = _item_progress(item)
            if total <= 0 or current < total:
                return True
        return False

    for item in known_items.values():
        if str(item.get("status") or "").strip() == "failed":
            return True
        current, total = _item_progress(item)
        if total > 0 and current < total:
            return True
    return False


# Celery beat から呼ばれ、次の scrape cycle を投入すべきか判定する。
def cycle_is_due(
    task_name: str,
    schedule_seconds: int,
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> bool:
    # 投入判定の順序:
    # 1. 失敗直後のクールダウン中なら待つ
    # 2. まだ生きている実行中タスクがあれば待つ
    # 3. 未完了が残っていれば短い周期、なければ通常周期で判定する
    if retry_marker_active(task_name):
        return False
    if task_is_running(task_name, stale_seconds=stale_seconds):
        return False
    latest = latest_status_timestamp(task_name)
    if latest is None:
        return True
    due_seconds = schedule_seconds
    if task_has_remaining_work(task_name):
        due_seconds = env_int("SCRAPER_INCOMPLETE_SCHEDULE_SECONDS", 10 * 60, minimum=60)
    return (time.time() - latest) >= max(1, due_seconds)


# ログ出力用にコマンド配列を空白区切り文字列へ変換する。
def command_text(command: list[str]) -> str:
    return " ".join(command)
