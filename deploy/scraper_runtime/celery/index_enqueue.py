#!/usr/bin/env python3
"""索引の積み直しを、同じ自治体で二重に積まないようにする。

索引キューに 2,820 件溜まっていたとき、中身は **450 自治体**でしかなかった。
同じ自治体が最大 9 回積まれていた。1 自治体の再索引に 13〜28 分かかるので、
重複はそのまま待ち時間になる。待ち行列が深いと、世代の追いつきや
`sweep_index_gap` の門（`STALE_SWEEP_QUEUE_LIMIT`）も開かなくなる。

印は broker の Redis に置く。積むときに立て、実行が始まったら消す。
つまり印は「積んだがまだ始まっていない」を意味する。実行後は残らないので、
失敗した自治体の投げ直しを塞がない。Redis が読めないときは印を諦めて
そのまま積む。**重複を避けるためだけの仕組みが、積むこと自体を止めない。**
"""

from __future__ import annotations

from typing import Any

# 印の保険。実行側で消すので通常は使われないが、worker ごと落ちたときに
# 永久に残らないようにする。
MARKER_TTL_SECONDS = 24 * 60 * 60


def marker_key(task_name: str, slug: str) -> str:
    return f"miyabe:index-queued:{task_name}:{slug}"


def _redis_client(app: Any):
    with app.connection_or_acquire() as connection:
        return connection.default_channel.client


def claim(app: Any, task_name: str, slug: str) -> bool:
    """まだ積まれていなければ印を立てて True。既に積まれていれば False。"""
    slug = str(slug or "").strip()
    if not slug:
        return True
    try:
        client = _redis_client(app)
        return bool(client.set(marker_key(task_name, slug), "1", nx=True, ex=MARKER_TTL_SECONDS))
    except Exception:
        # 印を扱えないときは、重複してでも積む。積まない方が害が大きい。
        return True


def release(app: Any, task_name: str, slug: str) -> None:
    """実行が始まったので印を消す。次の変更を積めるようにする。"""
    slug = str(slug or "").strip()
    if not slug:
        return
    try:
        _redis_client(app).delete(marker_key(task_name, slug))
    except Exception:
        return


def send_index_update(app: Any, task_name: str, queue: str, slug: str) -> str:
    """重複していなければ索引更新を積む。積まなかったときは空文字を返す。"""
    if not claim(app, task_name, slug):
        return ""
    return str(app.send_task(task_name, kwargs={"slug": slug}, queue=queue).id)
