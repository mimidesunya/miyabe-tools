"""事務事業評価の現在版 manifest と保存済み本文だけを読む（AI・再抽出なし）。"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_HYOKA_ROOT = Path(__file__).resolve().parents[2] / "work" / "hyoka"
KINDS = frozenset({"project_evaluation", "policy_evaluation", "external_review", "overview"})
HEADER_LABELS = ("題名", "年度", "種別", "出典", "掲載ページ")


def manifest_paths(root: Path, slugs: set[str] | None = None, exclude_slugs: set[str] | None = None) -> list[Path]:
    """保存先の取り違えを空の正常結果にしない。archive は走査しない。"""
    if not root.is_dir():
        raise ValueError(f"hyoka root does not exist: {root}")
    directories = [root / slug for slug in sorted(slugs)] if slugs else sorted(root.iterdir())
    paths = []
    for directory in directories:
        if directory.name.startswith(("_", ".")) or directory.name in (exclude_slugs or set()):
            continue
        if directory.parent.resolve() != root.resolve():
            raise ValueError("hyoka slug must be a directory name")
        if slugs or directory.is_dir():
            paths.append(directory / "manifest.json")
    return paths


def read_manifest(path: Path) -> dict:
    # document_crawl.load_manifest は破損時にファイルを移動するので使わない。
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("documents"), dict):
        raise ValueError("hyoka manifest documents must be an object")
    if data.get("slug") != path.parent.name:
        raise ValueError("hyoka manifest slug does not match its directory")
    code = str(data.get("jis_code") or "")
    if len(code) != 5 or not code.isdigit() or not str(data.get("name") or "").strip():
        raise ValueError("hyoka manifest municipality metadata is missing")
    return data


def current_text_path(work_dir: Path, entry: dict) -> Path:
    relative = Path(str(entry.get("text_file") or ""))
    path = (work_dir / relative).resolve()
    text_dir = (work_dir / "text").resolve()
    if (relative.is_absolute() or path.parent != text_dir or not path.name.endswith(".txt.gz")
            or text_dir.parent != work_dir.resolve() or "_archive" in path.parts):
        raise ValueError("hyoka text_file must point to current text/*.txt.gz")
    return path


def read_body(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8", errors="strict", newline="") as stream:
        text = stream.read().replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n", len(HEADER_LABELS) + 1)
    # 題名・年度等は manifest から載せる。本文途中の同じ語は除去しない。
    if len(lines) == len(HEADER_LABELS) + 2 and all(
        line.startswith(label + ": ") for line, label in zip(lines, HEADER_LABELS)
    ) and lines[len(HEADER_LABELS)] == "":
        text = lines[-1]
    if not text.strip():
        raise ValueError("hyoka text_status=ok has an empty body")
    return text.strip("\n")


# 題名はリンクの文字から取っているので、掲載ページのリンクに付く「別ウィンドウで開く」などが混ざる。
# 収集の tools/hyoka/scrape_hyoka_docs.py の WINDOW_NOTE_RE と同じ。
WINDOW_NOTE_RE = re.compile(
    r"\s*[（(][^（()）]*(?:別|新規|新しい)ウィンドウ[^（()）]*[）)]|\s*(?:別|新規|新しい)ウィンドウ(?:で|が)開(?:く|きます)"
)


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", WINDOW_NOTE_RE.sub("", title)).strip(" 　​")


def fallback_title(body: str, limit: int = 60) -> str:
    """題名の無い文書（川崎市の古い年度の HTML の頁には <title> が無い）の題名。

    本文の最初の行を使い、「事務事業名」のような短い欄名なら次の行（事業名）とつなぐ。
    """
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return ""
    title = lines[0]
    if len(title) <= 8 and len(lines) > 1:
        title = f"{title} {lines[1]}"
    return title[:limit]


def split_pages(body: str, entry: dict) -> list[tuple[int | None, str, str]]:
    """物理頁が確認できる場合だけ分割する。空頁の番号は詰めない。"""
    pages = entry.get("pages")
    if entry.get("file_type") != "pdf":
        return [(None, body, "non_pdf_document")]
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        return [(None, body, "page_count_unknown")]
    if "\f" in body:
        parts = body.split("\f")
        # pdftotext 型の最後の改頁は新たな空頁を意味しない。
        if not parts[-1].strip() and len(parts) == pages + 1:
            parts.pop()
        if len(parts) == pages:
            return [(number, text, "form_feed") for number, text in enumerate(parts, 1) if text.strip()]
        return [(None, body, "page_boundary_count_mismatch")]
    if pages == 1:
        return [(1, body, "single_page_pdf")]
    # 現行取得器は非空頁を二重改行で結合する。段落区切りと区別できない。
    return [(None, body, "saved_text_no_page_boundaries")]


def record_spans(display_dir: Path | None, slug: str, doc_id: str) -> list[tuple[int, int]]:
    """画面用の控え（data/hyoka/records）の事業ごとの頁の範囲。控えが無ければ空。

    様式ごとの読み取りプログラム（tools/hyoka/forms、川崎市が手本）は、事業ごとに載っている頁の一覧
    `pages` を書く（1 頁だけの事業は `page` だけ）。機械の抽出・AI の書き写しの記録は頁 `page` だけ。
    """
    if display_dir is None:
        return []
    path = display_dir / "records" / slug / f"{doc_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("doc_id") != doc_id:
        return []
    spans = []
    for record in data.get("records") or []:
        if not isinstance(record, dict):
            continue
        pages = [page for page in record.get("pages") or [] if isinstance(page, int) and not isinstance(page, bool) and page > 0]
        page = record.get("page")
        if not pages and isinstance(page, int) and not isinstance(page, bool) and page > 0:
            pages = [page]
        if pages:
            spans.append((min(pages), max(pages)))
    return spans


# 索引の 1 件の分類。事業ごとのシートを検索で優先するために使う。
SEGMENT_SHEET = "sheet"        # 事業 1 件の頁（1 事業が何頁にもわたれば、その範囲をまとめて 1 件）
SEGMENT_TABLE = "table"        # 1 頁に何事業も載る頁（総括や施策の表の一覧）
SEGMENT_OTHER = "other"        # 事業の記録がある文書の、どの事業にも入らない頁（政策体系図、表紙など）
SEGMENT_PAGE = "page"          # 事業の記録が無い文書の頁
SEGMENT_DOCUMENT = "document"  # 頁に分けられない文書の全体


def segment_pages(pages: list[tuple[int | None, str, str]],
                  spans: list[tuple[int, int]]) -> list[tuple[int | None, int | None, str, str, str]]:
    """split_pages の頁を、事業ごとのシートとそれ以外に分ける。返すのは (最初の頁, 最後の頁, 本文, 頁の根拠, 分類)。

    重なる範囲（同じ頁に 2 事業）はまとめて一覧にする。範囲の中に文字のある頁が無ければ、その範囲は出さない。
    頁に分けられない文書は 1 件のまま、事業が 1 つだけならシート、2 つ以上なら一覧とする。
    """
    if any(number is None for number, _, _ in pages):
        number, body, reason = pages[0]
        kind = SEGMENT_SHEET if len(spans) == 1 else SEGMENT_TABLE if spans else SEGMENT_DOCUMENT
        return [(None, None, body, reason, kind)]
    if not spans:
        return [(number, number, body, reason, SEGMENT_PAGE) for number, body, reason in pages]
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += 1
        else:
            merged.append([start, end, 1])
    out = []
    used: set[int] = set()
    for start, end, records in merged:
        inside = [(number, body, reason) for number, body, reason in pages if start <= number <= end]
        if not inside:
            continue
        used.update(number for number, _, _ in inside)
        out.append((inside[0][0], inside[-1][0], "\n\n".join(body for _, body, _ in inside), inside[0][2],
                    SEGMENT_SHEET if records == 1 else SEGMENT_TABLE))
    out += [(number, number, body, reason, SEGMENT_OTHER) for number, body, reason in pages if number not in used]
    return sorted(out, key=lambda segment: segment[0])


def source_page_link(source_url: str, page: int | None) -> str:
    if page is None:
        return source_url
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, f"page={page}"))
