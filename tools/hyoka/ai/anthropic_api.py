"""Claude の API を呼ぶ。OpenAI のクレジットが切れたときに sync を別の型で流すためのもの。

キーは data/config.json の claude.apiKey から読み、表示も保存もしない。
混雑と一時的な失敗は SDK がやり直す。SDK は専用の容器（docker/hyoka-ai）にだけあるので、呼ぶときに読み込む。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

MAX_TOKENS = 32000  # 事業の多い区切りは答えが長く、考える分もここに含まれる


def load_key(config_path: Path) -> str:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    key = str(((config.get("claude") or {}).get("apiKey")) or "").strip()
    if not key:
        raise ValueError(f"Claude のキーが無い（{config_path} の claude.apiKey）")
    return key


@lru_cache(maxsize=1)
def client_for(key: str):
    import anthropic

    return anthropic.Anthropic(api_key=key, timeout=600, max_retries=5)


def chat(key: str, model: str, system: str, user: str, schema: dict) -> tuple[str, dict, str]:
    """1 回の問い合わせ。openai_api.chat と同じ（答えの文字、使ったトークン、終わり方）を返す。"""
    with client_for(key).messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        message = stream.get_final_message()
    text = next((block.text for block in message.content if block.type == "text"), "")
    usage = {"prompt_tokens": message.usage.input_tokens, "completion_tokens": message.usage.output_tokens}
    return text, usage, str(message.stop_reason or "")
