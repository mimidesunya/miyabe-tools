"""長時間動くスクレイピングバッチ向けの共通プロセス制御。

会議録と例規集の batch runner はどちらも、ホスト単位の間引き、
子プロセスログ、シグナル処理、進捗抽出を必要とする。ここへまとめることで、
2 つのバッチループの細部が少しずつずれるのを防ぐ。
"""

from __future__ import annotations

import json
import heapq
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit


# バッチ run_id に使う短い現在時刻文字列を作る。
def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# slug / code / name などにフィルタ文字列が含まれるか判定する。
def target_matches(target: dict, keyword: str, *, extra_fields: tuple[str, ...] = ()) -> bool:
    if keyword == "":
        return True
    fields = ("slug", "code", "name", "full_name") + tuple(extra_fields)
    haystacks = [str(target.get(field, "")).lower() for field in fields]
    return any(keyword in value for value in haystacks)


# 自治体の source_url からホスト名を取り出し、同一ホスト制御のキーにする。
def target_host(target: dict) -> str:
    source_url = str(target.get("source_url", "")).strip()
    host = (urlsplit(source_url).hostname or "").strip().lower()
    return host or "unknown-host"


# 大きなログでも末尾だけ読む。進捗表示やエラー要約で使う。
def tail_text_lines(path: Path, max_bytes: int = 8192) -> list[str]:
    # 子プロセスのログは並行して書かれ消されるので、消失や権限エラーは空扱いにする。
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            if size == 0:
                return []
            read_size = min(size, max_bytes)
            handle.seek(-read_size, os.SEEK_END)
            chunk = handle.read(read_size)
    except OSError:
        return []
    text = chunk.decode("utf-8", errors="replace")
    return [line.rstrip() for line in text.splitlines() if line.strip()]


# stdout/stderr から WARN 相当の行だけを拾い、自治体カードの警告に回す。
def extract_warning_lines(*paths: Path, max_bytes: int = 32768, max_lines: int = 20) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for line in tail_text_lines(path, max_bytes=max_bytes):
            stripped = line.strip()
            if not stripped:
                continue
            if "[WARN]" not in stripped and "WARNING" not in stripped.upper() and "警告" not in stripped:
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            warnings.append(stripped)
    return warnings[-max_lines:]


# scrape_state と実ファイルを照合し、未取得項目を警告文に変換する。
def scrape_state_warning_lines(
    state_path: Path,
    *,
    downloads_dir: Path | None = None,
    max_examples: int = 5,
) -> list[str]:
    # 子スクレイパの scrape_state には「一覧には載ったがファイルが無い」項目が残る。
    # その差分をタスク表示の警告へ上げ、取得未完了を正常完了に見せない。
    if not state_path.is_file():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    validation = state.get("validation")
    if isinstance(validation, dict) and str(validation.get("mode") or "") == "classified_scrape_result":
        raw_lines = validation.get("warning_lines")
        if isinstance(raw_lines, list):
            lines: list[str] = []
            for line in raw_lines:
                stripped = re.sub(r"\s+", " ", str(line or "")).strip()
                if stripped and stripped not in lines:
                    lines.append(stripped)
            if lines:
                return lines[-max(0, max_examples + 3):]
    items = state.get("items")
    if not isinstance(items, dict):
        return []

    base_downloads_dir = downloads_dir or state_path.parent / "downloads"
    missing: list[dict] = []
    error_count = 0
    for item in items.values():
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("output_rel_path") or "").strip()
        has_output = bool(rel_path and (base_downloads_dir / rel_path).is_file())
        if has_output:
            continue
        missing.append(item)
        if str(item.get("status") or "").strip() == "error":
            error_count += 1

    if not missing:
        return []

    lines = [f"取得できていない項目 {len(missing)}件（うちエラー {error_count}件）"]
    for item in missing[:max(0, max_examples)]:
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip() or "名称不明"
        status = str(item.get("status") or "").strip() or "未取得"
        error = re.sub(r"\s+", " ", str(item.get("error") or "")).strip()
        if len(error) > 96:
            error = error[:95] + "..."
        detail = f"{title}: {status}"
        if error:
            detail += f" - {error}"
        lines.append(detail)
    if len(missing) > max_examples:
        lines.append(f"ほか {len(missing) - max_examples}件")
    return lines


# 子プロセスのログ末尾から、画面に出す短い作業中メッセージを作る。
def summarize_worker(stdout_path: Path, stderr_path: Path) -> str:
    if stderr_path.exists() and stderr_path.stat().st_size > 0:
        return f"stderr {stderr_path.stat().st_size} bytes"

    lines = tail_text_lines(stdout_path)
    if not lines:
        return "starting..."

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[INFO] "):
            return stripped[7:]
        if stripped.startswith("[DONE] "):
            return stripped[7:]
        if stripped.startswith("[ERROR] "):
            return stripped
        if stripped.startswith("[PROGRESS] "):
            continue
        progress_match = re.match(r"^\[\d+/\d+\]\s*(.*)$", stripped)
        if progress_match:
            detail = progress_match.group(1).strip()
            if re.search(r"\b(downloaded|checked|skipped|parsed|reused)=\d+\b", detail):
                return "既存データを確認中"
            if re.match(r"^Found\s+\d+\s+(unique regulation IDs|ordinance pages)\b", detail, re.IGNORECASE):
                return "例規一覧を確認中"
            return detail or "処理中"
        return stripped
    return "starting..."


# SIGTERM/SIGINT を受けたかをメインループから参照するための小さな状態オブジェクト。
class StopController:
    # 停止要求の初期状態を作る。
    def __init__(self) -> None:
        self.requested = False
        self.signum: int | None = None

    # シグナルハンドラから停止要求を記録する。
    def request(self, signum: int) -> None:
        self.requested = True
        self.signum = signum

    # メインループが新規起動を止めるべきかを返す。
    def should_stop(self) -> bool:
        return self.requested

    # 停止理由として item に残す returncode を返す。
    def returncode(self) -> int:
        return -(self.signum or signal.SIGTERM)


# SIGTERM/SIGINT を捕まえ、子プロセスを安全に止めるための controller を返す。
def install_stop_signal_handlers() -> StopController:
    controller = StopController()

    # OS シグナルを StopController へのフラグ更新に変換する。
    def handle_stop(signum, _frame) -> None:
        controller.request(int(signum))

    for signame in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            signal.signal(signum, handle_stop)
    return controller


# 子プロセスをプロセスグループ化するための Popen 引数を OS 別に返す。
def process_group_popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


# 子プロセスとその配下を SIGTERM -> SIGKILL の順で停止する。
def terminate_process_group(process: subprocess.Popen, *, grace_seconds: float = 20.0) -> int | None:
    returncode = process.poll()
    if returncode is not None:
        return int(returncode)

    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return process.poll()
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass

    try:
        return int(process.wait(timeout=max(0.1, grace_seconds)))
    except subprocess.TimeoutExpired:
        pass

    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return process.poll()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

    try:
        return int(process.wait(timeout=5.0))
    except subprocess.TimeoutExpired:
        return process.poll()


# 子スクレイパが書く scrape_state.json から current/total を読む。
def extract_worker_progress_from_state(state_path: Path, *, default_unit: str) -> dict[str, object] | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    progress_source = payload
    validation = payload.get("validation")
    if isinstance(validation, dict) and str(validation.get("mode") or "") == "classified_scrape_result":
        progress_source = validation

    current = progress_source.get("progress_current")
    total = progress_source.get("progress_total")
    if current is None or total is None:
        return None
    try:
        progress_current = int(current)
        progress_total = int(total)
    except Exception:
        return None
    if progress_total < 0:
        return None

    return {
        "progress_current": max(0, progress_current),
        "progress_total": max(0, progress_total),
        "progress_unit": str(progress_source.get("progress_unit", default_unit)).strip() or default_unit,
    }


# 旧形式の [PROGRESS] ログから current/total を読む。
def extract_worker_progress_from_log(stdout_path: Path, progress_re: re.Pattern[str]) -> dict[str, object] | None:
    lines = tail_text_lines(stdout_path, max_bytes=16_384)
    for line in reversed(lines):
        match = progress_re.match(line.strip())
        if not match:
            continue
        return {
            "progress_current": int(match.group("current")),
            "progress_total": int(match.group("total")),
            "progress_unit": match.group("unit"),
        }
    return None


# stdout/stderr をファイルへ流しながら子プロセスを監視実行する。
def run_logged_subprocess(
    command: list[str],
    *,
    cwd: str,
    stdout_path: Path,
    stderr_path: Path,
    heartbeat_callback=None,
    should_stop=None,
    poll_seconds: float = 5.0,
) -> subprocess.CompletedProcess:
    with stdout_path.open("w", encoding="utf-8", newline="") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8", newline=""
    ) as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=stdout_handle,
            stderr=stderr_handle,
            **process_group_popen_kwargs(),
        )

        try:
            while True:
                returncode = process.poll()
                if returncode is not None:
                    return subprocess.CompletedProcess(command, int(returncode))
                if should_stop is not None and should_stop():
                    returncode = terminate_process_group(process)
                    return subprocess.CompletedProcess(command, int(returncode if returncode is not None else -15))
                if heartbeat_callback is not None:
                    heartbeat_callback()
                time.sleep(max(0.5, poll_seconds))
        except BaseException:
            terminate_process_group(process)
            raise


# worker 辞書に保持したログファイルハンドルを閉じる。
def close_worker_streams(worker: dict) -> None:
    for key in ("stdout_handle", "stderr_handle"):
        handle = worker.get(key)
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:
            pass
        worker[key] = None


# 起動中 worker をホストごとに数え、同一ホスト上限の判定に使う。
def count_active_by_host(active_workers: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for worker in active_workers:
        host = str(worker["host"])
        counts[host] = counts.get(host, 0) + 1
    return counts

# ひとつの優先度付きキューから、実行可能な自治体を先頭順に取り出す。
# 同一ホスト制限などで今だけ起動できないものは一時退避し、順序を保って戻す。
class PriorityTargetQueue:
    # 初期 target 群を優先度付き heap に積む。
    def __init__(self, targets: list[dict], key_func) -> None:
        self._key_func = key_func
        self._sequence = 0
        self._heap: list[tuple[object, int, dict]] = []
        for target in targets:
            self.push(target)

    # キューに未起動 target が残っているかを bool として返す。
    def __bool__(self) -> bool:
        return bool(self._heap)

    # 未起動 target の件数を返す。
    def __len__(self) -> int:
        return len(self._heap)

    # target を優先度順に取り出せるよう heap へ追加する。
    def push(self, target: dict) -> None:
        heapq.heappush(self._heap, (self._key_func(target), self._sequence, target))
        self._sequence += 1

    # 停止時などに未起動 target をすべて捨てる。
    def clear(self) -> None:
        self._heap.clear()

    # まだ起動していない target を優先度順の一覧として返す。
    def remaining_targets(self) -> list[dict]:
        return [entry[2] for entry in sorted(self._heap)]

    # 優先度順に見て、ホスト制限などを満たす最初の target だけを取り出す。
    def pop_runnable(self, can_launch) -> dict | None:
        blocked: list[tuple[object, int, dict]] = []
        try:
            while self._heap:
                entry = heapq.heappop(self._heap)
                target = entry[2]
                if can_launch(target):
                    return target
                blocked.append(entry)
            return None
        finally:
            for entry in blocked:
                heapq.heappush(self._heap, entry)
