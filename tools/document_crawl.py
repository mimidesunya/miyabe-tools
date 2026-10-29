"""自治体のページに置かれた文書（PDF・Excel など）を辿って取る道具の共通部品。

包括外部監査の報告書（`tools/kansa/scrape_kansa_reports.py`）と事務事業評価
（`tools/hyoka/scrape_hyoka_docs.py`）は、どちらも共通のベンダ系システムが無く、
各自治体が自前のページに文書を置いている。台帳の入口ページから 1〜2 階層
降りて文書を探し、原典の URL と見つけたページを残して保存する、という形は
同じなので、制度に関係しない部分をここに置く。

- 取得: ホストごとの休止（`HostGuard`）、間隔を空ける取得器（`Fetcher`）、
  条件付き GET で文書を取り直す `fetch_document`
- ページ: リンクを直前の見出しと組にして返す `page_links`、同じ組織の判定
- 判定の部品: 会計年度の読み取り `fiscal_year`
- 台帳: 自治体マスタと台帳 TSV から取得対象を作る `load_targets`
- 保存: manifest の読み書き。中身が変わった文書は、上書きする前に前の版を
  `_archive/` へ移して残す（`archive_previous_version`。会議録・例規集の
  `_archive` と同じく、変更・削除の前の版をアーカイブとして蓄積する）
- 中身:取った応答の形式の見分け（`sniff_document_type`）、PDF・Excel（xlsx）・
  Word（docx）の本文抽出。Excel・Word は標準ライブラリの zipfile と XML だけで
  読む（取得イメージに openpyxl などは入っていない）。xls・doc の古い形式は読まない

どの文書を取るか（制度ごとの言葉の判定）は各ツールの側に置く。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import posixpath
import re
import threading
import time
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = WORKSPACE_ROOT / "data" / "municipalities"

USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be/)"
JST = timezone(timedelta(hours=9))

# 1 件ごとの間隔。取得元は自治体の本番サイト。
DEFAULT_DELAY_SECONDS = 1.5
# 一度取った文書を問い合わせ直すまでの日数。
DEFAULT_REFRESH_DAYS = 30
# 文字情報が無いと見なす本文の長さ。スキャンだけの PDF は OCR 待ちにする。
MIN_TEXT_CHARS = 200

# 同じホストで接続を続けて切られたら休む。
HOST_BLOCK_FILE = "host_blocks.json"
HOST_BLOCK_THRESHOLD = 5
HOST_BLOCK_SECONDS = 6 * 60 * 60


def now_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    except ValueError:
        return None


# ─────────────────────────────────────
# 年度
# ─────────────────────────────────────

# 年度。和暦は会計年度の始まる西暦に直す。
ERA_YEAR_RE = re.compile(r"(令和|平成)\s*([元\d０-９]{1,2})\s*年度?")
WESTERN_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})\s*年度")
ERA_BASE = {"令和": 2018, "平成": 1988}
FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def fiscal_year_source(*texts: str) -> tuple[int | None, str, int]:
    """文字列から会計年度を読む。(年, 読んだ文字, 何番目の文字列から読んだか)。

    見つからなければ (None, "", -1)。「年度」と書いたものを先に探す。
    「公表第1号（令和8年2月2日）」の「令和8年」は公表の日付で、監査の年度
    ではない（久留米市）。
    """
    for need_fiscal in (True, False):
        for index, text in enumerate(texts):
            value = str(text or "")
            for found in ERA_YEAR_RE.finditer(value):
                if need_fiscal and not found.group(0).endswith("年度"):
                    continue
                number = found.group(2).translate(FULLWIDTH)
                offset = 1 if number == "元" else int(number)
                return ERA_BASE[found.group(1)] + offset, f"{found.group(1)}{found.group(2)}年度", index
            found = WESTERN_YEAR_RE.search(value)
            if found:
                return int(found.group(1)), f"{found.group(1)}年度", index
    return None, "", -1


def fiscal_year(*texts: str) -> tuple[int | None, str]:
    """文字列から会計年度を読む。見つからなければ (None, "")。"""
    year, label, _index = fiscal_year_source(*texts)
    return year, label


# ─────────────────────────────────────
# ページ
# ─────────────────────────────────────

HEADING_RE = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.I | re.S)
ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)


def clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


@dataclass
class Link:
    url: str
    label: str
    heading: str


BASE_HREF_RE = re.compile(r"""<base\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", re.I)


def page_links(base_url: str, html: str) -> list[Link]:
    """ページのリンクを、直前の見出しと組にして返す。

    `<base href>` があれば、相対リンクはそれを基準に解く（京都市は
    `/sogo/page/…html` に `<base href=".../sogo/">` と `./cmsfiles/…` を置く。
    ページの URL を基準にすると `/sogo/page/cmsfiles/…` になり、全部 404 だった）。
    """
    declared = BASE_HREF_RE.search(html)
    if declared:
        base_url = urljoin(base_url, declared.group(1).strip().replace("&amp;", "&"))
    events: list[tuple[int, str, str, str]] = []
    for found in HEADING_RE.finditer(html):
        events.append((found.start(), "heading", clean_label(found.group(2)), ""))
    for found in ANCHOR_RE.finditer(html):
        events.append((found.start(), "anchor", found.group(2), found.group(1)))
    events.sort(key=lambda item: item[0])
    heading = ""
    links: list[Link] = []
    seen: set[str] = set()
    for _pos, kind, text, href in events:
        if kind == "heading":
            heading = text
            continue
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = urljoin(base_url, href.replace("&amp;", "&")).split("#", 1)[0]
        if urlsplit(target).scheme not in ("http", "https"):
            continue
        label = clean_label(text)
        key = f"{target}\t{label}"
        if key in seen:
            continue
        seen.add(key)
        links.append(Link(url=target, label=label, heading=heading))
    return links


def page_title(html: str) -> str:
    for pattern in (r"<h1\b[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        found = re.search(pattern, html, re.I | re.S)
        if found:
            return clean_label(found.group(1))[:160]
    return ""


def same_organization(host: str, other: str) -> bool:
    """同じ自治体のホストか。監査事務局などが別ホストに居ることがある。

    東京都は `www.metro.tokyo.lg.jp` に対して監査事務局が
    `kansa.metro.tokyo.lg.jp`、神奈川県・愛知県も同じ形。`www.` を外した
    ドメインを共有していれば同じ組織と見なす。外部のまったく別のサイトへは
    出ない（`.lg.jp` の別自治体はドメインが違うので混ざらない）。
    """
    left = host.lower().removeprefix("www.")
    right = other.lower().removeprefix("www.")
    if left == right:
        return True
    return left.endswith("." + right) or right.endswith("." + left)


# ─────────────────────────────────────
# 台帳
# ─────────────────────────────────────

def load_master(data_root: Path = DATA_ROOT) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with io.open(data_root / "municipality_master.tsv", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            if code:
                rows[code] = row
    return rows


def slug_for(code: str, master_row: dict[str, str]) -> str:
    romaji = str(master_row.get("name_romaji", "")).strip()
    return f"{code}-{romaji}" if romaji else code


@dataclass
class Target:
    code: str
    slug: str
    name: str
    url: str
    crawl_status: str


def load_targets(registry: Path, include_review: bool = False) -> list[Target]:
    """台帳 TSV（jis_code・url・crawl_status の列）から取得対象を作る。"""
    master = load_master()
    statuses = {"enabled", "review_required"} if include_review else {"enabled"}
    targets: list[Target] = []
    with io.open(registry, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            url = str(row.get("url", "")).strip()
            status = str(row.get("crawl_status", "")).strip()
            if not code or not url or status not in statuses:
                continue
            master_row = master.get(code, {})
            targets.append(Target(
                code=code,
                slug=slug_for(code, master_row),
                name=str(master_row.get("full_name") or master_row.get("name") or code),
                url=url,
                crawl_status=status,
            ))
    return targets


# ─────────────────────────────────────
# 保存
# ─────────────────────────────────────

def doc_id_for(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def load_manifest(work_dir: Path) -> dict:
    try:
        payload = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        # 壊れた manifest は脇へどけて作り直す。保存物は残っている。
        broken = work_dir / "manifest.json"
        broken.replace(work_dir / f"manifest.json.corrupt-{int(time.time())}")
        return {}
    return payload if isinstance(payload, dict) else {}


def tidy_text(text: str) -> str:
    """抜き出した本文の改行を揃え、サロゲートの欠片を落とす。"""
    text = "".join(ch for ch in text if not 0xD800 <= ord(ch) <= 0xDFFF)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def extract_pdf_text(raw: bytes) -> tuple[str, int]:
    """PDF の本文とページ数。読めなければ ("", 0)。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依存は requirements にある
        raise RuntimeError("PDF の本文抽出には pypdf が必要です。") from exc
    try:
        reader = PdfReader(BytesIO(raw))
        # 頁の木が壊れた PDF は、頁を辿る所で例外になる（岡崎市の
        # LimitReachedError「page tree depth」、橿原市の PdfReadError
        # 「Invalid object in /Pages」）。外へ漏れると自治体ごと止まるので、
        # その文書だけ読めない扱いにする。
        pages = list(reader.pages)
    except Exception:
        return "", 0
    parts: list[str] = []
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = tidy_text(text)
        if text:
            parts.append(text)
    body = re.sub(r"\n{3,}", "\n\n", "\n\n".join(parts)).strip()
    return body, len(pages)


# ─────────────────────────────────────
# 形式の見分けと、Excel・Word の本文
# ─────────────────────────────────────

# 保存するときの拡張子。
DOCUMENT_EXTENSIONS = {
    "pdf": ".pdf", "xlsx": ".xlsx", "xlsm": ".xlsm", "xls": ".xls",
    "docx": ".docx", "doc": ".doc", "pptx": ".pptx", "ods": ".ods", "odt": ".odt",
    "zip": ".zip", "csv": ".csv",
}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# 展開後の大きさの上限。ZIP 爆弾で手元の記憶域を食い潰さないため。
ZIP_MEMBER_LIMIT = 64 * 1024 * 1024


def filename_hint(url: str, content_disposition: str = "") -> str:
    """応答の Content-Disposition か URL から、ファイル名（小文字）を取る。"""
    found = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", str(content_disposition or ""), re.I)
    if found:
        return found.group(1).strip().lower()
    return urlsplit(url).path.rsplit("/", 1)[-1].lower()


def sniff_document_type(raw: bytes, url: str = "", content_type: str = "",
                        content_disposition: str = "", hint: str = "") -> str | None:
    """取った中身の形式。`DOCUMENT_EXTENSIONS` の鍵か、文書でなければ None。

    拡張子や Content-Type は当てにならないので、先頭のバイトで見る。
    xlsx・docx・pptx は中身が ZIP なので、入っている名前で分ける。
    xls・doc（OLE）は中身では分けにくいので、ファイル名・Content-Type・
    リンクから見込んだ形式（hint）で決める。
    """
    name = filename_hint(url, content_disposition)
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if raw[:5] == b"%PDF-":
        return "pdf"
    if raw[:4] == b"PK\x03\x04":
        try:
            names = set(zipfile.ZipFile(BytesIO(raw)).namelist())
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
        if "[Content_Types].xml" in names:
            if any(item.startswith("xl/") for item in names):
                return "xlsm" if "xl/vbaProject.bin" in names or name.endswith(".xlsm") else "xlsx"
            if any(item.startswith("word/") for item in names):
                return "docx"
            if any(item.startswith("ppt/") for item in names):
                return "pptx"
        if "mimetype" in names:
            try:
                mimetype = zipfile.ZipFile(BytesIO(raw)).read("mimetype")[:100].decode("ascii", "replace")
            except Exception:
                mimetype = ""
            if "spreadsheet" in mimetype:
                return "ods"
            if "opendocument.text" in mimetype:
                return "odt"
        return "zip"
    if raw[:8] == OLE_MAGIC:
        if name.endswith(".doc") or "msword" in media_type or hint == "doc":
            return "doc"
        return "xls"
    head = raw[:512].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<?xml", b"<head", b"<body")):
        return None
    if name.endswith(".csv") or media_type in ("text/csv", "application/csv") or hint == "csv":
        return "csv"
    return None


def _zip_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > ZIP_MEMBER_LIMIT:
        return None
    return archive.read(name)


SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELATIONSHIP_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_xlsx_text(raw: bytes) -> tuple[str, int]:
    """xlsx・xlsm の本文とシート数。シートごとに、行をタブ区切りで並べる。

    読めなければ ("", 0)。計算式は保存されている計算結果を使う。
    """
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
        names = set(archive.namelist())
        shared: list[str] = []
        strings = _zip_member(archive, "xl/sharedStrings.xml")
        if strings:
            for item in ElementTree.fromstring(strings).iter(f"{SPREADSHEET_NS}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{SPREADSHEET_NS}t")))
        sheets: list[tuple[str, str]] = []
        workbook = _zip_member(archive, "xl/workbook.xml")
        relations = _zip_member(archive, "xl/_rels/workbook.xml.rels")
        if workbook and relations:
            targets = {
                rel.get("Id"): str(rel.get("Target") or "")
                for rel in ElementTree.fromstring(relations).iter(f"{PACKAGE_REL_NS}Relationship")
            }
            for sheet in ElementTree.fromstring(workbook).iter(f"{SPREADSHEET_NS}sheet"):
                target = targets.get(sheet.get(f"{RELATIONSHIP_NS}id"), "")
                if not target:
                    continue
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
                sheets.append((str(sheet.get("name") or ""), path))
        if not sheets:
            sheets = [(item, item) for item in sorted(names) if re.match(r"xl/worksheets/sheet\d+\.xml$", item)]
        parts: list[str] = []
        for title, path in sheets:
            data = _zip_member(archive, path)
            if not data:
                continue
            rows: list[str] = []
            for row in ElementTree.fromstring(data).iter(f"{SPREADSHEET_NS}row"):
                cells: list[str] = []
                for cell in row.iter(f"{SPREADSHEET_NS}c"):
                    kind = cell.get("t", "")
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(f"{SPREADSHEET_NS}t"))
                    else:
                        found = cell.find(f"{SPREADSHEET_NS}v")
                        value = found.text or "" if found is not None else ""
                        if kind == "s" and value.strip().isdigit():
                            index = int(value)
                            value = shared[index] if index < len(shared) else ""
                    value = re.sub(r"\s+", " ", value).strip()
                    if value:
                        cells.append(value)
                if cells:
                    rows.append("\t".join(cells))
            if rows:
                parts.append(f"## {title}\n" + "\n".join(rows))
    except Exception:
        return "", 0
    return tidy_text("\n\n".join(parts)), len(sheets)


def extract_docx_text(raw: bytes) -> str:
    """docx の本文。段落は 1 行、表は行ごとにセルをタブ区切りにする。読めなければ ""。"""

    def paragraph(element) -> str:
        pieces: list[str] = []
        for node in element.iter():
            if node.tag == f"{WORD_NS}t":
                pieces.append(node.text or "")
            elif node.tag == f"{WORD_NS}tab":
                pieces.append("\t")
            elif node.tag in (f"{WORD_NS}br", f"{WORD_NS}cr"):
                pieces.append("\n")
        return "".join(pieces).strip()

    def walk(element, lines: list[str]) -> None:
        for child in element:
            if child.tag == f"{WORD_NS}p":
                lines.append(paragraph(child))
            elif child.tag == f"{WORD_NS}tbl":
                for row in child.findall(f"{WORD_NS}tr"):
                    cells = [
                        " ".join(filter(None, (paragraph(p) for p in cell.iter(f"{WORD_NS}p"))))
                        for cell in row.findall(f"{WORD_NS}tc")
                    ]
                    lines.append("\t".join(cells))
            elif child.tag == f"{WORD_NS}sdt":
                content = child.find(f"{WORD_NS}sdtContent")
                if content is not None:
                    walk(content, lines)

    try:
        data = _zip_member(zipfile.ZipFile(BytesIO(raw)), "word/document.xml")
        if not data:
            return ""
        body = ElementTree.fromstring(data).find(f"{WORD_NS}body")
        if body is None:
            return ""
        lines: list[str] = []
        walk(body, lines)
    except Exception:
        return ""
    text = "\n".join(line for line in lines if line.strip())
    return tidy_text(re.sub(r"\n{3,}", "\n\n", text))


def decode_csv(raw: bytes) -> str:
    """CSV の文字コードを当てて読む（自治体の CSV は Shift_JIS が多い）。"""
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return tidy_text(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return tidy_text(raw.decode("utf-8", errors="replace"))


def zip_member_names(raw: bytes, limit: int = 200) -> list[str]:
    """ZIP の中の名前（展開はしない）。日本語名は cp932 で入っていることが多い。"""
    try:
        infos = zipfile.ZipFile(BytesIO(raw)).infolist()
    except Exception:
        return []
    names: list[str] = []
    for info in infos[:limit]:
        name = info.filename
        if not info.flag_bits & 0x800:
            # UTF-8 の印が無い名前は cp437 として読まれている。cp932 で読み直す。
            try:
                name = name.encode("cp437").decode("cp932")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        names.append(name)
    return names


TEXT_HEADER_FIELDS = (
    ("題名", "title"),
    ("年度", "year_label"),
    ("種別", "kind"),
    ("出典", "source_url"),
    ("掲載ページ", "page_url"),
)


def text_document(entry: dict, body: str) -> str:
    """本文の頭に題名・年度・種別・出典の行を付ける。検索に載せたとき原典へ戻れるように。"""
    header = [f"{label}: {entry.get(key, '')}" for label, key in TEXT_HEADER_FIELDS]
    return "\n".join(header) + "\n\n" + body + "\n"


# ─────────────────────────────────────
# 取得
# ─────────────────────────────────────

class HostGuard:
    """ホスト単位で、接続を切られ続けたら休む。記録は自治体をまたいで共有する。"""

    # 同じプロセスの複数スレッドが同じ記録を書くことがある（--workers）。
    _lock = threading.Lock()

    def __init__(self, work_root: Path, *, threshold: int = HOST_BLOCK_THRESHOLD,
                 seconds: int = HOST_BLOCK_SECONDS) -> None:
        self.path = work_root / HOST_BLOCK_FILE
        self.threshold = threshold
        self.seconds = seconds
        self.failures: dict[str, int] = {}

    def _load(self) -> dict[str, float]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        blocks: dict[str, float] = {}
        for host, until in payload.items():
            try:
                blocks[str(host)] = float(until)
            except (TypeError, ValueError):
                continue
        return blocks

    def blocked(self, host: str, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        until = self._load().get(host)
        return until is not None and current < until

    def success(self, host: str) -> None:
        self.failures.pop(host, None)

    def failure(self, host: str, now: float | None = None) -> bool:
        """接続断を 1 回数える。休みに入ったら True。"""
        count = self.failures.get(host, 0) + 1
        self.failures[host] = count
        if count < self.threshold:
            return False
        current = time.time() if now is None else now
        with self._lock:
            blocks = {h: u for h, u in self._load().items() if u > current}
            blocks[host] = current + self.seconds
            try:
                write_json(self.path, blocks)
            except Exception as exc:
                print(f"[WARN] ホストの休止を残せませんでした: {exc}", flush=True)
        self.failures[host] = 0
        return True


class HostBlocked(Exception):
    pass


@dataclass
class Fetcher:
    session: requests.Session
    guard: HostGuard
    delay: float = DEFAULT_DELAY_SECONDS
    timeout: float = 60.0
    requests_made: int = 0
    _last_at: float = 0.0
    user_agent: str = USER_AGENT

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_at
        if self._last_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_at = time.monotonic()

    def get(self, url: str, *, headers: dict | None = None, stream: bool = False) -> requests.Response:
        host = (urlsplit(url).hostname or "").lower()
        if self.guard.blocked(host):
            raise HostBlocked(host)
        self._wait()
        self.requests_made += 1
        try:
            response = self.session.get(
                url,
                headers={"User-Agent": self.user_agent, **(headers or {})},
                timeout=self.timeout,
                stream=stream,
            )
        except (requests.ConnectionError, requests.Timeout):
            if self.guard.failure(host):
                print(f"[WARN] {host} に接続を切られ続けたので、しばらく休みます", flush=True)
            raise
        self.guard.success(host)
        return response

    def html(self, url: str) -> tuple[str, str] | None:
        response = self.get(url)
        if response.status_code >= 400:
            return None
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if content_type and "html" not in content_type:
            return None
        if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
            response.encoding = response.apparent_encoding or "utf-8"
        return response.url, response.text


@dataclass
class FetchedDocument:
    """`fetch_document` の結果。

    state: `recent`（最近確かめたので問い合わせない）/ `not_modified`（304）/
    `fetched`（中身を取った）/ `failed`（取れなかった。error に例外）。
    """

    state: str
    file_ok: bool = False
    raw: bytes = b""
    headers: Mapping | None = None
    error: Exception | None = None


def read_limited(response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=1 << 16):
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"大きすぎるため取りません（{max_bytes} バイト超）")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_document(fetcher: Fetcher, url: str, entry: dict, work_dir: Path, *, now: datetime,
                   refresh_days: int, max_bytes: int,
                   check: Callable[[object, bytes], None]) -> FetchedDocument:
    """1 件の文書を取る（取り直す）。保存はしない。

    - 保存済みで、`refresh_days` 日以内に確かめていれば問い合わせない
    - 保存済みなら ETag / Last-Modified で条件付きにする
    - `max_bytes` を超えたら途中でやめる
    - `check(response, raw)` が ValueError を投げたら取れなかった扱いにする
      （HTML の「見つかりません」を文書として保存しないため）

    ホストの休止（`HostBlocked`）は呼び出し側へそのまま投げる。
    """
    checked = parse_time(str(entry.get("checked_at", "")))
    file_ok = bool(entry.get("file")) and (work_dir / str(entry["file"])).is_file()
    if file_ok and checked is not None and now - checked < timedelta(days=refresh_days):
        return FetchedDocument("recent", file_ok=True)

    headers: dict[str, str] = {}
    if file_ok:
        if entry.get("etag"):
            headers["If-None-Match"] = str(entry["etag"])
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = str(entry["last_modified"])
    try:
        response = fetcher.get(url, headers=headers, stream=True)
        if response.status_code == 304 and file_ok:
            return FetchedDocument("not_modified", file_ok=True)
        if response.status_code >= 400:
            raise requests.HTTPError(f"HTTP {response.status_code}")
        raw = read_limited(response, max_bytes)
        check(response, raw)
    except HostBlocked:
        raise
    except Exception as exc:
        return FetchedDocument("failed", file_ok=file_ok, error=exc)
    # 応答の見出しは大文字小文字を区別しない辞書のまま渡す（HTTP/2 は小文字で来る）。
    return FetchedDocument("fetched", file_ok=file_ok, raw=raw, headers=response.headers)


def note_failure(entry: dict, error: Exception) -> None:
    entry["last_error"] = f"{type(error).__name__}: {error}"[:300]
    entry["last_error_at"] = now_text()


def note_fetched(entry: dict, fetched: FetchedDocument) -> tuple[str, bool]:
    """取った応答の印を entry に残す。(sha256, 保存済みと同じ中身か) を返す。"""
    headers = fetched.headers or {}
    sha256 = hashlib.sha256(fetched.raw).hexdigest()
    entry["checked_at"] = now_text()
    entry["etag"] = headers.get("ETag", "")
    entry["last_modified"] = headers.get("Last-Modified", "")
    entry.pop("last_error", None)
    entry.pop("last_error_at", None)
    return sha256, bool(fetched.file_ok and entry.get("sha256") == sha256)


ARCHIVE_DIR = "_archive"


def archive_previous_version(work_dir: Path, entry: dict) -> dict | None:
    """中身が変わった文書の前の版を、上書きする前に `_archive/` へ移す。

    取得元で改訂された評価表・報告書も、前の版をアーカイブとして残すため
    （会議録・例規集の `_archive` と同じ考え）。`work_dir/_archive/{日時}_{前の
    sha256 の頭 8 字}/` の下に、元と同じ相対パス（`source/…`・`text/…`）で置く。
    `source/` の外に置くので、文書の列挙や索引には戻ってこない。移した版の印を
    `entry["previous_versions"]` に足して返す。移す物が無ければ None。
    """
    old_sha = str(entry.get("sha256") or "")
    relatives = [str(entry.get(key) or "") for key in ("file", "text_file")]
    relatives = [rel for rel in relatives if rel and (work_dir / rel).is_file()]
    if not old_sha or not relatives:
        return None
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    base = Path(ARCHIVE_DIR) / f"{stamp}_{old_sha[:8]}"
    moved: list[str] = []
    for rel in relatives:
        destination = work_dir / base / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        (work_dir / rel).replace(destination)
        moved.append((base / rel).as_posix())
    version = {
        "sha256": old_sha,
        "bytes": entry.get("bytes"),
        "file_type": entry.get("file_type", ""),
        "fetched_at": entry.get("fetched_at", ""),
        "archived_at": now_text(),
        "files": moved,
    }
    entry.setdefault("previous_versions", []).append(version)
    return version


def mark_missing(documents: dict[str, dict], listed_ids: set[str]) -> list[str]:
    """取得元に見当たらなくなった文書に印を付ける。保存物は消さない。

    入口が開けなかった回などに呼ぶと全部が消えた扱いになるので、呼び出し側で
    「今回の一覧は信用できる」ときだけ呼ぶ。印を付けた doc_id を返す。
    """
    marked: list[str] = []
    for doc_id, entry in documents.items():
        if doc_id in listed_ids:
            continue
        if not entry.get("missing_since"):
            entry["missing_since"] = now_text()
        marked.append(doc_id)
    return marked
