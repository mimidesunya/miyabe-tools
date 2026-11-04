"""HTML で公開された評価シートを、PDF の頁と同じ升目の形（grid.page_layout）にする。

三郷市や貝塚市は事業ごとのシートを HTML の表で公開している。表の rowspan と colspan から行と列の番号を決め、
表の外の文字は段落ごとの行にする。1 文書を 1 頁として扱う。様式ごとの読み取り（tools/hyoka/forms）が
PDF と同じ道具で読めるようにするため。標準ライブラリだけで動く（升目づくりの容器には PyMuPDF しか無い）。
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)
BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "section", "article", "header", "footer", "caption"}
SKIP = {"script", "style", "noscript", "template", "head", "nav"}


def decode(raw: bytes) -> str:
    declared = CHARSET_RE.search(raw[:4096])
    names = [declared.group(1).decode("ascii", "ignore")] if declared else []
    for name in [*names, "utf-8", "cp932", "euc_jp"]:
        try:
            return raw.decode({"shift_jis": "cp932", "x-sjis": "cp932"}.get(name.lower(), name))
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def tidy(text: str) -> str:
    return re.sub(r"[ \t　\xa0]+", " ", html.unescape(text)).strip()


class _Table:
    def __init__(self, order: int) -> None:
        self.order = order
        self.rows: list[list[dict]] = []

    def layout(self) -> dict:
        """rowspan と colspan を升目の番号に置く（埋まった升目を飛ばして次の列へ）。"""
        taken: set[tuple[int, int]] = set()
        cells = []
        width = 0
        for r, row in enumerate(self.rows):
            c = 0
            for cell in row:
                while (r, c) in taken:
                    c += 1
                rs, cs = cell["rs"], cell["cs"]
                for dr in range(rs):
                    for dc in range(cs):
                        taken.add((r + dr, c + dc))
                lines = [line for line in (tidy(part) for part in cell["parts"]) if line]
                entry = {"r": r, "c": c, "rs": rs, "cs": cs, "text": " ".join(lines)}
                if len(lines) > 1:
                    entry["lines"] = lines
                cells.append(entry)
                c += cs
                width = max(width, c)
        return {"top": self.order, "rows": len(self.rows), "cols": width, "widths": [], "cells": cells}


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tables: list[_Table] = []
        self.stack: list[_Table] = []  # 入れ子の表は別の表にする
        self.cell: list[dict | None] = []
        self.lines: list[str] = []
        self.outside: list[str] = [""]
        self.skip = 0
        self.title = ""
        self.in_title = False

    def _text_target(self) -> list[str]:
        cell = self.cell[-1] if self.cell else None
        return cell["parts"] if cell is not None else self.outside

    def _break(self) -> None:
        target = self._text_target()
        if target and target[-1].strip():
            target.append("")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        if tag in SKIP:
            self.skip += 1
            return
        if tag == "title":
            self.in_title = True
        if self.skip:
            return
        if tag == "table":
            table = _Table(len(self.tables))
            self.tables.append(table)
            self.stack.append(table)
            self.cell.append(None)
        elif tag == "tr" and self.stack:
            self.stack[-1].rows.append([])
            self.cell[-1] = None
        elif tag in ("td", "th") and self.stack:
            if not self.stack[-1].rows:
                self.stack[-1].rows.append([])

            def span(name: str) -> int:
                try:
                    return max(1, min(int(str(attributes.get(name) or "1").strip() or 1), 200))
                except ValueError:
                    return 1

            cell = {"rs": span("rowspan"), "cs": span("colspan"), "parts": [""]}
            self.stack[-1].rows[-1].append(cell)
            self.cell[-1] = cell
        elif tag == "br" or tag in BLOCKS:
            self._break()
        elif tag == "img" and attributes.get("alt"):
            target = self._text_target()
            target[-1] += f" {attributes['alt']} "

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
        if self.skip:
            return
        if tag == "table" and self.stack:
            self.stack.pop()
            self.cell.pop()
            self._break()
        elif tag in ("td", "th") and self.cell:
            self.cell[-1] = None
        elif tag in BLOCKS:
            self._break()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if self.skip:
            return
        target = self._text_target()
        target[-1] += data

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")


def html_layout(raw: bytes) -> dict:
    """1 文書の HTML を、1 頁の升目の形にする。"""
    parser = _Parser()
    parser.feed(decode(raw))
    parser.close()
    tables = [table.layout() for table in parser.tables if any(table.rows)]
    tables = [table for table in tables if any(cell["text"] for cell in table["cells"])]
    lines = [tidy(line) for line in parser.outside]
    lines = [line for line in lines if line]
    return {"width": 0, "height": 0, "garbled": False, "html": True, "title": tidy(parser.title),
            "tables": tables, "lines": [{"y": min(99, i), "x": 0, "text": line} for i, line in enumerate(lines)], "vertical": []}
