#!/usr/bin/env python3
"""一覧が縮んだという観測を、何度か繰り返してから確定する。

一覧（マニフェスト）が前回より大きく減ったとき、それが「取得元が本当に
消した」のか「今回の走査が短かった」のかは 1 回の実行では分からない。
そこで正本は動かさず、候補を別名で残して人の確認を待つ設計になっている。

**待つ人がいないと、そこで永久に止まる。** 2026-09-06 の点検では例規集の
10 自治体がこの形で止まっていた。取得元が本当に例規を整理した場合、
正本は古いまま固定される。

同じ縮み方が**日をまたいで何度も再現する**なら、一時的な不調ではない。
一時的な失敗は毎回違う形で落ちるので、同じ識別子の集合が繰り返し出て
くることは考えにくい。ここでは「同じ中身を、間隔を空けて既定 3 回
観測したら確定」とする。観測はマニフェストの隣に置く。

確定してよいのは**取り切れた走査**の縮みだけである。取り切れていない
走査は、何度繰り返しても取り切れていないことの証明にしかならない。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# 同じ縮み方を何回観測したら確定するか。
DEFAULT_REQUIRED_RUNS = 3
# 観測の間隔。短時間の連続再試行を別々の観測として数えないための下限。
DEFAULT_MIN_INTERVAL_HOURS = 12
# 観測が古くなったら捨てる。取得元が戻ったあとの記録を持ち越さない。
DEFAULT_EXPIRY_DAYS = 30

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


def observation_path(manifest_path: Path) -> Path:
    """観測の置き場所。マニフェストの隣に置く。"""
    name = Path(manifest_path).name
    for suffix in (".json.gz", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return Path(manifest_path).parent / f"{name}.shrink_observations.json"


def content_signature(identifiers: Iterable[str]) -> str:
    """一覧の中身を表す指紋。件数だけでは別物の入れ替わりを見逃す。"""
    cleaned = sorted({str(value).strip() for value in identifiers if str(value).strip()})
    digest = hashlib.sha256("\n".join(cleaned).encode("utf-8")).hexdigest()
    return f"{len(cleaned)}:{digest[:32]}"


def manifest_signature(manifest: list, keys: tuple[str, ...] = ("source_file", "detail_url", "url")) -> str:
    identifiers = []
    for row in manifest:
        if not isinstance(row, dict):
            identifiers.append(str(row))
            continue
        for key in keys:
            value = str(row.get(key) or "").strip()
            if value:
                identifiers.append(value)
                break
    return content_signature(identifiers)


def load_observation(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        # 壊れた観測で取得を止めない。数え直しになるだけ。
        return {}
    return payload if isinstance(payload, dict) else {}


def save_observation(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def clear_observation(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except Exception:
        return


def observe(
    manifest_path: Path,
    signature: str,
    *,
    required_runs: int = DEFAULT_REQUIRED_RUNS,
    min_interval_hours: int = DEFAULT_MIN_INTERVAL_HOURS,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    now: str = "",
) -> dict:
    """縮みを 1 回観測する。確定してよいかを返す。

    返す辞書:
      confirmed: 確定してよいか
      seen: これまでの観測回数
      required: 確定に必要な回数
      first_seen / last_seen: 観測の時刻
    """
    path = observation_path(manifest_path)
    stamp = now or now_text()
    current = parse_time(stamp) or datetime.now(JST)
    payload = load_observation(path)

    same = str(payload.get("signature") or "") == signature
    last_seen = parse_time(str(payload.get("last_seen") or ""))
    first_seen = parse_time(str(payload.get("first_seen") or ""))

    # 取得元が戻ったあとに残っていた古い観測は捨てる。
    if same and first_seen is not None and (current - first_seen) > timedelta(days=expiry_days):
        same = False

    if not same:
        payload = {"signature": signature, "seen": 1, "first_seen": stamp, "last_seen": stamp}
        save_observation(path, payload)
        return {"confirmed": False, "seen": 1, "required": required_runs,
                "first_seen": stamp, "last_seen": stamp}

    seen = int(payload.get("seen") or 0)
    # 短時間の連続再試行は 1 回の観測として扱う。日をまたいだ再現だけを数える。
    if last_seen is not None and (current - last_seen) < timedelta(hours=min_interval_hours):
        return {"confirmed": False, "seen": seen, "required": required_runs,
                "first_seen": str(payload.get("first_seen") or stamp),
                "last_seen": str(payload.get("last_seen") or stamp),
                "too_soon": True}

    seen += 1
    payload.update({"seen": seen, "last_seen": stamp})
    save_observation(path, payload)
    return {"confirmed": seen >= required_runs, "seen": seen, "required": required_runs,
            "first_seen": str(payload.get("first_seen") or stamp), "last_seen": stamp}
