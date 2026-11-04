"""OpenAI の API（Chat Completions と Batch）を標準ライブラリだけで呼ぶ。

キーは data/config.json の openai.apiKey から読み、表示も保存もしない。
混雑（429）と一時的な失敗（5xx・接続の切れ）は、間を空けてやり直す。
クレジット切れ（429 の insufficient_quota）は待っても直らないので、やり直さない。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://api.openai.com/v1"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


def load_key(config_path: Path) -> str:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    key = str(((config.get("openai") or {}).get("apiKey")) or "").strip()
    if not key:
        raise ValueError(f"OpenAI のキーが無い（{config_path} の openai.apiKey）")
    return key


def _request(key: str, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None,
             timeout: float = 300, retries: int = 5) -> bytes:
    headers = {"Authorization": f"Bearer {key}"}
    if content_type:
        headers["Content-Type"] = content_type
    delay = 5.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            if error.code in RETRY_STATUS and attempt < retries and "insufficient_quota" not in detail:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise RuntimeError(f"OpenAI {method} {path}: HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise RuntimeError(f"OpenAI {method} {path}: {error}") from None
    raise RuntimeError("unreachable")


def chat_body(model: str, system: str, user: object, schema: dict, name: str, *, reasoning_effort: str = "low") -> dict:
    """Chat Completions の要求の本体。user は文字か、画像を含む内容の配列。"""
    return {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        "reasoning_effort": reasoning_effort,
    }


def chat(key: str, body: dict, *, timeout: float = 600) -> tuple[str, dict, str]:
    """1 回の問い合わせ。（答えの文字、使ったトークン、終わり方）を返す。"""
    data = json.loads(_request(key, "POST", "/chat/completions", body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                               content_type="application/json", timeout=timeout))
    choice = data["choices"][0]
    return choice["message"]["content"] or "", data.get("usage") or {}, str(choice.get("finish_reason") or "")


def upload_batch_file(key: str, path: Path) -> str:
    """一括処理の入力ファイル（JSONL）を送り、ファイルの ID を返す。"""
    boundary = uuid.uuid4().hex
    content = Path(path).read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{Path(path).name}\"\r\n"
            f"Content-Type: application/jsonl\r\n\r\n").encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    data = json.loads(_request(key, "POST", "/files", body=body, content_type=f"multipart/form-data; boundary={boundary}",
                               timeout=1800))
    return data["id"]


def create_batch(key: str, file_id: str, metadata: dict | None = None) -> dict:
    body = {"input_file_id": file_id, "endpoint": "/v1/chat/completions", "completion_window": "24h",
            "metadata": metadata or {}}
    return json.loads(_request(key, "POST", "/batches", body=json.dumps(body).encode("utf-8"),
                               content_type="application/json"))


def get_batch(key: str, batch_id: str) -> dict:
    return json.loads(_request(key, "GET", f"/batches/{batch_id}"))


def download_file(key: str, file_id: str, dest: Path) -> None:
    content = _request(key, "GET", f"/files/{file_id}/content", timeout=1800)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(dest)
