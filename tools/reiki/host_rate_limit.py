"""取得元の「取りすぎ」（HTTP 429 Too Many Requests）への備え。

taikei.php が 2026-09 から持っている仕組みを Python の取得器でも使う。

- 429 を受けた取得元は、ホスト単位で 1 日覚える。置き場と形は taikei.php の
  ``host_rate_limits.json``（``work/reiki`` の下、``{ホスト: 受けた時刻の epoch 秒}``）と
  同じにして、PHP と Python で同じ記憶を読み書きする。自治体 1 件ごとに別の
  プロセスなので、プロセスの中だけで覚えると、次の自治体がまた全速で始めて
  429 を受け直す。
- 覚えている取得元には、1 件ごとの間隔を広げて当たる（Pacer）。
- 429 を受けたら、``Retry-After`` を守り、指数的に間を延ばして取り直す
  （Backoff）。待ちきれない（取得元が長く待てと言う、待ちの合計が予算を
  超える）ときは、失敗にせず「後で取り直す」として呼んだ側へ返す。

legal-square（2026-09〜11）は本文ビューアが 429 を返し、本文が空のまま
1.5 秒あけて 1 度だけ開き直していた。20 自治体で 146 本の更新が取れなかった。
"""

from __future__ import annotations

import datetime
import email.utils
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.append(str(Path(__file__).resolve().parents[2]))
from tools.tasks.runner import target_host  # noqa: E402

# 429 を受けた取得元を覚えておく期間。taikei.php の TAIKEI_RATE_LIMIT_MEMORY_SECONDS と同じ。
RATE_LIMIT_MEMORY_SECONDS = 86400
HOST_RATE_LIMITS_FILE = "host_rate_limits.json"

# 本文ビューアの 429 は、ヘッダが取れなくても画面の文面で分かる。
# 本番の警告: 「本文が空でした …: … 429 Too Many Requests Too Many Requests」
_TOO_MANY_RE = re.compile(r"too\s+many\s+requests", re.IGNORECASE)


def rate_limit_host(url: str) -> str:
    """速さを覚える単位のホスト名。

    裏側が 1 つの取得元（``*.legal-square.com``）は 1 つにまとめる。バッチの
    同時実行の数え方（tools/tasks/runner.py の target_host）と同じ単位にする。
    """
    if not str(url or "").strip():
        return ""
    host = target_host({"source_url": str(url)})
    return "" if host == "unknown-host" else host


def is_rate_limited_text(text: str) -> bool:
    """画面の文面が 429 の断りか。"""
    return bool(_TOO_MANY_RE.search(str(text or "")))


def parse_retry_after(value: object, *, now: float | None = None) -> float | None:
    """``Retry-After`` を秒数にする。読めなければ None。

    秒数（``120``）と HTTP の日付（``Wed, 11 Nov 2026 07:00:00 GMT``）の
    どちらでも来る。過去の日付は 0 秒。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


def host_rate_limit_path(work_dir: Path) -> Path:
    """``work/reiki/host_rate_limits.json``。taikei.php の host_rate_limit_path と同じ。"""
    return Path(work_dir) / HOST_RATE_LIMITS_FILE


def _load_marks(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def host_rate_limited_recently(host: str, path: Path, *, now: float | None = None) -> bool:
    """この 1 日のうちに、このホストから 429 を受けたか。"""
    if not host:
        return False
    seen_at = _load_marks(path).get(host)
    if not isinstance(seen_at, int) or isinstance(seen_at, bool) or seen_at <= 0:
        return False
    current = time.time() if now is None else now
    return (current - seen_at) < RATE_LIMIT_MEMORY_SECONDS


def remember_host_rate_limit(host: str, path: Path, *, now: float | None = None) -> None:
    """このホストから 429 を受けたと覚える。taikei.php の remember_host_rate_limit と同じ形。"""
    if not host:
        return
    path = Path(path)
    current = int(time.time() if now is None else now)
    payload = _load_marks(path)
    payload[host] = current
    # 古い印は落とす。取得元が変わっても永久に遅いままにしない。PHP は整数で
    # 書くので、整数でない値も落とす（PHP 側も同じ扱い）。
    payload = {
        key: value
        for key, value in payload.items()
        if isinstance(value, int)
        and not isinstance(value, bool)
        and (current - value) < RATE_LIMIT_MEMORY_SECONDS
    }
    # ほかの取得器も同時に書くので、一時ファイル経由で入れ替える。
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        # 覚えられなくても、このプロセスの中では遅くして続ける。
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class RateLimitDeferred(Exception):
    """取得元が言う待ちを、この実行では待ちきれない。失敗ではなく後で取り直す。"""

    def __init__(self, reason: str, *, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class Pacer:
    """1 件ごとの間隔を保つ。429 を受けたら広げる。

    ``remembered`` は、この取得元から最近 429 を受けたと覚えていたか。
    覚えていたら最初から広い間隔で当たる。
    """

    def __init__(
        self,
        *,
        normal_interval: float,
        slow_interval: float,
        max_interval: float,
        remembered: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.normal_interval = float(normal_interval)
        self.slow_interval = float(slow_interval)
        self.max_interval = float(max_interval)
        self.interval = self.slow_interval if remembered else self.normal_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait_turn(self) -> float:
        """前の 1 件から interval 秒たつまで待つ。待った秒数を返す。"""
        now = self._clock()
        waited = 0.0
        if self._last is not None:
            waited = max(0.0, self._last + self.interval - now)
            if waited > 0:
                self._sleep(waited)
        self._last = self._clock()
        return waited

    def slow_down(self) -> float:
        """429 を受けた。間隔を広げる（初めは slow_interval、続けば倍、上限 max_interval）。"""
        widened = max(self.slow_interval, self.interval * 2)
        self.interval = min(self.max_interval, widened)
        return self.interval


class Backoff:
    """429 のあとの待ちを決める。

    n 回目（1 始まり）の待ちは ``base * 2**(n-1)`` 秒（上限 ``max_wait``）で、
    ``Retry-After`` がそれより長ければそちらを守る。次のどれかなら待たずに
    RateLimitDeferred を投げる（待ちきれない）:

    - ``Retry-After`` が ``max_wait`` を超える
    - 同じものに対して ``max_attempts`` 回待っても通らない
    - この実行での待ちの合計が ``budget`` 秒を超える
    """

    def __init__(
        self,
        *,
        base: float,
        max_wait: float,
        max_attempts: int,
        budget: float,
    ) -> None:
        self.base = float(base)
        self.max_wait = float(max_wait)
        self.max_attempts = int(max_attempts)
        self.budget = float(budget)
        self.total_waited = 0.0
        self.hits = 0

    def next_wait(self, attempt: int, retry_after: float | None) -> float:
        """attempt 回目（1 始まり）の待ち秒数。待ちきれないなら RateLimitDeferred。"""
        self.hits += 1
        if retry_after is not None and retry_after > self.max_wait:
            raise RateLimitDeferred(
                f"取得元が {int(retry_after)} 秒待つよう求めた（Retry-After）",
                retry_after=retry_after,
            )
        if attempt > self.max_attempts:
            raise RateLimitDeferred(
                f"{self.max_attempts} 回待っても取りすぎの制限が解けなかった",
                retry_after=retry_after,
            )
        wait = min(self.max_wait, self.base * (2 ** max(0, attempt - 1)))
        if retry_after is not None:
            wait = max(wait, float(retry_after))
        if self.total_waited + wait > self.budget:
            raise RateLimitDeferred(
                f"取りすぎの制限による待ちがこの実行で {int(self.total_waited)} 秒になり、"
                f"上限の {int(self.budget)} 秒を超える",
                retry_after=retry_after,
            )
        self.total_waited += wait
        return wait
