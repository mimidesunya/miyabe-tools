#!/usr/bin/env python3
"""PDF の本文を子のプロセスで抜き出す。時間を超えたら子を止めて打ち切る。

pypdf は図面などで 1 本に何十秒もかかることがあり（2026-11-14 に全国 400 本で試すと
最大 49 秒）、壊れた PDF では終わらないこともありうる。お知らせの取得は 1 つの worker で
フィード・本文・添付を順に読むので、1 本が終わらないとフィードの取得まで止まる。

親（`PdfTextWorker`）と子（このファイルを `-m` で動かす）は標準入出力でやりとりする。
    親 → 子: 4 バイトの大きさ・4 バイトの頁の上限・PDF
    子 → 親: 4 バイトの大きさ・JSON {"pages": [...], "total": 頁数}
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

HEADER = struct.Struct(">II")
SIZE = struct.Struct(">I")


class PdfTimeout(Exception):
    """時間内に読み終わらなかった（子は止めた）。"""


class PdfTextWorker:
    """子のプロセスを 1 つ持ち、PDF を 1 本ずつ読ませる。同時に呼ばれたら順に待つ。"""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def _ensure(self) -> subprocess.Popen:
        if self._process is None or self._process.poll() is not None:
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(WORKSPACE_ROOT), env.get("PYTHONPATH", "")]))
            self._process = subprocess.Popen(
                [sys.executable, "-m", "tools.news.pdf_text_worker"],
                cwd=str(WORKSPACE_ROOT), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        return self._process

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.kill()
            process.wait(timeout=10)
        except Exception:
            pass

    def read(self, raw: bytes, max_pages: int) -> tuple[list[str], int]:
        """(頁ごとの本文, 全体の頁数)。時間切れは PdfTimeout、子が落ちたら RuntimeError。"""
        with self._lock:
            process = self._ensure()
            result: dict[str, object] = {}

            def exchange() -> None:
                try:
                    process.stdin.write(HEADER.pack(len(raw), max_pages) + raw)
                    process.stdin.flush()
                    head = process.stdout.read(SIZE.size)
                    if len(head) < SIZE.size:
                        return
                    payload = process.stdout.read(SIZE.unpack(head)[0])
                    result["value"] = json.loads(payload.decode("utf-8"))
                except Exception as exc:  # 子が落ちた
                    result["error"] = exc

            thread = threading.Thread(target=exchange, daemon=True)
            thread.start()
            thread.join(self.timeout)
            if thread.is_alive():
                self._stop()
                thread.join(10)
                raise PdfTimeout(f"{self.timeout:g} 秒で読み終わらない")
            value = result.get("value")
            if not isinstance(value, dict):
                self._stop()
                raise RuntimeError(f"PDF を読む子のプロセスが答えない: {result.get('error') or '終了した'}")
            return [str(page) for page in value.get("pages") or []], int(value.get("total") or 0)

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=10)
        except Exception:
            process.kill()


def serve() -> int:
    from tools.document_crawl import read_pdf_pages

    source = sys.stdin.buffer
    sink = sys.stdout.buffer
    while True:
        head = source.read(HEADER.size)
        if len(head) < HEADER.size:
            return 0
        size, max_pages = HEADER.unpack(head)
        raw = source.read(size)
        pages, total = read_pdf_pages(raw, max_pages)
        payload = json.dumps({"pages": pages, "total": total}, ensure_ascii=False).encode("utf-8")
        sink.write(SIZE.pack(len(payload)) + payload)
        sink.flush()


if __name__ == "__main__":
    raise SystemExit(serve())
