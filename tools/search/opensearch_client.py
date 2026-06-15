#!/usr/bin/env python3
"""OpenSearch への最小 HTTP クライアント。

build_opensearch_index.py から分離した素の REST 呼び出し層。requests 依存を避け
標準ライブラリだけで動かす（子プロセス実行・最小構成 Docker でも追加依存なしで動く）。

落とし穴メモ:
- 開発用の自己署名 OpenSearch には `insecure_dev=True` を渡す。OpenSSL 3 系では
  `ssl._create_unverified_context()`（private API）ではなく
  `create_default_context()` + `check_hostname=False` + `CERT_NONE` を使う。
- 大量投入は `bulk_lines()`（事前 NDJSON 化した行を 1 リクエストで送る）を使う。
  1 件ずつ POST する直列 ping-pong は会議録 rebuild を律速する（実測 4.8→8.5 docs/s）。
"""

from __future__ import annotations

import base64
import json
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


class OpenSearchRequestError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"OpenSearch {method} {path} failed: HTTP {status}: {body[:800]}")
        self.method = method
        self.path = path
        self.status = status
        self.body = body


class OpenSearchClient:
    def __init__(
        self,
        base_url: str,
        *,
        user: str = "",
        password: str = "",
        insecure_dev: bool = False,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.user = user
        self.password = password
        self.insecure_dev = insecure_dev
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        ndjson: str | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        path = "/" + path.lstrip("/")
        url = urljoin(self.base_url, path.lstrip("/"))
        if query:
            url += "?" + urlencode(query)

        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if ndjson is not None:
            data = ndjson if isinstance(ndjson, bytes) else ndjson.encode("utf-8")
            headers["Content-Type"] = "application/x-ndjson"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self.user or self.password:
            token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        context = None
        if self.insecure_dev and url.lower().startswith("https://"):
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout, context=context) as response:
                raw = response.read().decode("utf-8", errors="replace")
                if raw == "":
                    return {}
                return json.loads(raw)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise OpenSearchRequestError(method.upper(), path, exc.code, raw) from exc
        except URLError as exc:
            raise RuntimeError(f"OpenSearch is unreachable: {exc}") from exc

    def bulk_lines(self, lines: list[bytes], count: int) -> int:
        """事前に NDJSON 化された行群を 1 回の _bulk リクエストで送る。"""
        if not lines:
            return 0
        payload = b"\n".join(lines) + b"\n"
        response = self.request("POST", "/_bulk", ndjson=payload)
        if bool(response.get("errors")):
            errors = []
            for item in response.get("items", []):
                if not isinstance(item, dict):
                    continue
                index_result = item.get("index") or {}
                if isinstance(index_result, dict) and "error" in index_result:
                    errors.append(index_result["error"])
                if len(errors) >= 3:
                    break
            raise RuntimeError(f"OpenSearch bulk request had item errors: {errors!r}")
        return count
