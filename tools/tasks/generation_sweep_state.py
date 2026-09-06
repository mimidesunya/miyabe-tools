"""世代の掃き取りが、同じ自治体を積み直さないための控え。

掃き取りは古い世代の多い自治体から積む。ところが大きい自治体ほど再索引に
時間がかかるので、1 時間で捌ける量を超えて積むと、次の掃き取りが**まだ
待っている同じ自治体**をもう一度積む。北海道の会議録 10,066 件は 15 分では
終わらず、20 件積めば数時間分になる。放っておくとキューだけが伸びる。

積んだ自治体をここに控え、しばらくは積み直さない。控えが壊れても取得は
止まらない。読めなければ空として扱い、次の成功で書き直す。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import status as batch_status


# 一度積んだ自治体を、これだけの間は積み直さない。大きい自治体の再索引が
# 終わるまでの見込みより長く取る。
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60


def state_path(kind: str) -> Path:
    return batch_status.status_root() / f"{kind}_generation_sweep.json"


def _read(kind: str) -> dict[str, float]:
    try:
        loaded = json.loads(state_path(kind).read_text(encoding="utf-8"))
    except Exception:
        return {}
    queued = loaded.get("queued") if isinstance(loaded, dict) else None
    if not isinstance(queued, dict):
        return {}
    found: dict[str, float] = {}
    for slug, when in queued.items():
        try:
            found[str(slug)] = float(when)
        except (TypeError, ValueError):
            continue
    return found


def _write(kind: str, queued: dict[str, float]) -> None:
    path = state_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "kind": kind,
        "updated_at": batch_status.now_text(),
        "queued": queued,
    }
    # 書いている途中で落ちても、読む側が壊れた JSON を読まないようにする。
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    # mkstemp は 0600。掃き取りの状態も公開画面から読むので、読める権限にする。
    try:
        os.chmod(temporary, 0o644)
    except OSError:
        pass
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def filter_recently_queued(
    kind: str,
    slugs: list[str],
    *,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    now: float | None = None,
) -> list[str]:
    """まだ待ち時間の中にある自治体を落とす。"""
    moment = time.time() if now is None else float(now)
    queued = _read(kind)
    kept: list[str] = []
    for slug in slugs:
        when = queued.get(str(slug))
        # 控えに無い自治体は一度も積んでいない。時刻 0 を既定にすると、
        # 試験のように現在時刻が小さいときへ待ち時間が効いてしまう。
        if when is None or moment - when >= float(cooldown_seconds):
            kept.append(slug)
    return kept


def mark_queued(kind: str, slugs: list[str], *, now: float | None = None) -> None:
    """積んだことを控える。古すぎる控えは落として、際限なく太らせない。"""
    if not slugs:
        return
    moment = time.time() if now is None else float(now)
    queued = _read(kind)
    for slug in slugs:
        queued[str(slug)] = moment
    keep_after = moment - float(DEFAULT_COOLDOWN_SECONDS) * 4
    queued = {slug: when for slug, when in queued.items() if when >= keep_after}
    _write(kind, queued)
