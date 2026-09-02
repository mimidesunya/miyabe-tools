"""索引投入の待ち行列。取得の成功と、公開への反映の成功を切り離さないための記録。

取得が成功すると `send_task` で索引更新を別キューへ投げるが、投げた時点で
その自治体は「今回の確認は済んだ」と記録されてしまう。索引更新が落ちても、
次に拾い直される機会は freshness の 30 日後になる。決定的な失敗なら、
30 日ごとに同じ失敗を繰り返すだけで永久に公開へ反映されない。

実際に起きた。2026-08-31、例規の索引更新が未定義変数で全自治体・全実行落ちて
いたが、取得側は成功し続けていたので、誰も気づかないまま 10 時間走っていた。

ここでは「索引へ載せたい自治体」を消えない場所に控える。**索引が成功したと
確認できるまで消さない。**キューへ投げるところではなく、成功したところで消すので、
メッセージが失われても、worker が強制終了されても、掃き取りが拾い直せる。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import status as batch_status


# 何度も失敗し続けるものを短い間隔で投げ直さない。人が見るべき状態として残す。
DEFAULT_MAX_ATTEMPTS = 8
# 失敗の上限に達したあとも、完全には止めない。索引側の修正が配られたとき、
# 人が投げ直さなくても拾えるように、長い間隔で試し続ける。
DEFAULT_STUCK_RETRY_SECONDS = 3 * 24 * 60 * 60
# 待ち行列のファイル版。1 は「積んだ回数」を「失敗した回数」として数えていた。
OUTBOX_VERSION = 2
# 直前の試行からこれだけ経っていなければ掃き取りの対象にしない（1回目の待ち時間）。
DEFAULT_MIN_RETRY_SECONDS = 10 * 60
# 待ち時間は試行ごとに倍にするが、上限を設けて再開が遅くなりすぎないようにする。
DEFAULT_MAX_RETRY_SECONDS = 6 * 60 * 60


def outbox_path(kind: str) -> Path:
    return batch_status.status_root() / f"{kind}_index_outbox.json"


def _read(kind: str) -> dict[str, dict[str, Any]]:
    try:
        loaded = json.loads(outbox_path(kind).read_text(encoding="utf-8"))
    except Exception:
        # 壊れていても取得を止めない。掃き取りが次の成功で書き直す。
        return {}
    if not isinstance(loaded, dict):
        return {}
    entries = loaded.get("entries")
    if not isinstance(entries, dict):
        return {}
    found = {str(slug): dict(entry) for slug, entry in entries.items() if isinstance(entry, dict)}
    try:
        version = int(loaded.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version < OUTBOX_VERSION:
        for entry in found.values():
            _migrate_v1_entry(entry)
    return found


def _migrate_v1_entry(entry: dict[str, Any]) -> None:
    """版 1 の「積んだ回数」を「失敗した回数」から切り離す。

    版 1 は掃き取りが投げ直すたびに `attempts` を進めていた。索引キューが
    数日分あると、**一度も実行されないまま** 8 回積まれて上限に達し、永久に
    止まった。本番で 61 自治体がそうなっていて、どれにも失敗の記録が無かった。
    失敗の記録が無い `attempts` は積んだ回数だったので、そちらへ移す。
    """
    attempts = int(entry.get("attempts") or 0)
    if attempts > 0 and not entry.get("last_error"):
        entry["enqueues"] = max(int(entry.get("enqueues") or 0), attempts)
        entry["last_enqueued_at"] = entry.get("last_enqueued_at") or entry.get("last_attempt_at")
        entry["attempts"] = 0
        entry.pop("last_attempt_at", None)


def _write(kind: str, entries: dict[str, dict[str, Any]]) -> None:
    path = outbox_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": OUTBOX_VERSION,
        "kind": kind,
        "updated_at": batch_status.now_text(),
        "entries": entries,
    }
    # 同じディレクトリへ書いてから差し替える。途中で死んでも壊れた JSON が残らない。
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    )
    try:
        with handle as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def record_pending(kind: str, slug: str, *, task_id: str = "", error: str = "") -> None:
    """索引へ載せたい自治体を控える。既にあるなら試行回数は増やさない。"""
    slug = str(slug).strip()
    if slug == "":
        return
    entries = _read(kind)
    entry = entries.get(slug) or {}
    now = time.time()
    entry.setdefault("first_requested_at", now)
    entry["requested_at"] = now
    entry["task_id"] = str(task_id or entry.get("task_id") or "")
    entry.setdefault("attempts", 0)
    if error:
        # 投入そのものに失敗した分は、1 回試して駄目だったものとして数える。
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["last_attempt_at"] = now
        entry["last_error"] = str(error)
    else:
        # 積めた。実行が始まるまでは、ここから数えた待ち時間で見る。
        entry["enqueues"] = int(entry.get("enqueues") or 0) + 1
        entry["last_enqueued_at"] = now
    entries[slug] = entry
    _write(kind, entries)


def mark_done(kind: str, slug: str) -> None:
    """索引が成功したときだけ消す。"""
    slug = str(slug).strip()
    entries = _read(kind)
    if entries.pop(slug, None) is None:
        return
    _write(kind, entries)


def mark_failed(kind: str, slug: str, error: str) -> None:
    slug = str(slug).strip()
    if slug == "":
        return
    entries = _read(kind)
    entry = entries.get(slug) or {}
    now = time.time()
    entry.setdefault("first_requested_at", now)
    entry.setdefault("requested_at", now)
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["last_attempt_at"] = now
    entry["last_error"] = str(error)[:2000]
    entries[slug] = entry
    _write(kind, entries)


def mark_enqueued(kind: str, slug: str) -> None:
    """投げ直したことだけを控える。成否はタスク側が確定させる。

    ここで時刻を進めておかないと、掃き取りが回るたびに同じ自治体へ
    何度も投げてしまう。**積んだ回数は失敗の回数ではない。**索引キューが
    数日分あると、実行される前に何度も掃き取りの番が来る。それを失敗と
    数えると、一度も走らないまま上限に達して永久に止まる。"""
    slug = str(slug).strip()
    if slug == "":
        return
    entries = _read(kind)
    entry = entries.get(slug)
    if entry is None:
        return
    entry["enqueues"] = int(entry.get("enqueues") or 0) + 1
    entry["last_enqueued_at"] = time.time()
    entries[slug] = entry
    _write(kind, entries)


# 旧名。呼び出し側が残っていても壊れないようにする。
mark_attempted = mark_enqueued


def retry_delay_seconds(
    attempts: int,
    *,
    minimum: int = DEFAULT_MIN_RETRY_SECONDS,
    maximum: int = DEFAULT_MAX_RETRY_SECONDS,
) -> int:
    """試行ごとに待ち時間を倍にする。上限で頭打ちにする。"""
    if attempts <= 0:
        return minimum
    return min(maximum, minimum * (2 ** min(attempts, 10)))


def _reference_time(entry: dict[str, Any]) -> float:
    """待ち時間の起点。最後に積んだ・失敗した・要求した時刻のうち最新。"""
    latest = 0.0
    for key in ("last_enqueued_at", "last_attempt_at", "requested_at", "first_requested_at"):
        try:
            latest = max(latest, float(entry.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return latest


def due_slugs(
    kind: str,
    *,
    now: float | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    minimum_seconds: int = DEFAULT_MIN_RETRY_SECONDS,
    maximum_seconds: int = DEFAULT_MAX_RETRY_SECONDS,
    stuck_seconds: int = DEFAULT_STUCK_RETRY_SECONDS,
    limit: int = 0,
) -> list[str]:
    """投げ直す番が来た自治体を、古い順に返す。

    投げたきり返事が無いもの（worker の強制終了やメッセージの消失）も、
    最後に積んでから待ち時間を過ぎれば対象になる。失敗の記録が残らない
    経路があるので、「失敗した」ではなく「成功が確認できていない」で拾う。

    まだキューで待っているだけの自治体をここで除くことはできない。
    それは積む側（`index_enqueue` の印）が断る。ここは候補を出すだけ。

    失敗の上限に達したものは止めない。間隔を 3 日に広げて試し続ける。
    索引側の修正が配られたとき、人が投げ直さなくても拾えるようにする。
    """
    current = time.time() if now is None else float(now)
    due: list[tuple[float, str]] = []
    for slug, entry in _read(kind).items():
        attempts = int(entry.get("attempts") or 0)
        enqueues = int(entry.get("enqueues") or 0)
        last_at = _reference_time(entry)
        if attempts >= max_attempts:
            wait = int(stuck_seconds)
        else:
            # 積み直しの間隔も広げる。印が効かない（Redis が読めない）ときに
            # 10 分おきに同じ自治体を積まないため。印が効いていれば、積む側が
            # 断るのでこの間隔は効かない。
            wait = retry_delay_seconds(
                max(attempts, enqueues - 1), minimum=minimum_seconds, maximum=maximum_seconds
            )
        if current - last_at < wait:
            continue
        due.append((last_at, slug))
    due.sort()
    slugs = [slug for _, slug in due]
    return slugs[:limit] if limit > 0 else slugs


def stuck_entries(kind: str, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> dict[str, dict[str, Any]]:
    """失敗の上限に達したもの。3 日おきにしか試さないので、人が見るべき状態。"""
    return {
        slug: entry
        for slug, entry in _read(kind).items()
        if int(entry.get("attempts") or 0) >= max_attempts
    }


def pending_count(kind: str) -> int:
    return len(_read(kind))


def all_entries(kind: str) -> dict[str, dict[str, Any]]:
    return _read(kind)
