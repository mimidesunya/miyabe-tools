"""PDF の 1 頁を、AI に渡す「升目の一覧」にする（PyMuPDF）。

字を 1 字ずつ座標つきで集め、PyMuPDF の表の検出（罫線）で見つけた升目に振り分ける。

- 表は `lines_strict`（Excel 由来の塗りの縁を罫線と見ない）を使い、文字の取りこぼしが 1 割を超えるときだけ `lines`。
- 升目は「行,列[+行の幅x列の幅]: 文字」。表の外の文字は行ごとに「[上からの位置,左からの位置] 文字」（それぞれ 0〜99）。
- 図形・画像の印を字にする: 小さな塗りの丸 → ●、線だけの丸 → ○、小さな塗りの四角 → ■、線だけの四角 → □、
  文字を囲む楕円 → 囲まれた文字の前に ◯。同じ大きさの小さな画像が何種類かあり、1 つが少なく使われ黒い点が
  1 割以上多ければ、それを ☑、ほかを ☐（空欄の画像を使い回し、選んだ欄だけ別の画像の様式）。
- 印だけの升目（印が選択肢と別の升目に入る様式）は、同じ高さで右にある最も近い字の前へ付け直す。
- 升目の中の行は、右端まで届いていれば折り返しとみなして空白なしでつなぐ。
- 字の間の空白は位置から決める（均等割付の「事 業 名」は詰め、英数字の語の間は空ける）。
- 制御文字や意味のない英字ばかりの頁（字の対応表が壊れた PDF）は、文字を出さずにそのことだけを書く。
"""

from __future__ import annotations

import re
import statistics

import fitz

MARK_CHARS = set("●○◎■□☑☐✓✔◯◉")
SUSPECT = re.compile(r"[\u0000-\u001f -ɏɐ-˿Ͱ-Ͽ-�]")
CJK = re.compile(r"[぀-ヿ㐀-鿿＀-￯]")
ASCII_WORD = re.compile(r"[A-Za-z0-9]")
GARBLED_NOTE = "## この頁の文字層は文字化けしているため省略（画像や OCR で読む必要がある）"


class Glyph:
    __slots__ = ("x0", "y0", "x1", "y1", "c", "vertical", "pseudo")

    def __init__(self, bbox, c, vertical=False, pseudo=False):
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.c = c
        self.vertical = vertical
        self.pseudo = pseudo

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1.0)

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


def choose_tables(page):
    """`lines_strict` を使い、`lines` より文字の取りこぼしが 1 割を超えて多いときだけ `lines` にする。"""
    words = page.get_text("words")

    def coverage(tables):
        boxes = [t.bbox for t in tables]
        hit = 0
        for w in words:
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            if any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in boxes):
                hit += 1
        return hit / len(words) if words else 0

    strict = page.find_tables(strategy="lines_strict").tables
    big = [t for t in strict if t.row_count * t.col_count >= 4]
    strict_coverage = coverage(strict)
    if big and strict_coverage >= 0.9:
        return strict  # `lines` の取りこぼしは 0 未満にならないので、比べても結論は同じ（時間を半分にする）
    loose = page.find_tables(strategy="lines").tables
    if big and strict_coverage >= coverage(loose) - 0.10:
        return strict
    return loose


def spans(table) -> dict:
    """升目ごとの行の幅・列の幅（`table.rows` の None の並びから）。"""
    grid = [list(row.cells) for row in table.rows]
    out = {}
    for r, row in enumerate(grid):
        for c, bbox in enumerate(row):
            if bbox is None:
                continue
            cs = 1
            while c + cs < len(row) and row[c + cs] is None:
                cs += 1
            rs = 1
            while (r + rs < len(grid) and c < len(grid[r + rs]) and grid[r + rs][c] is None
                   and bbox[3] > (table.rows[r + rs].bbox[1] + table.rows[r + rs].bbox[3]) / 2):
                rs += 1
            out[(r, c)] = (rs, cs)
    return out


def text_glyphs(page) -> list[Glyph]:
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            vertical = abs(line["dir"][1]) > 0.9
            for span in line["spans"]:
                for ch in span["chars"]:
                    if not ch["c"].isspace():
                        out.append(Glyph(ch["bbox"], ch["c"], vertical))
    return out


def luminance(color) -> float:
    if not color:
        return 1.0
    if len(color) == 1:
        return color[0]
    r, g, b = color[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def drawing_marks(page, glyphs: list[Glyph]) -> list[Glyph]:
    """小さな図形を印の字に、文字を囲む楕円を ◯ にする。"""
    marks = []
    for d in page.get_drawings():
        rect = d["rect"]
        w, h = rect.width, rect.height
        kinds = {item[0] for item in d["items"]}
        filled_dark = d.get("fill") is not None and luminance(d.get("fill")) < 0.45
        stroke_only = d.get("fill") is None and d.get("color") is not None
        if 3 <= w <= 16 and 3 <= h <= 16 and 0.6 <= w / h <= 1.6:
            if "c" in kinds:
                if filled_dark:
                    marks.append(Glyph(tuple(rect), "●", pseudo=True))
                elif stroke_only:
                    marks.append(Glyph(tuple(rect), "○", pseudo=True))
            elif kinds <= {"re", "qu", "l"} and ("re" in kinds or "qu" in kinds):
                if filled_dark:
                    marks.append(Glyph(tuple(rect), "■", pseudo=True))
                elif stroke_only:
                    marks.append(Glyph(tuple(rect), "□", pseudo=True))
        elif "c" in kinds and d.get("fill") is None and 12 <= w <= 300 and 7 <= h <= 40 and w / max(h, 1) >= 1.2:
            inside = [g for g in glyphs if rect.x0 <= g.xc <= rect.x1 and rect.y0 <= g.yc <= rect.y1]
            if 0 < len(inside) <= 30:
                first = min(inside, key=lambda g: (round(g.yc), g.x0))
                marks.append(Glyph((first.x0 - 0.6, first.y0, first.x0 - 0.1, first.y1), "◯", pseudo=True))
    return marks


def image_ink(page, rect, xref) -> float:
    """画像そのものの暗い画素の割合（透明な画素は数えない）。埋め込みの番号が無ければ頁を描いて数える。"""
    doc = page.parent
    if xref:
        try:
            info = doc.extract_image(xref)
            base = fitz.Pixmap(doc, xref)
            if base.alpha:
                base = fitz.Pixmap(base, 0)
            gray = fitz.Pixmap(fitz.csGRAY, base) if base.n > 1 else base
            mask = fitz.Pixmap(doc, info["smask"]).samples if info.get("smask") else None
            samples = gray.samples
            if mask is not None and len(mask) != len(samples):
                mask = None
            dark = sum(1 for i, v in enumerate(samples) if v < 128 and (mask is None or mask[i] > 128))
            return dark / max(len(samples), 1)
        except Exception:
            pass
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect, colorspace=fitz.csGRAY)
    return sum(1 for v in pix.samples if v < 110) / max(len(pix.samples), 1)


def image_marks(page) -> list[Glyph]:
    seen = set()
    smalls = []
    for info in page.get_image_info(xrefs=True):
        rect = fitz.Rect(info["bbox"])
        key = (round(rect.x0, 1), round(rect.y0, 1), info.get("xref"))
        if key in seen:
            continue
        seen.add(key)
        if 4 <= rect.width <= 30 and 4 <= rect.height <= 30:
            smalls.append((rect, info.get("xref") or 0))
    groups: dict = {}
    for rect, xref in smalls:
        groups.setdefault((round(rect.width), round(rect.height)), []).append((rect, xref))
    marks = []
    for items in groups.values():
        if len(items) < 2:
            continue
        ink, uses = {}, {}
        for rect, xref in items:
            key = xref or (round(rect.x0), round(rect.y0))
            uses[key] = uses.get(key, 0) + 1
            if key not in ink:
                ink[key] = image_ink(page, rect, xref)
        if len(ink) < 2:
            continue
        best = max(ink, key=ink.get)
        rest = [k for k in ink if k != best]
        if ink[best] < max(ink[k] for k in rest) * 1.1 + 0.002 or uses[best] > max(uses[k] for k in rest):
            continue
        for rect, xref in items:
            key = xref or (round(rect.x0), round(rect.y0))
            marks.append(Glyph(tuple(rect), "☑" if key == best else "☐", pseudo=True))
    return marks


def join_line(glyphs: list[Glyph]) -> str:
    """1 行の字を左から並べ、位置から空白を決める。"""
    glyphs = sorted(glyphs, key=lambda g: g.x0)
    h = statistics.median(g.h for g in glyphs)
    out = []
    prev = None
    for g in glyphs:
        if prev is not None:
            gap = g.x0 - prev.x1
            both_ascii = ASCII_WORD.match(prev.c) and ASCII_WORD.match(g.c)
            if gap > 1.5 * h or (both_ascii and gap > 0.2 * h):
                out.append(" ")
        out.append(g.c)
        prev = g
    return "".join(out)


def group_lines(glyphs: list[Glyph]):
    """横書きは高さの重なりで行に、縦書きの字は列にまとめる（右の列から、上から下へ）。"""
    lines: list[list[Glyph]] = []
    for g in sorted((g for g in glyphs if not g.vertical), key=lambda g: (g.yc, g.x0)):
        for line in lines:
            ly0 = min(x.y0 for x in line)
            ly1 = max(x.y1 for x in line)
            if min(ly1, g.y1) - max(ly0, g.y0) > 0.5 * min(ly1 - ly0, g.h):
                line.append(g)
                break
        else:
            lines.append([g])
    lines.sort(key=lambda line: (min(x.yc for x in line), min(x.x0 for x in line)))
    cols: list[list[Glyph]] = []
    for g in sorted((g for g in glyphs if g.vertical), key=lambda g: (-g.xc, g.y0)):
        for col in cols:
            if abs(statistics.mean(x.xc for x in col) - g.xc) < 0.5 * g.h:
                col.append(g)
                break
        else:
            cols.append([g])
    vtexts = ["".join(x.c for x in sorted(col, key=lambda x: x.y0)) for col in cols]
    return lines, vtexts


def cell_text(glyphs: list[Glyph], right_edge: float) -> str:
    lines, vtexts = group_lines(glyphs)
    parts: list[str] = []
    prev = None
    for line in lines:
        text = join_line(line)
        if prev is not None:
            h = statistics.median(g.h for g in prev)
            wrapped = max(g.x1 for g in prev) >= right_edge - 1.6 * h
            last, first = parts[-1][-1:], text[:1]
            parts.append("" if wrapped and not (ASCII_WORD.match(last) and ASCII_WORD.match(first)) else " ")
        parts.append(text)
        prev = line
    text = "".join(parts)
    if vtexts:
        text = (" ".join(vtexts) + (" " + text if text else "")).strip()
    return text


def garbled(glyphs: list[Glyph]) -> bool:
    chars = "".join(g.c for g in glyphs if not g.pseudo)
    if len(chars) < 50:
        return False
    return len(SUSPECT.findall(chars)) / len(chars) > 0.15 and len(CJK.findall(chars)) / len(chars) < 0.3


def build_cells(page) -> str:
    """頁の升目の一覧。"""
    glyphs = text_glyphs(page)
    if garbled(glyphs):
        return GARBLED_NOTE
    glyphs += drawing_marks(page, glyphs)
    glyphs += image_marks(page)
    cells = []  # (表の番号, 行, 列, 枠, 行の幅, 列の幅)
    for ti, table in enumerate(choose_tables(page)):
        sp = spans(table)
        for ri, row in enumerate(table.rows):
            for ci, bbox in enumerate(row.cells):
                if bbox is None:
                    continue
                rs, cs = sp.get((ri, ci), (1, 1))
                rect = fitz.Rect(bbox)
                if rs > 1 and ri + rs - 1 < len(table.rows):  # 結合した升目は下の行まで広げる
                    rect.y1 = max(rect.y1, table.rows[ri + rs - 1].bbox[3])
                cells.append((ti, ri, ci, rect, rs, cs))

    def locate(g):
        best = None
        for idx, (_ti, _r, _c, rect, _rs, _cs) in enumerate(cells):
            if rect.x0 - 0.5 <= g.xc <= rect.x1 + 0.5 and rect.y0 - 0.5 <= g.yc <= rect.y1 + 0.5:
                area = rect.width * rect.height
                if best is None or area < best[1]:
                    best = (idx, area)
        return best[0] if best else None

    owner = {id(g): locate(g) for g in glyphs}
    members: dict = {}
    for g in glyphs:
        members.setdefault(owner[id(g)], []).append(g)
    # 印だけの升目の印を、同じ高さで右にある最も近い字の前へ付け直す
    for idx, group in list(members.items()):
        if idx is None or not group or not all(g.c in MARK_CHARS for g in group):
            continue
        for g in list(group):
            right = [t for t in glyphs if t.c not in MARK_CHARS and t.x0 >= g.x0
                     and min(t.y1, g.y1 + 2) - max(t.y0, g.y0 - 2) > 0 and t.x0 - g.x1 < 80]
            if not right:
                continue
            target = min(right, key=lambda t: t.x0)
            dest = owner[id(target)]
            g.x0, g.x1 = target.x0 - 0.02, target.x0 - 0.01
            g.y0, g.y1 = target.y0, target.y1
            members[idx].remove(g)
            members.setdefault(dest, []).append(g)
            owner[id(g)] = dest
    parts = []
    current = None
    for idx, (ti, ri, ci, rect, rs, cs) in enumerate(cells):
        group = members.get(idx)
        if not group:
            continue
        if ti != current:
            parts.append(f"## table {ti + 1}")
            current = ti
        tag = f"{ri},{ci}" + (f"+{rs}x{cs}" if (rs, cs) != (1, 1) else "")
        parts.append(f"{tag}: {cell_text(group, rect.x1)}")
    outside = members.get(None) or []
    if outside:
        parts.append("## outside tables")
        lines, vtexts = group_lines(outside)
        width, height = page.rect.width or 1, page.rect.height or 1
        for line in lines:
            line = sorted(line, key=lambda g: g.x0)
            h = statistics.median(g.h for g in line)
            seg = [line[0]]
            segs = []
            for g in line[1:]:
                if g.x0 - seg[-1].x1 > 2.5 * h:  # 大きく離れた字は別の塊
                    segs.append(seg)
                    seg = [g]
                else:
                    seg.append(g)
            segs.append(seg)
            for seg in segs:
                y = min(99, max(0, int(min(g.y0 for g in seg) / height * 100)))
                x = min(99, max(0, int(min(g.x0 for g in seg) / width * 100)))
                parts.append(f"[{y:02d},{x:02d}] {join_line(seg)}")
        for vt in vtexts:
            parts.append(f"[縦] {vt}")
    return "\n".join(parts)
