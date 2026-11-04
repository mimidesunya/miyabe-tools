"""様式ごとの読み取りプログラム（readers/）が使う、頁の升目の道具。

升目は `tools/hyoka/ai/grid.py` の `page_layout` が作る（`<out>/<slug>/layouts/<doc_id>.json.gz`、1 行目が目次で、
そのあと 1 頁 1 行）。番地は表の中が `p{頁}t{表}r{行}c{列}`、表の外の行が `p{頁}l{番号}`、縦書きが `p{頁}v{番号}`。
形 2.0 の AI の書き写し（`tools/hyoka/format2/extract.py`）と同じ番地なので、採点も同じ道具でできる。

表の番号は 1 から、行と列は 0 から数える（`page_layout` のまま）。
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

MARKS = "☑■●◉◎✔✓"
EMPTY_MARKS = "☐□○"
DASHES = {"-", "－", "―", "‐", "−", "ー", "—", "‒", "─"}
ERAS = {"R": 2018, "令和": 2018, "H": 1988, "平成": 1988, "S": 1925, "昭和": 1925}
YEAR_RE = re.compile(r"(令和|平成|昭和|[RHS])\s*(元|\d{1,2})\s*(?:年度|年)?|((?:19|20)\d{2})\s*(?:年度|年)?")


def norm(text: object) -> str:
    """比べるための形。全角半角と空白の違いを消す。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


@dataclass(frozen=True)
class Cell:
    page: int
    table: int
    r: int
    c: int
    rs: int
    cs: int
    text: str
    # 升目に複数の行があるときの行ごとの文字。点線で区切った複数の指標が 1 つの升目に入る様式で使う
    lines: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"p{self.page}t{self.table}r{self.r}c{self.c}"

    def line_values(self) -> list[dict]:
        """行ごとの値の組。番地は升目のまま（原文は升目の文字の一部になる）。"""
        return [{"raw": line, "ref": self.ref} for line in (self.lines or (self.text,)) if line]

    def value(self) -> dict:
        """値の組（原文と番地）。読み取りの結果にはこの形で入れる。"""
        return {"raw": self.text, "ref": self.ref}


@dataclass(frozen=True)
class Line:
    page: int
    index: int
    y: int
    x: int
    text: str

    @property
    def ref(self) -> str:
        return f"p{self.page}l{self.index}"

    def value(self) -> dict:
        return {"raw": self.text, "ref": self.ref}


def matches(text: str, pattern: str | re.Pattern) -> bool:
    """文字列なら空白を除いた部分一致、正規表現なら search。"""
    if isinstance(pattern, re.Pattern):
        return bool(pattern.search(norm(text)))
    return norm(pattern) in norm(text)


@dataclass
class Page:
    number: int
    cells: list[Cell] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    vertical: list[str] = field(default_factory=list)
    readable: bool = True
    plain: str = ""

    def find(self, pattern: str | re.Pattern, *, table: int | None = None, after: Cell | None = None) -> Cell | None:
        """最初に当たる升目（表、行、列の順）。after を渡すとその升目より後ろから探す。"""
        found = self.find_all(pattern, table=table)
        if after is not None:
            found = [cell for cell in found if (cell.table, cell.r, cell.c) > (after.table, after.r, after.c)]
        return found[0] if found else None

    def find_all(self, pattern: str | re.Pattern, *, table: int | None = None) -> list[Cell]:
        return [cell for cell in self.cells if (table is None or cell.table == table) and cell.text and matches(cell.text, pattern)]

    def find_line(self, pattern: str | re.Pattern) -> Line | None:
        return next((line for line in self.lines if matches(line.text, pattern)), None)

    def table_cells(self, table: int) -> list[Cell]:
        return [cell for cell in self.cells if cell.table == table]

    def right(self, cell: Cell | None, n: int = 1) -> Cell | None:
        """右の升目。結合した升目は行の範囲が重なるものを選ぶ。n で何個先か。"""
        for _ in range(n):
            if cell is None:
                return None
            cell = next((x for x in self.table_cells(cell.table)
                         if x.c == cell.c + cell.cs and x.r <= cell.r < x.r + x.rs), None)
        return cell

    def left(self, cell: Cell | None, n: int = 1) -> Cell | None:
        for _ in range(n):
            if cell is None:
                return None
            cell = next((x for x in self.table_cells(cell.table)
                         if x.c + x.cs == cell.c and x.r <= cell.r < x.r + x.rs), None)
        return cell

    def below(self, cell: Cell | None, n: int = 1) -> Cell | None:
        """下の升目。列の範囲が重なるものを選ぶ。"""
        for _ in range(n):
            if cell is None:
                return None
            cell = next((x for x in self.table_cells(cell.table)
                         if x.r == cell.r + cell.rs and x.c <= cell.c < x.c + x.cs), None)
        return cell

    def above(self, cell: Cell | None, n: int = 1) -> Cell | None:
        for _ in range(n):
            if cell is None:
                return None
            cell = next((x for x in self.table_cells(cell.table)
                         if x.r + x.rs == cell.r and x.c <= cell.c < x.c + x.cs), None)
        return cell

    def row(self, cell: Cell) -> list[Cell]:
        """同じ行にかかる升目を左から。"""
        return sorted((x for x in self.table_cells(cell.table) if x.r <= cell.r < x.r + x.rs), key=lambda x: x.c)

    def column(self, cell: Cell) -> list[Cell]:
        """同じ列にかかる升目を上から。"""
        return sorted((x for x in self.table_cells(cell.table) if x.c <= cell.c < x.c + x.cs), key=lambda x: x.r)

    def text(self) -> str:
        """頁の全部の文字（頁の種類を見分けるため）。"""
        return "\n".join([cell.text for cell in self.cells] + [line.text for line in self.lines] + self.vertical + [self.plain])


@dataclass
class Document:
    slug: str
    doc_id: str
    pages: list[Page]

    def refs(self) -> dict[str, str]:
        """番地から文字を引く表。"""
        table = {cell.ref: cell.text for page in self.pages for cell in page.cells}
        table.update({line.ref: line.text for page in self.pages for line in page.lines})
        table.update({f"p{page.number}v{i}": text for page in self.pages for i, text in enumerate(page.vertical)})
        return table


def page_from_layout(layout: dict) -> Page:
    number = int(layout.get("page") or 0)
    page = Page(number=number, readable=not (layout.get("image_only") or layout.get("garbled")), plain=layout.get("plain") or "")
    for ti, table in enumerate(layout.get("tables") or [], 1):
        for cell in table.get("cells") or []:
            page.cells.append(Cell(number, ti, int(cell["r"]), int(cell["c"]), int(cell.get("rs") or 1), int(cell.get("cs") or 1),
                                   str(cell.get("text") or ""), tuple(str(line) for line in cell.get("lines") or ())))
    page.lines = [Line(number, i, int(line.get("y") or 0), int(line.get("x") or 0), str(line.get("text") or ""))
                  for i, line in enumerate(layout.get("lines") or [])]
    page.vertical = [str(text) for text in layout.get("vertical") or []]
    return page


def load_document(path: Path, slug: str = "") -> Document:
    """升目の置き場（layouts/<doc_id>.json.gz）から文書を読む。"""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    head, layouts = rows[0], rows[1:]
    return Document(slug=slug or path.parent.parent.name, doc_id=str(head.get("doc_id") or path.name.split(".")[0]),
                    pages=[page_from_layout(layout) for layout in layouts])


def number(raw: object) -> Decimal | None:
    """原文の数字。桁の区切り、全角、「▲」「△」の負の数を読む。数字でなければ None。"""
    text = norm(raw).replace(",", "").replace("円", "")
    negative = text[:1] in ("▲", "△", "-", "−") and len(text) > 1
    text = text[1:] if negative else text
    if not re.fullmatch(r"\d+(?:\.\d+)?\.?", text):
        return None
    try:
        value = Decimal(text.rstrip("."))
    except InvalidOperation:
        return None
    return -value if negative else value


def status(raw: object) -> str:
    """値の状態（形 2.0 の「値の状態」）。value、zero、dash、blank。"""
    text = norm(raw)
    if not text:
        return "blank"
    if text in DASHES or set(text) <= DASHES:
        return "dash"
    value = number(text)
    if value is not None:
        return "zero" if value == 0 else "value"
    return "value"


def marked(text: str) -> list[str]:
    """印の付いた選択肢（☑ や ■ の直後の語）。印の無い並びからは何も返さない。"""
    options = re.findall(rf"[{MARKS}]\s*([^{MARKS}{EMPTY_MARKS}\n]+)", text or "")
    return [option.strip() for option in options if option.strip()]


def western_year(text: object, era: str = "") -> int | None:
    """「R6年度」「令和6年度」「H28」「2024年度」を西暦にする。元号の無い「6年度」は era（R、H）で補う。"""
    value = norm(text)
    found = YEAR_RE.search(value)
    if found:
        if found.group(3):
            return int(found.group(3))
        number_text = found.group(2)
        return ERAS[found.group(1)] + (1 if number_text == "元" else int(number_text))
    bare = re.search(r"(元|\d{1,2})年度", value)
    if bare and era in ERAS:
        return ERAS[era] + (1 if bare.group(1) == "元" else int(bare.group(1)))
    return None
