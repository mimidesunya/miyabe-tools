#!/usr/bin/env python3
"""RTF の本文を文字列にする。

議会の文書を Word から RTF で書き出して公開する取得元がある（菊陽町は
会議結果を `…/kiji0033487/3_3487_9615_up_gsv5h51g.rtf` で配る）。RTF を
読めないと、汎用クロールはリンク先をページとして開こうとして失敗し、
「開けなかったページ」に数えて自治体ごとエラー停止にしていた。

依存を増やさないよう、本文を取り出すのに要る分だけを読む。

- `\\'xx` は文字コードの 1 バイト。日本語は `\\'82\\'a0` のように 2 つ続くので、
  続いたバイトをまとめてから戻す。文字コードは `\\ansicpg932` と、フォント表の
  `\\fcharset128`（Shift_JIS）から決める。
- `\\uN` は Unicode の 1 文字。後ろに `\\ucN` 個の代わりの文字が付くので読み飛ばす。
- フォント表・書式・埋め込み画像・ヘッダとフッタなど、本文でない「宛先」は捨てる。
"""

from __future__ import annotations

import codecs
import re

RTF_MAGIC = b"{\\rtf"

# 制御語・`\'xx`・制御記号・波かっこ・改行・地の文。
TOKEN_RE = re.compile(
    r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?"
    r"|\\'([0-9a-fA-F]{2})"
    r"|\\([^a-zA-Z])"
    r"|([{}])"
    r"|[\r\n]+"
    r"|([^\\{}\r\n]+)",
)

# 中身が本文ではない宛先。`{\*\…}` の形で書かれるものは別に一律で捨てる。
IGNORED_DESTINATIONS = frozenset(
    {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "objdata",
        "themedata", "colorschememapping", "datastore", "latentstyles", "listtable",
        "listoverridetable", "rsidtbl", "xmlnstbl", "mmathPr", "generator", "filetbl",
        "revtbl", "pgdsctbl", "fldinst", "pn", "header", "headerl", "headerr", "headerf",
        "footer", "footerl", "footerr", "footerf", "nonshppict", "template", "xe", "tc",
    }
)

# 文字として出す制御語。
CONTROL_TEXT = {
    "par": "\n",
    "line": "\n",
    "sect": "\n",
    "page": "\n",
    "row": "\n",
    "tab": "\t",
    "cell": "\t",
    "nestcell": "\t",
    "emdash": "\u2014",
    "endash": "\u2013",
    "emspace": " ",
    "enspace": " ",
    "qmspace": " ",
    "bullet": "\u2022",
    "lquote": "\u2018",
    "rquote": "\u2019",
    "ldblquote": "\u201c",
    "rdblquote": "\u201d",
}

# `\fcharsetN` から文字コードへ。0（ANSI）と 1（既定）は文書の `\ansicpg` に従う。
FONT_CHARSET_CODEPAGES = {
    77: "mac_roman",
    128: "cp932",
    129: "cp949",
    134: "gbk",
    136: "big5",
    161: "cp1253",
    162: "cp1254",
    177: "cp1255",
    178: "cp1256",
    186: "cp1257",
    204: "cp1251",
    222: "cp874",
    238: "cp1250",
}


def looks_like_rtf(raw: bytes) -> bool:
    """中身が RTF か。拡張子や Content-Type ではなく先頭で見る。"""
    return bytes(raw[:64]).lstrip(b"\xef\xbb\xbf \t\r\n")[: len(RTF_MAGIC)] == RTF_MAGIC


def _codec(name: str) -> str:
    try:
        return codecs.lookup(name).name
    except LookupError:
        return ""


class _Group:
    __slots__ = ("ignorable", "fonttbl", "ucskip", "font")

    def __init__(self) -> None:
        self.ignorable = False
        self.fonttbl = False
        self.ucskip = 1
        self.font: int | None = None

    def copy(self) -> "_Group":
        other = _Group()
        other.ignorable = self.ignorable
        other.fonttbl = self.fonttbl
        other.ucskip = self.ucskip
        other.font = self.font
        return other


class _RtfText:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pending = bytearray()
        self.pending_codepage = "cp1252"
        self.document_codepage = ""
        self.font_charsets: dict[int, int] = {}
        self.font_codepages: dict[int, str] = {}
        self.fonttbl_font: int | None = None
        self.default_font: int | None = None

    def base_codepage(self) -> str:
        if self.document_codepage:
            return self.document_codepage
        # `\ansicpg` を書かない古い書き出しでも、Shift_JIS のフォントがあれば日本語の文書。
        if 128 in self.font_charsets.values():
            return "cp932"
        return "cp1252"

    def codepage_for(self, font: int | None) -> str:
        if font is None:
            font = self.default_font
        if font is not None:
            explicit = self.font_codepages.get(font)
            if explicit:
                return explicit
            mapped = FONT_CHARSET_CODEPAGES.get(self.font_charsets.get(font, -1))
            if mapped:
                return mapped
        return self.base_codepage()

    def flush(self) -> None:
        if self.pending:
            self.parts.append(bytes(self.pending).decode(self.pending_codepage, errors="replace"))
            self.pending.clear()

    def emit_bytes(self, data: bytes, codepage: str) -> None:
        # 2 バイト文字が書式の切れ目（`}{`）をまたぐことがあるので、
        # 文字コードが変わるまでは区切らずにためる。
        if codepage != self.pending_codepage:
            self.flush()
            self.pending_codepage = codepage
        self.pending.extend(data)

    def emit_text(self, text: str) -> None:
        self.flush()
        self.parts.append(text)

    def font_table_word(self, word: str, arg: str | None) -> None:
        number = int(arg) if arg else 0
        if word == "f":
            self.fonttbl_font = number
        elif self.fonttbl_font is None:
            return
        elif word == "fcharset":
            self.font_charsets[self.fonttbl_font] = number
        elif word == "cpg":
            codec = _codec(f"cp{number}")
            if codec:
                self.font_codepages[self.fonttbl_font] = codec

    def run(self, data: str) -> str:
        group = _Group()
        stack: list[_Group] = []
        skip = 0
        pos = 0
        length = len(data)
        while pos < length:
            match = TOKEN_RE.match(data, pos)
            if match is None:
                # 末尾に孤立した `\` など。読み飛ばす。
                pos += 1
                continue
            pos = match.end()
            word, arg, hex_byte, symbol, brace, text = match.groups()
            if brace == "{":
                skip = 0
                stack.append(group)
                group = group.copy()
                continue
            if brace == "}":
                skip = 0
                if stack:
                    group = stack.pop()
                continue
            if word is not None:
                skip = 0
                if word == "bin":
                    # 生のバイナリが続く。中身は読まない。
                    pos += max(0, int(arg or 0))
                    continue
                if group.ignorable:
                    if group.fonttbl:
                        self.font_table_word(word, arg)
                    continue
                if word in IGNORED_DESTINATIONS:
                    group.ignorable = True
                    group.fonttbl = word == "fonttbl"
                    continue
                if word == "ansicpg":
                    self.document_codepage = _codec(f"cp{int(arg or 0)}")
                elif word == "mac":
                    self.document_codepage = "mac_roman"
                elif word in ("pc", "pca"):
                    self.document_codepage = "cp437" if word == "pc" else "cp850"
                elif word == "deff":
                    self.default_font = int(arg or 0)
                elif word == "f":
                    group.font = int(arg or 0)
                elif word == "uc":
                    group.ucskip = max(0, int(arg or 0))
                elif word == "u":
                    code = int(arg or 0)
                    if code < 0:
                        code += 0x10000
                    self.emit_text(chr(code))
                    # 続く代わりの文字（`\'82\'a0` や `?`）は読み飛ばす。
                    skip = group.ucskip
                elif word in CONTROL_TEXT:
                    self.emit_text(CONTROL_TEXT[word])
                continue
            if hex_byte is not None:
                if skip > 0:
                    skip -= 1
                elif not group.ignorable:
                    self.emit_bytes(bytes([int(hex_byte, 16)]), self.codepage_for(group.font))
                continue
            if symbol is not None:
                if symbol == "*":
                    group.ignorable = True
                elif skip > 0:
                    skip -= 1
                elif group.ignorable:
                    pass
                elif symbol in "\\{}":
                    # 2 バイト文字の 2 バイト目がこの 3 つになることがある。バイトとしてためる。
                    self.emit_bytes(symbol.encode("latin-1"), self.codepage_for(group.font))
                elif symbol == "~":
                    self.emit_text(" ")
                elif symbol == "_":
                    self.emit_text("-")
                elif symbol in "\r\n":
                    self.emit_text("\n")
                continue
            if text is not None:
                if skip > 0:
                    dropped = min(skip, len(text))
                    skip -= dropped
                    text = text[dropped:]
                if text and not group.ignorable:
                    # 書き出しによっては 8 ビットの文字をそのまま置く。latin-1 で読んだ
                    # 1 文字が元の 1 バイトなので、バイトに戻してためる。
                    self.emit_bytes(text.encode("latin-1"), self.codepage_for(group.font))
        self.flush()
        joined = "".join(self.parts)
        # BMP の外の文字は `\u` 2 つ（サロゲートの組）で書かれる。組にして戻す。
        return joined.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def normalize_rtf_text(value: str) -> str:
    """行末の空白と、続きすぎた空行を整える。字間は詰めない（表の区切りを残す）。"""
    text = value.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_rtf_text(raw: bytes) -> str:
    """RTF のバイト列から本文を取り出す。RTF でなければ空文字を返す。"""
    if not looks_like_rtf(raw):
        return ""
    return normalize_rtf_text(_RtfText().run(bytes(raw).decode("latin-1")))
