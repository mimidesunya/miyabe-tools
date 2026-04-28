#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import locale
import re
import sys
from functools import lru_cache

from sudachipy import Dictionary, SplitMode


# 日本語全文検索では、記号や空白を落として語形正規化したトークン列を作る。
SPACE_PATTERN = re.compile(r"\s+", re.UNICODE)
QUERY_TOKEN_PATTERN = re.compile(
    r'"[^"]*"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bNEAR(?:/\d+)?\b|[^\s()"]+',
    re.IGNORECASE,
)
NON_WORD_PATTERN = re.compile(r"^[\W_]+$", re.UNICODE)
TOKENIZER_SPLIT_PATTERN = re.compile(r"(\n+|[。．！？?!])")
MAX_SUDACHI_INPUT_BYTES = 40000


def normalize_fragment(value: str) -> str:
    return SPACE_PATTERN.sub(" ", (value or "").strip())


def unique_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        if value == "" or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


@lru_cache(maxsize=1)
def sudachi_tokenizer():
    # B 分割は「短すぎず長すぎず」で、会議録と例規集の両方で扱いやすい。
    dictionary = Dictionary(dict="core")
    return dictionary.create(
        mode=SplitMode.B,
        fields={"pos", "normalized_form", "dictionary_form"},
    )


def morpheme_is_searchable(morpheme) -> bool:
    surface = normalize_fragment(morpheme.surface())
    if surface == "" or NON_WORD_PATTERN.fullmatch(surface):
        return False

    pos = morpheme.part_of_speech()
    pos_head = str(pos[0]) if pos else ""
    if pos_head in {"補助記号", "空白"}:
        return False
    return True


def morpheme_variants(morpheme) -> list[str]:
    variants = [
        normalize_fragment(morpheme.surface()),
        normalize_fragment(morpheme.normalized_form()),
        normalize_fragment(morpheme.dictionary_form()),
    ]
    return unique_preserve([value for value in variants if value and value != "*"])


def utf8_size(text: str) -> int:
    return len((text or "").encode("utf-8"))


def read_stdin_text() -> str:
    data = sys.stdin.buffer.read()
    if not data:
        return ""

    encodings = unique_preserve([
        "utf-8",
        "utf-8-sig",
        str(sys.stdin.encoding or ""),
        locale.getpreferredencoding(False),
    ])
    for encoding in encodings:
        if encoding == "":
            continue
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def split_large_piece(text: str, *, max_bytes: int) -> list[str]:
    items: list[str] = []
    buffer = ""
    buffer_bytes = 0
    for char in text:
        char_bytes = utf8_size(char)
        if buffer and buffer_bytes + char_bytes > max_bytes:
            items.append(buffer)
            buffer = char
            buffer_bytes = char_bytes
            continue
        buffer += char
        buffer_bytes += char_bytes
    if buffer:
        items.append(buffer)
    return items


def split_text_for_tokenizer(text: str, *, max_bytes: int = MAX_SUDACHI_INPUT_BYTES) -> list[str]:
    normalized = text or ""
    if normalized == "" or utf8_size(normalized) <= max_bytes:
        return [normalized]

    # SudachiPy の入力上限を超える本文は、句点や改行を優先しつつ安全な長さに分割する。
    pieces: list[str] = []
    for piece in TOKENIZER_SPLIT_PATTERN.split(normalized):
        if piece == "":
            continue
        if utf8_size(piece) <= max_bytes:
            pieces.append(piece)
            continue
        pieces.extend(split_large_piece(piece, max_bytes=max_bytes))

    chunks: list[str] = []
    current = ""
    current_bytes = 0
    for piece in pieces:
        piece_bytes = utf8_size(piece)
        if current and current_bytes + piece_bytes > max_bytes:
            chunks.append(current)
            current = piece
            current_bytes = piece_bytes
            continue
        current += piece
        current_bytes += piece_bytes
    if current:
        chunks.append(current)
    return chunks or [normalized]


def tokenize_text(text: str):
    tokenizer = sudachi_tokenizer()
    chunks = split_text_for_tokenizer(text or "")
    if len(chunks) == 1:
        return tokenizer.tokenize(chunks[0])

    morphemes = []
    for chunk in chunks:
        morphemes.extend(tokenizer.tokenize(chunk))
    return morphemes


def document_terms_text(text: str) -> str:
    terms: list[str] = []
    for morpheme in tokenize_text(text):
        if not morpheme_is_searchable(morpheme):
            continue
        # 文書側は表記ゆれを吸収したいので、表層形と正規形を両方入れる。
        terms.extend(morpheme_variants(morpheme))
    return " ".join(terms)


def document_terms_map(values: dict[str, str]) -> dict[str, str]:
    return {key: document_terms_text(value) for key, value in values.items()}


def searchable_morphemes(text: str):
    return [morpheme for morpheme in tokenize_text(text) if morpheme_is_searchable(morpheme)]


def surface_terms_from_morphemes(morphemes, *, unique: bool = True) -> list[str]:
    items: list[str] = []
    for morpheme in morphemes:
        surface = normalize_fragment(morpheme.surface())
        if surface:
            items.append(surface)
    return unique_preserve(items) if unique else items


def surface_terms(text: str) -> list[str]:
    return surface_terms_from_morphemes(searchable_morphemes(text))


def fts_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def build_term_query_from_morphemes(token: str, morphemes) -> str:
    if token == "":
        return ""

    clauses: list[str] = []
    for morpheme in morphemes:
        variants = morpheme_variants(morpheme)
        if not variants:
            continue
        if len(variants) == 1:
            clauses.append(fts_quote(variants[0]))
        else:
            clauses.append("(" + " OR ".join(fts_quote(value) for value in variants) + ")")

    if not clauses:
        return fts_quote(token)
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " AND ".join(clauses) + ")"


def build_term_query(token: str) -> str:
    token = normalize_fragment(token)
    return build_term_query_from_morphemes(token, searchable_morphemes(token))


def build_phrase_query_from_morphemes(token: str, morphemes) -> str:
    if token == "":
        return ""

    surfaces = surface_terms_from_morphemes(morphemes, unique=False)
    if not surfaces:
        return fts_quote(token)
    return fts_quote(" ".join(surfaces))


def build_phrase_query(token: str) -> str:
    token = normalize_fragment(token)
    return build_phrase_query_from_morphemes(token, searchable_morphemes(token))


def append_unique(items: list[str], values: list[str]) -> None:
    seen = set(items)
    for value in values:
        if value == "" or value in seen:
            continue
        seen.add(value)
        items.append(value)


def build_query_payload(text: str) -> dict[str, object]:
    parts: list[str] = []
    highlight_terms: list[str] = []
    exact_phrases: list[str] = []
    exact_phrases_supported = True
    skip_highlight = False
    for token in QUERY_TOKEN_PATTERN.findall(text or ""):
        if token == "":
            continue

        upper = token.upper()
        if token in {"(", ")"} or upper in {"AND", "OR", "NOT"} or upper.startswith("NEAR"):
            parts.append(upper if upper != token else token)
            if upper == "OR" or upper.startswith("NEAR"):
                exact_phrases_supported = False
            if upper == "NOT":
                skip_highlight = True
            continue

        is_negated = skip_highlight
        term = token[1:-1] if token.startswith('"') and token.endswith('"') and len(token) >= 2 else token
        term = normalize_fragment(term)
        morphemes = searchable_morphemes(term)
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            # FTS 側は候補を広めに取る。terms カラムは表記ゆれ用トークンも含むため、
            # 厳密なフレーズ判定は PHP 側で本文そのものに対して行う。
            clause = build_term_query_from_morphemes(term, morphemes)
            highlight_candidates = [term]
            if not is_negated:
                append_unique(exact_phrases, [term])
        else:
            clause = build_term_query_from_morphemes(term, morphemes)
            highlight_candidates = surface_terms_from_morphemes(morphemes)
        if clause:
            parts.append(clause)
        if not is_negated:
            append_unique(highlight_terms, highlight_candidates)
        skip_highlight = False

    return {
        "fts_query": " ".join(parts),
        "surface_terms": highlight_terms,
        "exact_phrases": exact_phrases if exact_phrases_supported else [],
    }


def build_query(text: str) -> str:
    return str(build_query_payload(text)["fts_query"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SudachiPy ベースの全文検索トークナイザ")
    parser.add_argument("--mode", choices=["document", "query", "fields"], required=True)
    parser.add_argument("--text", default=None, help="未指定時は stdin から読み取る")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    text = args.text if args.text is not None else read_stdin_text()

    if args.mode == "document":
        payload = {
            "terms_text": document_terms_text(text),
            "surface_terms": surface_terms(text),
        }
    elif args.mode == "fields":
        decoded = json.loads(text or "{}")
        if not isinstance(decoded, dict):
            raise ValueError("fields mode expects a JSON object")
        payload = document_terms_map({str(key): str(value) for key, value in decoded.items()})
    else:
        payload = build_query_payload(text)

    if args.json:
        print(json.dumps(payload, ensure_ascii=True))
    elif args.mode == "document":
        print(payload["terms_text"])
    else:
        print(payload["fts_query"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
