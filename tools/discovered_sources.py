#!/usr/bin/env python3
"""探索で見つけた取得元を、実行時に使える形で覚えておく。

登録簿（`data/municipalities/*.tsv`）には取得元 URL を人が書く。書けていない
自治体は `crawl_status=unresolved` のままで、**巡回のキューに載らない**。
載らないので何度放置しても状態は変わらない。2026-09-06 の点検では会議録
245 自治体、例規集 27 自治体がこの形だった。

探索そのものは `tools/gijiroku/discover_minutes_urls.py` と
`tools/reiki/discover_reiki_urls.py` が持っている。これまで人が走らせて
結果を見てから TSV へ書いていた。その人がいないと埋まらないので、
定期実行から呼び、**確信度の高い結果だけ**を実行時の上書きとして記録する。

登録簿は git 管理なので実行中には書き換えない。上書きは
`work/<task>/discovered_sources.json` に積み、対象を読むときに差し替える。
TSV に URL が入れば、そちらが優先されて上書きは使われなくなる。

確信度の意味（探索側の定義）:

- `high`: 既知のベンダ URL を自治体の公式サイトから辿って見つけた。
  system_type は URL の形から決まるので、人が見ても同じ結論になる
- `medium`: 自治体自身のサイト内で会議録ページを見つけ、形を調べて
  system_type を決めた。ページの持ち主は自治体で間違いない
- `low` / `none`: system_type を決められていない。スクレイパを選べないので使わない
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = WORKSPACE_ROOT / "work"

# 実行時の上書きとして採用する確信度。
USABLE_CONFIDENCE = ("high", "medium")
# 同じ自治体を探索し直すまでの間隔。取得元は頻繁には現れない。
DEFAULT_RETRY_DAYS = 14
JST = timezone(timedelta(hours=9))


def now_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    except ValueError:
        return None


def store_path(task_name: str) -> Path:
    return WORK_ROOT / str(task_name) / "discovered_sources.json"


def load(task_name: str) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(store_path(task_name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        # 壊れた記録で巡回を止めない。TSV の値をそのまま使う。
        return {}
    if not isinstance(payload, dict):
        return {}
    entries: dict[str, dict[str, str]] = {}
    for code, entry in payload.items():
        if isinstance(entry, dict):
            entries[str(code).strip()] = {str(k): str(v) for k, v in entry.items()}
    return entries


def save(task_name: str, entries: dict[str, dict[str, str]]) -> None:
    path = store_path(task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def record(
    task_name: str,
    code: str,
    *,
    url: str = "",
    system_type: str = "",
    confidence: str = "",
    note: str = "",
) -> None:
    """探索の結果を記録する。見つからなかった場合も、試した時刻を残す。

    時刻を残さないと、同じ自治体を毎回試して他の自治体に順番が回らない。
    """
    normalized = str(code).strip()
    if normalized == "":
        return
    entries = load(task_name)
    entries[normalized] = {
        "url": str(url).strip(),
        "system_type": str(system_type).strip(),
        "confidence": str(confidence).strip(),
        "note": str(note).strip(),
        "observed_at": now_text(),
    }
    save(task_name, entries)


def is_usable(entry: dict[str, str] | None) -> bool:
    if not entry:
        return False
    if str(entry.get("confidence", "")).strip() not in USABLE_CONFIDENCE:
        return False
    return bool(str(entry.get("url", "")).strip()) and bool(str(entry.get("system_type", "")).strip())


def apply_to_row(
    entry: dict[str, str] | None,
    url: str,
    system_type: str,
) -> tuple[str, str, bool]:
    """登録簿の値へ探索結果を重ねる。返すのは (url, system_type, 差し替えたか)。

    **登録簿に URL があるときは触らない。** 人が書いた値が常に優先で、
    TSV が埋まれば上書きは自然に使われなくなる。
    """
    if str(url).strip():
        return url, system_type, False
    if not is_usable(entry):
        return url, system_type, False
    return str(entry["url"]).strip(), str(entry["system_type"]).strip(), True


def due_codes(
    task_name: str,
    codes: list[str],
    *,
    retry_days: int = DEFAULT_RETRY_DAYS,
    limit: int = 0,
    now: str = "",
) -> list[str]:
    """まだ試していない、または前回から十分に時間が経った自治体を返す。

    古い順に返すので、放っておけば全件をひと回りする。
    """
    entries = load(task_name)
    current = parse_time(now or now_text()) or datetime.now(JST)
    pending: list[tuple[datetime | None, str]] = []
    for code in codes:
        normalized = str(code).strip()
        if normalized == "":
            continue
        entry = entries.get(normalized)
        if is_usable(entry):
            # すでに使える取得元がある。探索し直さない。
            continue
        observed = parse_time(str((entry or {}).get("observed_at", "")))
        if observed is not None and (current - observed) < timedelta(days=retry_days):
            continue
        pending.append((observed, normalized))
    # 未実施（None）を先に、次に古い順。
    pending.sort(key=lambda item: (item[0] is not None, item[0] or current))
    ordered = [code for _observed, code in pending]
    return ordered[:limit] if limit and limit > 0 else ordered
