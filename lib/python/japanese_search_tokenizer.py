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
# NEAR は語として丸ごと一致したときだけ演算子にする。NEARBY のような語を捨てない。
NEAR_OPERATOR_PATTERN = re.compile(r"NEAR(?:/\d+)?", re.IGNORECASE)
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


def searchable_morphemes(text: str):
    return [morpheme for morpheme in tokenize_text(text) if morpheme_is_searchable(morpheme)]


def surface_terms_from_morphemes(morphemes, *, unique: bool = True) -> list[str]:
    items: list[str] = []
    for morpheme in morphemes:
        surface = normalize_fragment(morpheme.surface())
        if surface:
            items.append(surface)
    return unique_preserve(items) if unique else items


def append_unique(items: list[str], values: list[str]) -> None:
    seen = set(items)
    for value in values:
        if value == "" or value in seen:
            continue
        seen.add(value)
        items.append(value)


def term_query_unit(token: str) -> str:
    """語 1 つを、語単位の項目（title_terms / body_terms）向けの simple_query_string の 1 単位にする。

    Sudachi で割れる語は括弧でまとめた AND にする（素の検索の multi_match と同じ当たり方）。
    `盛土|土砂` のように | でつないだ語は、それぞれをまとめた OR にする。
    """
    groups: list[str] = []
    for alternative in token.split("|"):
        surfaces = surface_terms_from_morphemes(searchable_morphemes(alternative))
        if not surfaces:
            continue
        groups.append(surfaces[0] if len(surfaces) == 1 else "(" + " ".join(surfaces) + ")")
    if not groups:
        return ""
    return groups[0] if len(groups) == 1 else "(" + " | ".join(groups) + ")"


def build_term_query(tokens: list[str]) -> str:
    """演算子（OR・NOT・括弧・| ・頭の -）を保ったまま、語を Sudachi の語に置き換えた問い合わせを作る。

    演算子つきの検索では、語を全部 AND で並べる multi_match を使えない（NOT で外した文書が戻る）。
    かといって語単位の句を外すと、複合語の当たり方が素の検索と大きく変わる（「同和団体」で 1,490 件と 42 件）。
    そこで語単位の句にも同じ演算子を効かせる。引用符の語句は語単位の項目では位置を表せないので、
    語句があるときは空を返し、文字単位の句だけで探させる。
    """
    parts: list[str] = []
    negate = False
    for token in tokens:
        if token == "(":
            parts.append("-(" if negate else "(")
            negate = False
            continue
        if token == ")":
            parts.append(")")
            continue
        upper = token.upper()
        if upper == "AND" or NEAR_OPERATOR_PATTERN.fullmatch(upper):
            continue
        if upper == "OR":
            parts.append("|")
            negate = False
            continue
        if upper == "NOT":
            negate = True
            continue
        if token.startswith('"'):
            return ""
        body = token
        while body[:1] in {"+", "-"}:
            if body[:1] == "-":
                negate = True
            body = body[1:]
        if body == "":
            continue
        unit = term_query_unit(body)
        if unit == "":
            if body.strip("|") == "":
                parts.append("|")
            negate = False
            continue
        parts.append(("-" if negate else "") + unit)
        negate = False
    return " ".join(parts)


def build_query_payload(text: str) -> dict[str, object]:
    highlight_terms: list[str] = []
    exact_phrases: list[str] = []
    exact_phrases_supported = True
    skip_highlight = False
    tokens = QUERY_TOKEN_PATTERN.findall(text or "")
    for token in tokens:
        if token in {"", "(", ")"}:
            continue

        upper = token.upper()
        is_near = NEAR_OPERATOR_PATTERN.fullmatch(upper) is not None
        if upper in {"AND", "OR", "NOT"} or is_near:
            if upper == "OR" or is_near:
                exact_phrases_supported = False
            if upper == "NOT":
                skip_highlight = True
            continue

        # 頭の - は simple_query_string の除外。外す語は光らせない。`-"語句"` は - と語句が別の塊になる。
        if token.startswith("-"):
            skip_highlight = True
            token = token.lstrip("-")
            if token == "":
                continue
        is_negated = skip_highlight
        quoted = token.startswith('"') and token.endswith('"') and len(token) >= 2
        term = normalize_fragment(token[1:-1] if quoted else token)
        if quoted:
            # 厳密なフレーズ判定は PHP 側で本文そのものに対して行う。
            highlight_candidates = [term]
            if not is_negated:
                append_unique(exact_phrases, [term])
        else:
            highlight_candidates = surface_terms_from_morphemes(searchable_morphemes(term))
        if not is_negated:
            append_unique(highlight_terms, highlight_candidates)
        skip_highlight = False

    return {
        "surface_terms": highlight_terms,
        "exact_phrases": exact_phrases if exact_phrases_supported else [],
        "term_query": build_term_query(tokens),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SudachiPy ベースの検索語トークナイザ")
    parser.add_argument("--mode", choices=["query"], required=True)
    parser.add_argument("--text", default=None, help="未指定時は stdin から読み取る")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    text = args.text if args.text is not None else read_stdin_text()
    payload = build_query_payload(text)
    if args.json:
        print(json.dumps(payload, ensure_ascii=True))
    else:
        print(" ".join(payload["surface_terms"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
