#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""年別の一覧から文書ページへ辿るだけの、自治体独自の会議録サイト向けスクレイパ。

「対応するスクレイパが無い独自検索」として除外していた取得元のうち、
中身を開くと**年を選ぶ → 文書の一覧 → 文書ページ**の三段しか無いものが
いくつもあった。検索フォームは飾りで、一覧は年ごとに素直な HTML で返る。
製品名は付いていないが、構造は kensakusystem や voices より単純である。

取得元ごとの違いは「年をどう列挙するか」「一覧のどのリンクが文書か」
「本文をどこから切り出すか」の三つだけなので、それをアダプタにして、
計画・保存・再開・進捗は他のスクレイパと同じ共通層に任せる。

| system_type | 取得元 | 年の列挙 | 文書 |
| --- | --- | --- | --- |
| shizuoka-notes | 静岡県議会（Lotus Notes） | 展開ビュー 1 枚に全部 | `?OpenDocument` |
| chuo-kugikai | 中央区議会 | `index.cgi` の年セレクト | `kaigiroku.cgi/rNN/*.html` |
| nakano-kugikai | 中野区議会 | `search.html` の年チェック | `view.html?gijiroku_id=` |
| echizen-search | 越前市議会（poseidon） | `Record/?treedepth=年` | `Document4/index.exe` |
| yoshinogawa-asp | 吉野川市議会（ASP） | `index.asp` の年見出し | 日ごとの枠の発言ページを連結 |

使い方:
    python3 tools/gijiroku/scrapers/html_list_sites.py --slug 13102-chuo-ku --ack-robots --max-meetings 3
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))

import gijiroku_planning  # noqa: E402
import gijiroku_storage  # noqa: E402
import gijiroku_targets  # noqa: E402
import minutes_kind  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be)"
ERA_YEAR_RE = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)\s*年")


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t　]+", " ", value or "").strip()


def to_ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6]|pre|font|title|dd|dt)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def anchors(page_html: str, base_url: str) -> list[tuple[str, str]]:
    """(絶対 URL, アンカー文言)。"""
    found: list[tuple[str, str]] = []
    for href, body in re.findall(r'<a\b[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_html, flags=re.S | re.I):
        found.append((urljoin(base_url, html.unescape(href)), normalize_space(html_to_text(body))))
    return found


def year_label_from(text: str) -> str:
    match = ERA_YEAR_RE.search(text or "")
    if not match:
        return ""
    digits = to_ascii_digits(match.group(2))
    if digits == "元":
        digits = "1"
    return f"{match.group(1)}{int(digits)}年"


def era_from_western(year: int) -> str:
    if year >= 2019:
        return f"令和{year - 2018}年"
    if year >= 1989:
        return f"平成{year - 1988}年"
    return f"昭和{year - 1925}年"


def fetch(session: requests.Session, url: str, *, timeout_ms: int, data: dict | None = None, referer: str = "") -> str:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    timeout = max(timeout_ms / 1000.0, 5.0)
    if data is None:
        response = session.get(url, headers=headers, timeout=timeout)
    else:
        response = session.post(url, data=data, headers=headers, timeout=timeout)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def slice_between(page_html: str, start_markers: list[str], end_markers: list[str]) -> str:
    """本文の前後のサイト共通部分を落とす。印が無ければ全体を返す。"""
    body = page_html
    for marker in start_markers:
        index = body.find(marker)
        if index >= 0:
            body = body[index:]
            break
    for marker in end_markers:
        index = body.find(marker)
        if index >= 0:
            body = body[:index]
            break
    return body


# ---------------------------------------------------------------- adapters


class Adapter:
    system_type = ""

    def discover(self, session: requests.Session, source_url: str, timeout_ms: int, walk: dict) -> list[MeetingItem]:
        raise NotImplementedError

    def fetch_text(self, session: requests.Session, item: MeetingItem, timeout_ms: int) -> str:
        raise NotImplementedError


class ShizuokaNotes(Adapter):
    """静岡県議会の本会議会議録（Lotus Notes の Web ビュー）。

    `WebView1?OpenView&ExpandView` で開催別のビューを全部展開すると、
    定例会ごとの見出し行と文書行（発言・議案説明ごと）が 1 枚に並ぶ。
    """

    system_type = "shizuoka-notes"

    def discover(self, session, source_url, timeout_ms, walk):
        base = source_url.rstrip("/")
        view_url = f"{base}/WebView1?OpenView&ExpandView&Count=9999"
        page_html = fetch(session, view_url, timeout_ms=timeout_ms)
        items: list[MeetingItem] = []
        category = ""
        seen: set[str] = set()
        for row in re.findall(r"<tr\b[\s\S]*?</tr>", page_html, flags=re.I):
            links = [(u, t) for u, t in anchors(row, view_url) if "OpenDocument" in u]
            if not links:
                heading = normalize_space(html_to_text(row))
                if ERA_YEAR_RE.search(heading) and ("定例会" in heading or "臨時会" in heading):
                    category = heading
                continue
            for url, text in links:
                if url in seen or not text or "目次" in text:
                    continue
                seen.add(url)
                items.append(MeetingItem(title=text, url=url, year_label=year_label_from(category) or "不明", meeting_group=category or None))
        walk["declared_total"] = len(items)
        return items

    def fetch_text(self, session, item, timeout_ms):
        page_html = fetch(session, item.url, timeout_ms=timeout_ms)
        body = slice_between(page_html, ['<main id="page"', "ここから本文です"], ['class="pagetop2"', '<div id="reference"', '<footer'])
        # 本文は `<div id="notes">` の中にある。タブ画像の段落だけを落とす。
        body = re.sub(r"<h2>議会補足文書</h2>\s*<p>[\s\S]*?</p>", "", body, flags=re.I)
        return html_to_text(body)


class ChuoKugikai(Adapter):
    """中央区議会。`index.cgi` に年を POST すると、その年の会議録が並ぶ。"""

    system_type = "chuo-kugikai"

    def discover(self, session, source_url, timeout_ms, walk):
        form_url = urljoin(source_url, "index.cgi")
        form_html = fetch(session, form_url, timeout_ms=timeout_ms)
        options = re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', form_html)
        items: list[MeetingItem] = []
        seen: set[str] = set()
        missed: list[str] = []
        for value, label in options:
            try:
                listing = fetch(
                    session,
                    form_url,
                    timeout_ms=timeout_ms,
                    data={"term": "date", "sel_year": value, "sort_day": "desc", "searchlist": "表示"},
                    referer=form_url,
                )
            except Exception as exc:
                missed.append(f"{label}: {exc}")
                continue
            year_label = year_label_from(label + "年") or normalize_space(label)
            for url, text in anchors(listing, form_url):
                if "/kaigiroku.cgi/" not in url or url in seen or not text:
                    continue
                if "目次" in text:
                    continue
                seen.add(url)
                items.append(MeetingItem(title=text, url=url, year_label=year_label_from(text) or year_label))
            time.sleep(0.5)
        walk["missed_pages"] = len(missed)
        walk["missed_examples"] = missed[:10]
        return items

    def fetch_text(self, session, item, timeout_ms):
        page_html = fetch(session, item.url, timeout_ms=timeout_ms)
        body = slice_between(page_html, ['id="mainContentsStart"', '<div class="content">'], ['<footer', '<!-- /#container'])
        heading = body.find("<h1")
        if heading >= 0:
            body = body[heading:]
        return html_to_text(body)


class NakanoKugikai(Adapter):
    """中野区議会。`search.html` に年を POST すると `view.html?gijiroku_id=` が並ぶ。"""

    system_type = "nakano-kugikai"

    def discover(self, session, source_url, timeout_ms, walk):
        form_html = fetch(session, source_url, timeout_ms=timeout_ms)
        years = sorted({int(v) for v in re.findall(r'name="s1"[^>]*value="(\d{4})"', form_html)})
        items: list[MeetingItem] = []
        seen: set[str] = set()
        missed: list[str] = []
        for year in years:
            try:
                listing = fetch(session, source_url, timeout_ms=timeout_ms, data={"flg": "check", "s1": str(year)}, referer=source_url)
            except Exception as exc:
                missed.append(f"{year}: {exc}")
                continue
            for url, text in anchors(listing, source_url):
                if "gijiroku_id=" not in url:
                    continue
                doc_id = parse_qs(urlsplit(url).query).get("gijiroku_id", [""])[0]
                if not doc_id or doc_id in seen:
                    continue
                # 同じ文書へ題名と日付の 2 本のリンクがある。長い方が題名。
                if len(text) < 12:
                    continue
                seen.add(doc_id)
                clean = urljoin(source_url, f"view.html?gijiroku_id={doc_id}")
                items.append(MeetingItem(title=text, url=clean, year_label=year_label_from(text) or era_from_western(year)))
            time.sleep(0.5)
        walk["missed_pages"] = len(missed)
        walk["missed_examples"] = missed[:10]
        return items

    def fetch_text(self, session, item, timeout_ms):
        page_html = fetch(session, item.url + "&flg=print", timeout_ms=timeout_ms)
        return html_to_text(page_html)


class EchizenSearch(Adapter):
    """越前市議会（poseidon）。kensakusystem の親戚だが入口が全部違う。

    `Record/?Code=poseidon&treedepth=<年>` がその年の文書一覧、
    本文は `Document4/index.exe?<code>/<fileName>/-1/10/1//0/0` にある。
    """

    system_type = "echizen-search"

    def discover(self, session, source_url, timeout_ms, walk):
        parts = urlsplit(source_url)
        root = f"{parts.scheme}://{parts.netloc}"
        record_url = f"{root}/Record/?Code=poseidon"
        record_html = fetch(session, record_url, timeout_ms=timeout_ms)
        select = re.search(r"<select[^>]*viewyear[^>]*>([\s\S]*?)</select>", record_html, flags=re.I)
        years = re.findall(r"<option[^>]*value=[\"']([^\"']+)[\"']", select.group(1) if select else "", flags=re.I)
        years = [html.unescape(y) for y in years if y and y != "all"]
        items: list[MeetingItem] = []
        seen: set[str] = set()
        missed: list[str] = []
        for year in years:
            try:
                # 年は Shift_JIS で URL エンコードしないと通じない。UTF-8 で送ると
                # 黙って既定の年（最新）に落ち、どの年を頼んでも同じ 11 件が返る。
                listing = fetch(session, f"{record_url}&treedepth={requests.utils.quote(year, encoding='cp932')}", timeout_ms=timeout_ms)
            except Exception as exc:
                missed.append(f"{year}: {exc}")
                continue
            for url, text in anchors(listing, record_url):
                if "fileName=" not in url or "Document0" not in url:
                    continue
                file_name = parse_qs(urlsplit(url).query).get("fileName", [""])[0]
                if not file_name or file_name in seen or "目次" in text:
                    continue
                seen.add(file_name)
                items.append(MeetingItem(title=text, url=url, year_label=year_label_from(text) or year_label_from(year) or "不明"))
            time.sleep(0.5)
        walk["missed_pages"] = len(missed)
        walk["missed_examples"] = missed[:10]
        return items

    def fetch_text(self, session, item, timeout_ms):
        parts = urlsplit(item.url)
        root = f"{parts.scheme}://{parts.netloc}"
        file_name = parse_qs(parts.query).get("fileName", [""])[0]
        code = parse_qs(parts.query).get("Code", ["poseidon"])[0]
        for suffix in ("PRINT_ALL/0/0", "/0/0"):
            url = f"{root}/Document4/index.exe?{code}/{file_name}/-1/10/1/{suffix}"
            page_html = fetch(session, url, timeout_ms=timeout_ms, referer=item.url)
            text = html_to_text(re.sub(r"<head[\s\S]*?</head>", "", page_html, flags=re.I))
            if len(text) > 200:
                return text
        return text


class YoshinogawaAsp(Adapter):
    """吉野川市議会（ASP）。定例会 → 日 → 発言ごとのページ。

    `index.asp` に定例会（`proc_list.asp?cid=`）が並び、定例会ごとに日の文書
    （`proc_disp2.asp?cid=&id=`）がある。本文は日の枠の中の一覧フレーム
    （`proc_disp_list2.asp`）が指す発言ページ（`proc_disp_remark2.asp?id=`）に
    1 発言ずつ分かれているので、全部読んで繋ぐ。セッション無しで文書を開くと
    エラーページになるので、入口から順に開く。
    """

    system_type = "yoshinogawa-asp"

    def __init__(self) -> None:
        # 文書は定例会の一覧を踏んだセッションでしか開けない。どの一覧から来たかを控える。
        self.list_url_by_doc: dict[str, str] = {}

    def _base(self, source_url: str) -> str:
        return source_url.rsplit("/", 1)[0] + "/"

    def discover(self, session, source_url, timeout_ms, walk):
        base = self._base(source_url)
        index_html = fetch(session, base + "index.asp", timeout_ms=timeout_ms)
        items: list[MeetingItem] = []
        seen: set[str] = set()
        missed: list[str] = []
        # 年見出し（<strong>令和６年</strong>）の下に定例会が並ぶ。
        year = ""
        for chunk in re.split(r"(?=<strong>)", index_html):
            heading = re.match(r"<strong>([^<]+)</strong>", chunk)
            if heading:
                year = year_label_from(heading.group(1)) or year
            for url, text in anchors(chunk, base):
                if "proc_list.asp?cid=" not in url or url in seen:
                    continue
                seen.add(url)
                try:
                    listing = fetch(session, url, timeout_ms=timeout_ms, referer=base + "index.asp")
                except Exception as exc:
                    missed.append(f"{text}: {exc}")
                    continue
                for doc_url, doc_text in anchors(listing, url):
                    if "proc_disp2.asp?cid=" not in doc_url or doc_url in seen or not doc_text:
                        continue
                    seen.add(doc_url)
                    self.list_url_by_doc[doc_url] = url
                    items.append(MeetingItem(title=doc_text, url=doc_url, year_label=year_label_from(doc_text) or year or "不明", meeting_group=text or None))
                time.sleep(0.5)
        walk["missed_pages"] = len(missed)
        walk["missed_examples"] = missed[:10]
        return items

    def fetch_text(self, session, item, timeout_ms):
        base = self._base(item.url)
        # 入口を踏んでからでないと文書がエラーページになる。
        fetch(session, base + "index.asp", timeout_ms=timeout_ms)
        list_url = self.list_url_by_doc.get(item.url) or f"{base}proc_list.asp?cid={parse_qs(urlsplit(item.url).query).get('cid', [''])[0]}"
        fetch(session, list_url, timeout_ms=timeout_ms, referer=base + "index.asp")
        frame_html = fetch(session, item.url, timeout_ms=timeout_ms, referer=list_url)
        list_src = next((src for src in re.findall(r"<(?:frame|iframe)[^>]+src=[\"']([^\"']+)", frame_html, flags=re.I) if "proc_disp_list2" in src), "")
        if not list_src:
            raise RuntimeError(f"発言一覧のフレームが見つかりません: {item.url}")
        list_html = fetch(session, urljoin(item.url, list_src), timeout_ms=timeout_ms, referer=item.url)
        remark_ids: list[str] = []
        for url, _text in anchors(list_html, item.url):
            match = re.search(r"proc_disp_remark2\.asp\?id=(\d+)", url)
            if match and match.group(1) not in remark_ids:
                remark_ids.append(match.group(1))
        if not remark_ids:
            raise RuntimeError(f"発言が 1 件も見つかりません: {item.url}")
        parts: list[str] = []
        for index, remark_id in enumerate(remark_ids):
            page_html = fetch(session, f"{base}proc_disp_remark2.asp?id={remark_id}", timeout_ms=timeout_ms, referer=item.url)
            body = slice_between(page_html, ["<body"], ["</body>"])
            text = html_to_text(body)
            if index > 0:
                # 各ページ先頭の「令和６年１２月定例会第４日目(2024.12.20)」の見出しは 1 回でよい。
                text = re.sub(r"^[^\n]*\n", "", text, count=1)
            parts.append(text)
            time.sleep(0.3)
        return "\n\n".join(parts)


ADAPTERS: dict[str, Adapter] = {
    adapter.system_type: adapter for adapter in (ShizuokaNotes(), ChuoKugikai(), NakanoKugikai(), EchizenSearch(), YoshinogawaAsp())
}


def emit_progress(current: int, total: int, state_path: Path, state: dict) -> None:
    print(f"[PROGRESS] unit=meeting current={max(0, current)} total={max(0, total)}", flush=True)
    state["progress_current"] = max(0, int(current))
    state["progress_total"] = max(0, int(total))
    state["progress_unit"] = "meeting"
    gijiroku_storage.save_state(state_path, state)


def build_meeting_text(item: MeetingItem, body_text: str) -> str:
    held_on = minutes_kind.extract_plausible_held_on(body_text, title=item.title, year_label=item.year_label, filename=item.url)
    header = [item.title]
    if item.meeting_group:
        header.append(item.meeting_group)
    header.append(item.year_label)
    header.extend(minutes_kind.held_on_header_lines(held_on))
    header.append(f"Source URL: {item.url}")
    header.append("")
    header.append(body_text)
    return "\n".join(header).strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--max-meetings", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--ack-robots", action="store_true")
    parser.add_argument("--save-html", action="store_true", help="互換用（未使用）")
    parser.add_argument("--headful", action="store_true", help="互換用（未使用）")
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug)
    system_type = str(target.get("system_type") or "")
    adapter = ADAPTERS.get(system_type)
    if adapter is None:
        print(f"[ERROR] この取得元の形は扱えません: {system_type}（対応: {', '.join(ADAPTERS)}）")
        return 2
    if not args.ack_robots:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。")
        return 2

    output_dir: Path = (args.output_dir or target["work_dir"]).resolve()
    work_dir = output_dir
    downloads_dir = (output_dir / "downloads").resolve() if args.output_dir is not None else Path(target["downloads_dir"]).resolve()
    index_json = (output_dir / "meetings_index.json").resolve() if args.output_dir is not None else Path(target["index_json_path"]).resolve()
    result_csv = work_dir / f"run_result_{now_ts()}.csv"
    state_path = work_dir / "scrape_state.json"
    state = gijiroku_storage.load_state(state_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Target: {target['name']} ({target['slug']}, {system_type})")
    print(f"[INFO] Source URL: {target['source_url']}")

    session = requests.Session()
    print("[INFO] 会議一覧を収集中...")
    walk: dict = {}
    meeting_items = adapter.discover(session, str(target["source_url"]), args.timeout_ms, walk)
    limit_reached = False
    if args.max_meetings > 0 and len(meeting_items) > args.max_meetings:
        meeting_items = meeting_items[: args.max_meetings]
        limit_reached = True
    plan_shrank = gijiroku_storage.meetings_index_would_shrink(index_json, [asdict(item) for item in meeting_items])
    gijiroku_storage.record_catalog_walk(
        work_dir,
        discovered=len(meeting_items),
        plan_shrank=plan_shrank,
        missed_pages=int(walk.get("missed_pages") or 0),
        missed_examples=walk.get("missed_examples") or [],
        limit_reached=limit_reached,
        extra={"declared_total": int(walk.get("declared_total") or 0)} if walk.get("declared_total") else None,
    )
    print(f"[INFO] 会議候補 {len(meeting_items)} 件")
    if not meeting_items:
        raise RuntimeError("会議候補が 0 件でした。一覧の形が変わったか、取得元が応えていません。")

    index_json.parent.mkdir(parents=True, exist_ok=True)
    gijiroku_storage.save_meetings_index(index_json, [asdict(item) for item in meeting_items])
    emit_progress(0, len(meeting_items), state_path, state)

    with result_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "year", "url", "status", "output", "error", "documents", "fragments"])
        writer.writeheader()
        planned_items = [gijiroku_planning.attach_text_output(plan) for plan in gijiroku_planning.build_base_plans(meeting_items, downloads_dir)]
        previous_missing = gijiroku_planning.previous_missing_count(state)
        planned_items, work_items, missing_count = gijiroku_planning.select_work_items(
            planned_items, no_resume=args.no_resume, previous_missing_count=previous_missing
        )
        date_range = gijiroku_planning.describe_date_range(planned_items)
        if date_range:
            print(f"[INFO] Discovered meeting date range: {date_range}", flush=True)
        gijiroku_planning.save_plan_summary(state_path, state, planned_items, missing_count, previous_missing)
        if missing_count > 0:
            print(f"[INFO] Missing outputs: {missing_count}/{len(planned_items)}", flush=True)
        if not args.no_resume and not work_items:
            print("[INFO] All expected outputs already exist; skipping download loop.", flush=True)
        saved_count = sum(1 for plan in planned_items if plan.get("existing_output") is not None)
        emit_progress(saved_count, len(meeting_items), state_path, state)

        for idx, plan in enumerate(work_items, start=1):
            item = plan["item"]
            print(f"[{idx}/{len(work_items)}] {item.year_label} {item.title}")
            status = ""
            output_path = ""
            error_msg = ""
            resume_key = plan["resume_key"]
            existing_output = plan["existing_output"]
            if not args.no_resume and existing_output is not None:
                output_path = str(existing_output)
                status = "skipped_existing"
                saved_count += 1
            else:
                try:
                    body_text = adapter.fetch_text(session, item, args.timeout_ms)
                    if len(body_text) < 40:
                        raise RuntimeError(f"本文が短すぎます（{len(body_text)} 字）: {item.url}")
                    dest = gijiroku_storage.write_text(plan["dest_base"], build_meeting_text(item, body_text), compress=True)
                    output_path = str(dest)
                    status = "saved_text"
                    saved_count += 1
                except Exception as exc:
                    status = "error"
                    error_msg = str(exc)
            state["items"][resume_key] = {
                "title": item.title,
                "year_label": item.year_label,
                "url": item.url,
                "status": "saved_text" if status in {"saved_text", "skipped_existing"} else status,
                "output_rel_path": str(Path(output_path).relative_to(downloads_dir)) if output_path else "",
                "updated_at": now_ts(),
            }
            gijiroku_storage.save_state(state_path, state)
            writer.writerow({"title": item.title, "year": item.year_label, "url": item.url, "status": status, "output": output_path, "error": error_msg, "documents": 1, "fragments": 0})
            handle.flush()
            emit_progress(saved_count, len(meeting_items), state_path, state)
            if status == "saved_text" and args.delay_seconds > 0 and idx < len(work_items):
                time.sleep(args.delay_seconds)

    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
