#!/usr/bin/env python3
"""索引の積み直しを、同じ自治体で二重に積まないようにする。

索引キューに 2,820 件溜まっていたとき、中身は **450 自治体**でしかなかった。
同じ自治体が最大 9 回積まれていた。1 自治体の再索引に 13〜28 分かかるので、
重複はそのまま待ち時間になる。待ち行列が深いと、世代の追いつきや
`sweep_index_gap` の門（`STALE_SWEEP_QUEUE_LIMIT`）も開かなくなる。

印は broker の Redis に置く。積むときに立て、**実行が終わったら消す**。
つまり印は「積んだか、いま実行中」を意味する。失敗した自治体の投げ直しは
塞がない（失敗も終わりなので消える）。Redis が読めないときは印を諦めて
そのまま積む。**重複を避けるためだけの仕組みが、積むこと自体を止めない。**

印の寿命は 2 段にする。積んだ直後は、待ち行列が数日分あっても実行まで
生き残るように長く（14 日）。実行が始まったら、worker ごと落ちても
永久に残らないように短く（12 時間）。最初は 24 時間ひとつだったが、
待ち行列が 130 時間分あると実行前に印が消え、重複がまた積まれた。
"""

from __future__ import annotations

from typing import Any

# 積んでから実行が始まるまでの保険。待ち行列の最大滞留時間より長く取る。
# 1,500 自治体 × 15 分 = 375 時間なので、14 日あれば足りる。
QUEUED_TTL_SECONDS = 14 * 24 * 60 * 60
# 実行中の保険。終われば消すので通常は使われないが、worker ごと落ちたとき
# 永久に残らないようにする。最長の再索引（北海道 10,000 件）より十分長く。
RUNNING_TTL_SECONDS = 12 * 60 * 60


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
        return bool(client.set(marker_key(task_name, slug), "1", nx=True, ex=QUEUED_TTL_SECONDS))
    except Exception:
        # 印を扱えないときは、重複してでも積む。積まない方が害が大きい。
        return True


def started(app: Any, task_name: str, slug: str) -> None:
    """実行が始まった。印は残すが、寿命を実行中の長さに縮める。

    印が無い（寿命切れ・Redis の入れ替え）まま始まったときも立て直す。
    実行中に掃き取りが同じ自治体を積むのを防ぐため。
    """
    slug = str(slug or "").strip()
    if not slug:
        return
    try:
        _redis_client(app).set(marker_key(task_name, slug), "running", ex=RUNNING_TTL_SECONDS)
    except Exception:
        return


def hold(app: Any, task_name: str, slug: str, seconds: int) -> None:
    """その場の投げ直しを待つ間、印を保つ。"""
    slug = str(slug or "").strip()
    if not slug:
        return
    try:
        _redis_client(app).set(
            marker_key(task_name, slug), "retry", ex=max(60, int(seconds)) + RUNNING_TTL_SECONDS
        )
    except Exception:
        return


def release(app: Any, task_name: str, slug: str) -> None:
    """実行が終わった（成功でも失敗でも）ので印を消す。次の変更を積めるようにする。"""
    slug = str(slug or "").strip()
    if not slug:
        return
    try:
        _redis_client(app).delete(marker_key(task_name, slug))
    except Exception:
        return


def is_held(app: Any, task_name: str, slug: str) -> bool:
    """積まれているか実行中か。読めなければ False（積む側は claim で決める）。"""
    slug = str(slug or "").strip()
    if not slug:
        return False
    try:
        return bool(_redis_client(app).exists(marker_key(task_name, slug)))
    except Exception:
        return False


def send_index_update(app: Any, task_name: str, queue: str, slug: str) -> str:
    """重複していなければ索引更新を積む。積まなかったときは空文字を返す。"""
    if not claim(app, task_name, slug):
        return ""
    try:
        return str(app.send_task(task_name, kwargs={"slug": slug}, queue=queue).id)
    except Exception:
        # 積めなかったのに印だけ残ると、次の掃き取りまで誰も積めない。
        release(app, task_name, slug)
        raise
