"""PDFium の文字領域、OOXML のセル、展開しない ZIP 読み取り。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from functools import cached_property
from io import BytesIO
from pathlib import Path, PurePosixPath
import posixpath
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

MAX_MEMBER_BYTES = 120 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 10000
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def normalize(text: str) -> str:
    """照合だけに用いる。保存する原文は変換しない。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def normalized_match(text: str, pattern: str):
    """空白を除いた検索結果を元の文字列の範囲に戻す（原文を生成しない）。"""
    normalized, offsets = [], []
    for i, char in enumerate(text):
        for c in unicodedata.normalize("NFKC", char):
            if not c.isspace():
                normalized.append(c)
                offsets.append(i)
    match = re.search(unicodedata.normalize("NFKC",pattern), "".join(normalized))
    if not match:
        return None
    group = 1 if match.lastindex else 0
    start, end = match.span(group)
    if start == end:
        return ""
    return text[offsets[start]:offsets[end - 1] + 1]


class ReadError(ValueError):
    pass


@dataclass
class Source:
    doc_id: str
    data: bytes
    file_type: str
    metadata: dict
    parent_doc_id: str | None = None
    member: str | None = None

    @cached_property
    def digest(self):
        return sha256(self.data).hexdigest()


def safe_zip(data: bytes):
    archive = zipfile.ZipFile(BytesIO(data))
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS or sum(x.file_size for x in infos) > MAX_ARCHIVE_BYTES:
        archive.close()
        raise ReadError("archive_limit")
    if any(x.file_size > MAX_MEMBER_BYTES for x in infos):
        archive.close()
        raise ReadError("archive_member_limit")
    return archive


def member_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp932")
    except (UnicodeError, LookupError):
        return info.filename


def archive_sources(source: Source):
    """メンバーをディスクへ展開しない。子 ID と SHA は結果の文書台帳に残す。"""
    with safe_zip(source.data) as archive:
        for info in sorted(archive.infolist(), key=lambda x: x.filename):
            if info.is_dir():
                continue
            name = member_name(info)
            parts = PurePosixPath(name.replace("\\", "/")).parts
            if ".." in parts or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
                raise ReadError("unsafe_archive_member")
            suffix = PurePosixPath(name).suffix.lower().lstrip(".")
            if suffix not in {"pdf", "xlsx", "xlsm", "xls", "zip", "docx", "doc"}:
                continue
            child_id = source.doc_id + "--" + sha256(info.filename.encode("utf-8")).hexdigest()[:16]
            yield Source(child_id, archive.read(info), suffix, source.metadata,
                         parent_doc_id=source.doc_id, member=name)


class PDFPage:
    """rect は左上原点・頁に対する比率 [left, top, right, bottom]。"""
    def __init__(self, page, number):
        self.page = page
        self.number = number
        self.width, self.height = page.get_size()
        self.textpage = page.get_textpage()
        self.text = self.textpage.get_text_bounded()
        self._mark_image = None
        self._glyphs = None

    def region(self, rect=None):
        if rect is None:
            return self.text.strip()
        l, t, r, b = rect
        selected=[g for g in self.glyphs() if l <= (g[1]+g[3])/2 <= r and t <= (g[2]+g[4])/2 <= b]
        lines=self._lines(selected)
        rendered=[]
        for _,gs in lines:
            gs.sort(key=lambda g:g[1])
            parts=[];previous=None
            for g in gs:
                if previous and g[1]-previous[3] > max(g[3]-g[1],previous[3]-previous[1])*.7:
                    parts.append(" ")
                parts.append(g[0]);previous=g
            rendered.append("".join(parts))
        return "\n".join(rendered).strip()

    def locator(self, rect=None):
        result = {"type": "pdf", "page": self.number}
        if rect is not None:
            l, t, r, b = rect
            result["bbox"] = [round(l * self.width, 3), round(t * self.height, 3),
                              round(r * self.width, 3), round(b * self.height, 3)]
        return result

    def glyphs(self):
        """表の行・見出しの位置検出用。PDFium の文字番号を保持する。"""
        if self._glyphs is not None:return self._glyphs
        import pypdfium2.raw as raw
        import ctypes
        result = []
        for i in range(self.textpage.count_chars()):
            codepoint=raw.FPDFText_GetUnicode(self.textpage,i)
            if not codepoint:continue
            char = chr(codepoint)
            if not char.strip():
                continue
            l, b, r, t = self.textpage.get_charbox(i)
            ox,oy=ctypes.c_double(),ctypes.c_double()
            if not raw.FPDFText_GetCharOrigin(self.textpage,i,ox,oy):
                raise ReadError("character_origin_missing")
            result.append((char, l/self.width, 1-t/self.height, r/self.width, 1-b/self.height, i,
                           1-oy.value/self.height))
        self._glyphs = result
        return result

    @staticmethod
    def _lines(glyphs):
        lines=[]
        # 小書き仮名・句読点・英小文字は字形の中心が異なる。文字の原点（基線）で組む。
        for g in sorted(glyphs,key=lambda g:(g[6],g[1])):
            cy=g[6]
            line=next((x for x in lines[-3:] if abs(x[0]-cy)<.0015),None)
            if line is None:lines.append([cy,[g]])
            else:line[1].append(g)
        return lines

    def anchor_rects(self, pattern, region=None):
        """文字位置順に行を組み、指定範囲内の見出しを探す。"""
        glyphs = self.glyphs()
        if region:
            l,t,r,b = region
            glyphs = [g for g in glyphs if l <= (g[1]+g[3])/2 <= r and t <= (g[2]+g[4])/2 <= b]
        lines = self._lines(glyphs)
        result = []
        for _, gs in lines:
            gs.sort(key=lambda g:g[1])
            chars, refs = [], []
            for g in gs:
                for c in normalize(g[0]):
                    chars.append(c); refs.append(g)
            for m in re.finditer(unicodedata.normalize("NFKC",pattern), "".join(chars)):
                hit = refs[m.start():m.end()]
                if hit:
                    result.append([min(g[1] for g in hit),min(g[2] for g in hit),
                                   max(g[3] for g in hit),max(g[4] for g in hit)])
        return result

    def render(self, path, scale=1.5):
        bitmap = self.page.render(scale=scale)
        try:
            bitmap.to_pil().save(path)
        finally:
            bitmap.close()

    def mark_fraction(self, rect):
        """既知のチェック枠の内側だけを測る。文字認識や欄外の画像解析はしない。"""
        if self._mark_image is None:
            bitmap=self.page.render(scale=2)
            try:self._mark_image=bitmap.to_pil().convert("L").copy()
            finally:bitmap.close()
        w,h=self._mark_image.size
        l,t,r,b=rect
        crop=self._mark_image.crop((round(l*w),round(t*h),round(r*w),round(b*h)))
        if crop.width<3 or crop.height<3:raise ReadError("checkbox_too_small")
        histogram=crop.histogram()
        return sum(histogram[:90])/(crop.width*crop.height)

    def horizontal_rules(self, left, right, top=0, bottom=1):
        """表の横罫線（PDF の線分）。位置を推定せず、指定した列を横切る線だけ。"""
        import ctypes
        import pypdfium2.raw as raw
        segments = []
        for obj in self.page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
            # 入れ子の変換行列の合成は未実装なので採用しない。
            if obj.level:
                continue
            fill,stroke=ctypes.c_int(),ctypes.c_int()
            if not raw.FPDFPath_GetDrawMode(obj,fill,stroke) or not (stroke.value or fill.value):
                continue
            matrix = obj.get_matrix()
            previous = None
            path_segments=[];points=[]
            def append_subpath():
                # 複数の細い罫線を1個のPDFパスにまとめる作成ソフトにも対応する。
                if stroke.value or (points and max(y for _,y in points)-min(y for _,y in points)<=1.5):
                    segments.extend(path_segments)
            for i in range(raw.FPDFPath_CountSegments(obj)):
                seg = raw.FPDFPath_GetPathSegment(obj, i)
                x,y = ctypes.c_float(),ctypes.c_float()
                if not raw.FPDFPathSegment_GetPoint(seg, x,y):
                    previous = None
                    continue
                current = matrix.on_point(x.value,y.value)
                if raw.FPDFPathSegment_GetType(seg)==raw.FPDF_SEGMENT_MOVETO:
                    append_subpath();path_segments=[];points=[];previous=None
                points.append(current)
                if raw.FPDFPathSegment_GetType(seg) == raw.FPDF_SEGMENT_LINETO and previous:
                    x1,y1 = previous; x2,y2 = current
                    yy=1-(y1+y2)/2/self.height
                    if abs(y2-y1) <= 0.8 and min(x1,x2)/self.width < right and max(x1,x2)/self.width > left and top <= yy <= bottom:
                        path_segments.append((yy,min(x1,x2)/self.width,max(x1,x2)/self.width))
                previous = current
            # Excel の PDF は罫線を細い塗り矩形で描く場合がある。
            # セル背景の矩形の縁を罫線と取り違えない。
            append_subpath()
        groups=[]
        for y,l,r in sorted(segments):
            if not groups or y-groups[-1][0]>.001:
                groups.append([y,[(l,r)]])
            else:groups[-1][1].append((l,r))
        result=[]
        for y,intervals in groups:
            covered=left
            for l,r in sorted(intervals):
                if l>covered+.002:break
                covered=max(covered,r)
            if covered>=right-.002 and (not result or y-result[-1]>.002):result.append(y)
        return result

    def close(self):
        if self._mark_image is not None:self._mark_image.close()
        self.textpage.close()
        self.page.close()


@contextmanager
def pdf_document(data):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        yield doc
    finally:
        doc.close()


def column_number(letters):
    n = 0
    for c in letters:
        n = n * 26 + ord(c) - 64
    return n


def cell_key(ref):
    m = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", ref)
    if not m:
        raise ReadError("invalid_cell_reference")
    return int(m[2]), column_number(m[1])


@dataclass
class Sheet:
    name: str
    cells: dict
    formulas_without_cache: set
    merges: list
    hidden: bool = False

    def region(self, cells):
        ends = cells.split(":")
        a, b = cell_key(ends[0]), cell_key(ends[-1])
        return "\n".join(v for k,v in sorted(self.cells.items(), key=lambda x:cell_key(x[0]))
                         if a[0] <= cell_key(k)[0] <= b[0] and a[1] <= cell_key(k)[1] <= b[1] and v)


def read_xlsx(data):
    """保存済みの値だけを読む。数式を再計算せず、書式から値を推測しない。"""
    with safe_zip(data) as archive:
        names = set(archive.namelist())
        def xml(path):
            raw = archive.read(path)
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ReadError("xml_entity_forbidden")
            return ET.fromstring(raw)
        def visible_text(node):
            # rPh は表示本文ではなく、Excel のふりがな情報。
            if node.tag in {NS+"rPh", NS+"phoneticPr"}:return ""
            if node.tag==NS+"t":return node.text or ""
            return "".join(visible_text(child) for child in node)
        strings = []
        if "xl/sharedStrings.xml" in names:
            for si in xml("xl/sharedStrings.xml").findall(NS+"si"):
                strings.append(visible_text(si))
        targets = {x.get("Id"):x.get("Target") for x in xml("xl/_rels/workbook.xml.rels")
                   if x.get("TargetMode") != "External"}
        result = []
        for item in xml("xl/workbook.xml").iter(NS+"sheet"):
            target = targets.get(item.get(REL+"id"))
            if not target:
                raise ReadError("missing_sheet_relationship")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/"+target)
            if not path.startswith("xl/") or ".." in PurePosixPath(path).parts:
                raise ReadError("invalid_sheet_target")
            root = xml(path); cells = {}; missing = set()
            for cell in root.iter(NS+"c"):
                ref = cell.get("r"); cell_key(ref)
                value_node = cell.find(NS+"v")
                value = value_node.text or "" if value_node is not None else ""
                if cell.get("t") == "s":
                    try:
                        value = strings[int(value)]
                    except (ValueError, IndexError):
                        raise ReadError("invalid_shared_string") from None
                elif cell.get("t") == "inlineStr":
                    value = visible_text(cell)
                if cell.find(NS+"f") is not None and (value_node is None or value_node.text is None):
                    missing.add(ref)
                cells[ref] = value
            result.append(Sheet(item.get("name"),cells,missing,
                                [x.get("ref") for x in root.iter(NS+"mergeCell")],
                                item.get("state", "visible") != "visible"))
        return result
