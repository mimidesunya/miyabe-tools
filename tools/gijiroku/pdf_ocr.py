#!/usr/bin/env python3
"""文字情報のない PDF を OCR して本文にする。

会議録の PDF には、紙をスキャンしただけで文字情報を持たないものがある。
`extract_pdf_text` は空文字を返し、その会議は `empty_pdf_text` として
除外される。2026-09-06 の点検では 144 自治体・3,860 件がこの形だった。
OCR が無い限り、何周回しても同じように除外される。

使うのは国立国会図書館の NDLOCR-Lite（CC BY 4.0）。GPU を必要とせず、
モデル（ONNX 4 本・157MB）を同梱していて、実行時のダウンロードも
API キーも要らない。実測 0.7 秒/ページ。

**通常の巡回では動かさない。** 1 件あたり数十秒かかるので、取得の周期に
混ぜると一巡が数日延びる。環境変数で明示的に有効にしたときだけ働く。
専用の掃き取り（`tools/tasks/ocr_backfill.py`）から使う。

再試行の止め方: 同じ PDF を毎回 OCR し直さないよう、試した回数と元 PDF の
指紋を自治体ごとに残す。指紋が変われば（取得元が差し替えたら）また試す。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# NDLOCR-Lite の置き場所。コンテナでは /opt/ndlocr-lite に置く。
DEFAULT_TOOL_DIRS = (
    "/opt/ndlocr-lite",
    str(Path.home() / "ndlocr-lite"),
)
# 1 件に許す時間。100 ページで 70 秒ほど。長い会議録と壊れた PDF の両方を見込む。
DEFAULT_TIMEOUT_SECONDS = 1800
# ページ数の上限。これを超える PDF は OCR せず、別の理由として残す。
DEFAULT_MAX_PAGES = 1000
# 同じ PDF を試す回数。取れないものは何度やっても取れない。
MAX_ATTEMPTS = 2
JST = timezone(timedelta(hours=9))


def now_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def is_enabled() -> bool:
    """OCR を使うかどうか。通常の巡回では無効。"""
    return str(os.environ.get("MIYABE_MINUTES_OCR", "")).strip().lower() in {"1", "true", "yes", "on"}


def tool_directory() -> Path | None:
    """NDLOCR-Lite の場所。見つからなければ None。"""
    candidates = []
    configured = str(os.environ.get("MIYABE_NDLOCR_DIR", "")).strip()
    if configured:
        candidates.append(configured)
    candidates.extend(DEFAULT_TOOL_DIRS)
    for candidate in candidates:
        path = Path(candidate)
        if (path / "src" / "ocr.py").is_file():
            return path
    return None


def python_executable() -> str:
    """OCR を動かす python。専用の環境を指せるようにしておく。"""
    configured = str(os.environ.get("MIYABE_NDLOCR_PYTHON", "")).strip()
    return configured or sys.executable


def pdf_page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


def ocr_pdf_text(
    pdf_path: Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[str, str]:
    """PDF を OCR して本文を返す。返すのは (本文, 失敗理由)。

    成功したら理由は空文字。取れなかったときだけ理由が入る。
    """
    tool = tool_directory()
    if tool is None:
        return "", "NDLOCR-Lite が見つからない"
    source = Path(pdf_path)
    if not source.is_file():
        return "", "PDF が保存されていない"

    pages = pdf_page_count(source)
    if max_pages > 0 and pages > max_pages:
        return "", f"ページ数が多すぎる（{pages}ページ）"

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "out"
        output.mkdir(parents=True, exist_ok=True)
        # 日本語のファイル名で落ちることがあるので、英数字の名前へ写してから渡す。
        staged = Path(directory) / "input.pdf"
        try:
            shutil.copyfile(source, staged)
        except Exception as error:
            return "", f"PDF を写せない: {error}"

        command = [
            python_executable(),
            str(tool / "src" / "ocr.py"),
            "--sourcepdf", str(staged),
            "--output", str(output),
            "--device", "cpu",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(60, int(timeout_seconds)),
                cwd=str(tool / "src"),
            )
        except subprocess.TimeoutExpired:
            return "", f"時間切れ（{timeout_seconds}秒）"
        except Exception as error:
            return "", f"起動に失敗: {error}"

        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()
            return "", f"OCR が失敗（終了コード {completed.returncode}）: {tail[-1] if tail else ''}"

        texts = sorted(output.glob("*.txt"))
        if not texts:
            return "", "OCR がテキストを出さなかった"
        try:
            body = texts[0].read_text(encoding="utf-8", errors="replace")
        except Exception as error:
            return "", f"OCR の出力を読めない: {error}"

    body = body.strip()
    if not body:
        return "", "OCR の結果が空だった"
    return body, ""


# --- 再試行の記録 ----------------------------------------------------------


def attempts_path(work_dir: Path) -> Path:
    return Path(work_dir) / "ocr_attempts.json"


def load_attempts(work_dir: Path) -> dict[str, dict]:
    try:
        payload = json.loads(attempts_path(work_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_attempts(work_dir: Path, payload: dict[str, dict]) -> None:
    path = attempts_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o644)
    except OSError:
        pass
    temporary.replace(path)


def file_digest(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:32]
    except Exception:
        return ""


def should_try(work_dir: Path, key: str, digest: str) -> bool:
    """まだ試す価値があるか。

    取れなかった PDF を毎周回 OCR し直すと、CPU をそれだけで使い切る。
    同じ中身に対しては上限回数で打ち切り、取得元が差し替えたら再開する。
    """
    entry = load_attempts(work_dir).get(str(key))
    if not entry:
        return True
    if str(entry.get("status") or "") == "ok":
        return False
    if digest and str(entry.get("digest") or "") != digest:
        # 元の PDF が変わった。前回の結果は当てにならないので数え直す。
        return True
    return int(entry.get("attempts") or 0) < MAX_ATTEMPTS


def record_attempt(work_dir: Path, key: str, digest: str, *, status: str, reason: str = "") -> None:
    payload = load_attempts(work_dir)
    entry = payload.get(str(key)) or {}
    attempts = int(entry.get("attempts") or 0)
    if digest and str(entry.get("digest") or "") != digest:
        attempts = 0
    payload[str(key)] = {
        "attempts": attempts + 1,
        "digest": digest,
        "status": status,
        "reason": reason,
        "last_at": now_text(),
    }
    save_attempts(work_dir, payload)
