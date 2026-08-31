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


# 何度も失敗し続けるものを無限に投げ直さない。人が見るべき状態として残す。
DEFAULT_MAX_ATTEMPTS = 8
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
    return {str(slug): dict(entry) for slug, entry in entries.items() if isinstance(entry, dict)}


def _write(kind: str, entries: dict[str, dict[str, Any]]) -> None:
    path = outbox_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
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


def mark_attempted(kind: str, slug: str) -> None:
    """投げ直したことだけを控える。成否はタスク側が確定させる。

    ここで試行回数を進めておかないと、掃き取りが回るたびに同じ自治体へ
    何度も投げてしまう。"""
    slug = str(slug).strip()
    if slug == "":
        return
    entries = _read(kind)
    entry = entries.get(slug)
    if entry is None:
        return
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["last_attempt_at"] = time.time()
    entries[slug] = entry
    _write(kind, entries)


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


def due_slugs(
    kind: str,
    *,
    now: float | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    minimum_seconds: int = DEFAULT_MIN_RETRY_SECONDS,
    maximum_seconds: int = DEFAULT_MAX_RETRY_SECONDS,
    limit: int = 0,
) -> list[str]:
    """投げ直す番が来た自治体を、古い順に返す。

    投げたきり返事が無いもの（worker の強制終了やメッセージの消失）も、
    最初の要求から待ち時間を過ぎれば対象になる。失敗の記録が残らない
    経路があるので、「失敗した」ではなく「成功が確認できていない」で拾う。
    """
    current = time.time() if now is None else float(now)
    due: list[tuple[float, str]] = []
    for slug, entry in _read(kind).items():
        attempts = int(entry.get("attempts") or 0)
        if attempts >= max_attempts:
            continue
        last = entry.get("last_attempt_at") or entry.get("requested_at") or entry.get("first_requested_at") or 0
        try:
            last_at = float(last)
        except (TypeError, ValueError):
            last_at = 0.0
        wait = retry_delay_seconds(attempts, minimum=minimum_seconds, maximum=maximum_seconds)
        if current - last_at < wait:
            continue
        due.append((last_at, slug))
    due.sort()
    slugs = [slug for _, slug in due]
    return slugs[:limit] if limit > 0 else slugs


def stuck_entries(kind: str, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> dict[str, dict[str, Any]]:
    """試行の上限に達して、人が見るまで動かないもの。収集状況へ出すために使う。"""
    return {
        slug: entry
        for slug, entry in _read(kind).items()
        if int(entry.get("attempts") or 0) >= max_attempts
    }


def pending_count(kind: str) -> int:
    return len(_read(kind))


def all_entries(kind: str) -> dict[str, dict[str, Any]]:
    return _read(kind)
