#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""DBSR 系の会議録システム向けスクレイパ。

この形式では CGI parameter の奥に年度・一覧・詳細ページがある。
日付付きの文書行を列挙して印刷用本文を取得し、ファイル名や再開判断は
gijiroku_planning へ任せる。
"""

from __future__ import annotations

import argparse
import csv
import datetime
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
# `python tools/.../dbsr.py` の直接実行と、小さな結合確認からの import の両方で動かす。
# そのため、隣接モジュールを sys.path に明示的に入れる。
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import gijiroku_planning
import gijiroku_storage
import gijiroku_targets


DEFAULT_WAIT_MS = 10_000
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 900
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None
    list_url: str | None = None
    held_on: str | None = None
    doc_urls: list[str] | None = None
    doc_kind: str | None = None


@dataclass
class ListPage:
    title: str
    year_label: str
    url: str
    meeting_group: str
    auxiliary_docs: list[dict[str, str]]
    # 検索フォームを送らないと一覧が出ず、その結果 URL を開き直せない
    # 取得元がある（国分寺市など。CSRF トークンがセッションに紐づく）。
    # その場で読み切った行をここに載せ、後段は再訪問せずに使う。
    documents: list["DocumentRow"] | None = None


@dataclass
class DayDocumentGroup:
    title: str
    year_label: str
    meeting_group: str
    list_url: str
    doc_urls: list[str]
    held_on: str


@dataclass
class DocumentRow:
    title: str
    url: str
    held_on: str


class DiscoveryTimeoutError(RuntimeError):
    pass


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def emit_progress(
    current: int,
    total: int,
    state_path: Path | None = None,
    state: dict | None = None,
) -> None:
    print(f"[PROGRESS] unit=meeting current={max(0, current)} total={max(0, total)}", flush=True)
    if state_path is not None:
        if state is not None:
            state["progress_current"] = max(0, int(current))
            state["progress_total"] = max(0, int(total))
            state["progress_unit"] = "meeting"
            gijiroku_storage.save_state(state_path, state)
        else:
            gijiroku_storage.update_progress_state(state_path, current=current, total=total, unit="meeting")


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", value).strip()


def sanitize_filename(text: str, fallback: str) -> str:
    return gijiroku_planning.sanitize_filename(text, fallback)


def normalize_year_dir(year_label: str) -> str:
    label = sanitize_filename((year_label or "unknown").strip(), "unknown")
    if not label:
        return "unknown"
    return label


def normalize_meeting_group_dir(meeting_group: str | None) -> str:
    if not meeting_group:
        return ""
    return sanitize_filename(meeting_group, "meeting")


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6]|pre|section|article)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_inner_text(locator, timeout_ms: int = 1_500) -> str:
    try:
        return (locator.inner_text(timeout=timeout_ms) or "").strip()
    except Exception:
        return ""


def safe_href(locator) -> str:
    try:
        return locator.get_attribute("href") or ""
    except Exception:
        return ""


def discovery_deadline(timeout_seconds: int) -> float | None:
    if timeout_seconds <= 0:
        return None
    return time.monotonic() + max(1, int(timeout_seconds))


def ensure_discovery_time(deadline: float | None, detail: str) -> None:
    if deadline is None:
        return
    if time.monotonic() > deadline:
        raise DiscoveryTimeoutError(f"会議一覧の収集が制限時間を超えました: {detail}")


def cleaned_query_pairs(url: str) -> list[tuple[str, str]]:
    return [(key, value) for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)]


def query_value(url: str, key: str) -> str:
    for current_key, current_value in cleaned_query_pairs(url):
        if current_key == key:
            return current_value
    return ""


def canonicalize_template_url(url: str) -> str:
    parts = urlsplit(url)
    query = cleaned_query_pairs(url)
    template = ""
    others: list[tuple[str, str]] = []
    for key, value in query:
        if key == "Template":
            template = value
        else:
            others.append((key, value))
    others.sort()
    normalized_query: list[tuple[str, str]] = []
    if template:
        normalized_query.append(("Template", template))
    normalized_query.extend(others)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(normalized_query), ""))


def request_text(request_context, url: str, timeout_ms: int, referer: str | None = None) -> str:
    headers = {"referer": referer} if referer else None
    response = request_context.get(url, timeout=timeout_ms, headers=headers)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status}: {url}")
    body = response.body()
    for encoding in ("utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="ignore")


def clean_page_title(title_text: str) -> str:
    if "|" in title_text:
        return normalize_space(title_text.split("|", 1)[0])
    return normalize_space(title_text)


def dbsr_index_root(source_url: str) -> str:
    parts = urlsplit(source_url)
    path = parts.path or "/"
    if path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def find_search_library_url(page, source_url: str) -> str:
    candidates: list[str] = []
    # 入口ページが読み終える前に別ページへ移る取得元がある（鳥取県）。
    # リンクを読めなくても下の推測 URL で先へ進めるので、ここで止めない。
    try:
        links = page.locator("a")
        for i in range(links.count()):
            href = safe_href(links.nth(i))
            # Template 名の大文字小文字は取得元によって違う（Template=List など）。
            if "template=search-library" not in href.lower():
                continue
            candidates.append(urljoin(page.url, href))
    except Exception:
        candidates = []
    if candidates:
        return candidates[0]

    base_url = dbsr_index_root(source_url)
    return urljoin(base_url, "100000?Template=search-library")


# 入口ページの一覧は「直近４年分または12年分」しか出さないテンプレートがある。
# それ以前の会議は期間を明示した文書一覧からしか辿れない。どの経路で
# 会議を見つけたかを呼び出し元へ返し、収録範囲の判定に使う。
DISCOVERY_SOURCE_LIBRARY = "search_library"
DISCOVERY_SOURCE_RECENT = "recent_fallback"
DISCOVERY_SOURCE_FULL_PERIOD = "full_period"


def detail_search_year_bounds(page) -> tuple[str, str]:
    """「くわしく検索」の期間指定から、選べる最小年と最大年を返す。

    入口ページが遅れて別ページへ移る取得元（鳥取県）では、読んでいる
    最中に DOM が入れ替わる。年が読めなくても後続の経路で会議一覧へ
    辿れるので、ここで探索ごと止めない。
    """
    years: list[int] = []
    try:
        options = page.locator("select[name='TermStartYear'] option")
        for index in range(options.count()):
            value = (options.nth(index).get_attribute("value") or "").strip()
            if value.isdigit():
                years.append(int(value))
    except Exception:
        return "", ""
    if not years:
        return "", ""
    return str(min(years)), str(max(years))


def full_period_list_url(source_url: str, start_year: str, end_year: str) -> str:
    """指定期間の文書一覧 URL を組み立てる。

    会議種別（Cabinet）を指定しないと全種別が対象になる。入口ページの
    「最近の会議録」に出ない古い会議も、この一覧なら辿れる。
    """
    base_url = dbsr_index_root(source_url)
    query = urlencode(
        [
            ("Template", "list"),
            ("ListOrder", "Asc"),
            ("QueryType", "New"),
            ("TermStart", f"{start_year}-01-01"),
            ("TermEnd", f"{end_year}-12-31"),
        ]
    )
    return urljoin(base_url, f"100000?{query}")


ERA_STARTS = (
    ("令和", (2019, 5, 1), 2018),
    ("平成", (1989, 1, 8), 1988),
    ("昭和", (1926, 12, 25), 1925),
)


def era_year_label(held_on: str | None) -> str:
    """開催日から「平成9年」のような年ラベルを作る。"""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(held_on or ""))
    if not match:
        return "不明"
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    for era, start, offset in ERA_STARTS:
        if (year, month, day) >= start:
            return f"{era}{year - offset}年"
    return f"{year}年"


def meeting_name_from_document_title(title: str) -> str:
    """文書の表題から会議名だけを取り出す。

    「平成９年第４回定例会（第５日目）　議事日程・名簿」→「平成９年第４回定例会（第５日目）」
    """
    normalized = normalize_space(title)
    match = re.match(r"^(.*?[）)])", normalized)
    if match:
        return normalize_space(match.group(1))
    for suffix in ("議事日程・名簿", "議事日程", "本文", "名簿", "署名", "一般質問", "〔資料〕", "資料"):
        if normalized.endswith(suffix):
            return normalize_space(normalized[: -len(suffix)]) or normalized
    return normalized


# 会議種別として使うために、会議名から年・回次・日別の番号を落とす。
#   「令和８年第１回定例会（第７号）」→「定例会」
#   「平成30年第2回総務委員会」      →「総務委員会」
# これを落とさないと会議ごとに別々の種別になり、種別での絞り込みができない。
MEETING_GROUP_TRIM_PATTERNS = (
    re.compile(r"^(明治|大正|昭和|平成|令和)\s*[元\d０-９]+\s*年度?\s*"),
    re.compile(r"^第\s*[\d０-９]+\s*回\s*"),
    re.compile(r"[（(]\s*第?\s*[\d０-９]+\s*(?:号|回|日目|日)\s*[)）]\s*$"),
    re.compile(r"[\[［][^\]］]*[\]］]\s*$"),
    re.compile(r"\s*(?:目次|会期日程|提出議案一覧表|議事日程・名簿|議事日程|本文|名簿|署名)\s*$"),
)


def meeting_group_from_meeting_name(meeting_name: str) -> str:
    """会議名から会議種別を取り出す。落としきれなければ元の会議名を返す。"""
    label = normalize_space(meeting_name)
    for _ in range(4):
        before = label
        for pattern in MEETING_GROUP_TRIM_PATTERNS:
            label = normalize_space(pattern.sub("", label))
        if label == before:
            break
    return label or normalize_space(meeting_name)


def build_full_period_day_groups(list_url: str, rows: list[DocumentRow]) -> list[DayDocumentGroup]:
    """期間指定の文書一覧を、開催日と会議名の単位でまとめる。

    この一覧は会議種別が混ざるため、日付だけでまとめると別々の会議が
    ひとつに潰れる。表題から会議名を取り出して組にする。
    """
    grouped: dict[tuple[str, str], list[DocumentRow]] = {}
    ordered_keys: list[tuple[str, str]] = []
    for row in rows:
        key = (row.held_on, meeting_name_from_document_title(row.title))
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(row)

    groups: list[DayDocumentGroup] = []
    for held_on, meeting_name in ordered_keys:
        doc_rows = grouped[(held_on, meeting_name)]
        body_rows = [row for row in doc_rows if "本文" in normalize_space(row.title)]
        chosen_rows = body_rows or doc_rows
        suffix = document_suffix(chosen_rows[0].title)
        groups.append(
            DayDocumentGroup(
                title=f"{infer_day_title_from_held_on(held_on)}－{suffix}",
                year_label=era_year_label(held_on),
                meeting_group=meeting_group_from_meeting_name(meeting_name) or "会議",
                list_url=list_url,
                doc_urls=[row.url for row in chosen_rows],
                held_on=held_on,
            )
        )
    return groups


def held_on_from_text(value: str) -> str | None:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", value)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    return None


def japanese_date_label(year_label: str, held_on: str | None) -> str | None:
    if not held_on:
        return None
    year_match = re.search(r"(昭和|平成|令和)\s*([元\d０-９]+)年", year_label)
    date_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", held_on)
    if not year_match or not date_match:
        return None
    return f"{year_match.group(1)}{year_match.group(2)}年{int(date_match.group(2))}月{int(date_match.group(3))}日"


def detect_meeting_group(text: str, title_text: str) -> str:
    cleaned = normalize_space(text)
    if cleaned:
        cleaned = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", cleaned).strip()
        if cleaned:
            return cleaned

    page_title = clean_page_title(title_text)
    page_title = re.sub(r"\s*目次$", "", page_title)
    page_title = re.sub(r"\s*会議録一覧$", "", page_title)
    page_title = re.sub(r"\s*検索結果一覧$", "", page_title)
    page_title = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", page_title).strip()
    return normalize_space(page_title) or "会議録"


def collect_list_page_entries(page, entries, year_label: str, items: dict[str, ListPage]) -> None:
    for entry_index in range(entries.count()):
        entry = entries.nth(entry_index)
        anchors = entry.locator("a")
        list_url = ""
        meeting_group = ""
        auxiliary_docs: list[dict[str, str]] = []

        for anchor_index in range(anchors.count()):
            anchor = anchors.nth(anchor_index)
            text = safe_inner_text(anchor)
            href = safe_href(anchor)
            if not href:
                continue
            absolute_url = canonicalize_template_url(urljoin(page.url, href))

            href_lower = href.lower()
            if "template=list" in href_lower and list_url == "":
                list_url = absolute_url
                meeting_group = detect_meeting_group(text, page.title())
                continue

            if "template=mokuji" in href_lower:
                auxiliary_docs.append(
                    {
                        "title": normalize_space(text) or "補助資料",
                        "url": absolute_url,
                    }
                )

        if not list_url and auxiliary_docs:
            # 一覧を持たず目次だけの取得元がある（富山県）。目次ページに
            # 本文リンクが並ぶので、目次をそのまま一覧として扱う。
            list_url = str(auxiliary_docs[0].get("url") or "")
        if not list_url:
            continue

        items.setdefault(
            list_url,
            ListPage(
                title=meeting_group,
                year_label=year_label,
                url=list_url,
                meeting_group=meeting_group,
                auxiliary_docs=auxiliary_docs,
            ),
        )


def collect_template_list_links(
    page, items: dict[str, ListPage], deadline: float | None, label: str
) -> int:
    """いま開いているページから会議一覧（Template=list）へのリンクを集める。"""
    added = 0
    links = page.locator("a[href*='Template=list' i]")
    total = links.count()
    # 一覧リンクが数百並ぶ取得元があるので、ページ全体で変わらない値は
    # ループの外で 1 回だけ取る。
    page_url = page.url
    page_title = page.title()
    for index in range(total):
        ensure_discovery_time(deadline, f"{label} {index + 1}/{total}")
        link = links.nth(index)
        href = safe_href(link)
        if not href:
            continue
        list_url = canonicalize_template_url(urljoin(page_url, href))
        if list_url in items:
            continue
        text = safe_inner_text(link)
        meeting_group = detect_meeting_group(text, page_title)
        items[list_url] = ListPage(
            title=meeting_group or text,
            year_label=normalize_space(text) or "不明",
            url=list_url,
            meeting_group=meeting_group,
            auxiliary_docs=[],
        )
        added += 1
    return added


RECENT_ONLY_YEAR_SPAN = 4


def list_links_cover_recent_years_only(items: dict[str, ListPage]) -> bool:
    """集めた一覧リンクが直近数年しか指していないかを見る。

    期間を URL に持つ取得元では、そこに書かれた年の幅で判断できる。
    期間を持たないリンクが混ざっていれば、全期間かどうかは判断できない
    ので、狭いとは決めつけない。
    """
    years: set[int] = set()
    for list_url in items:
        found = re.findall(r"Term(?:Start|End)(?:Year)?=(\d{4})", list_url)
        if not found:
            return False
        years.update(int(value) for value in found)
    if not years:
        return False
    return (max(years) - min(years)) < RECENT_ONLY_YEAR_SPAN


# 会議録が始まる前まで遡れば、その取得元の全期間になる。
WIDENED_PERIOD_START = "1970-01-01"
# 全期間の一覧から作った ListPage の年ラベル。年度別一覧と区別するために使う。
WIDENED_PERIOD_LABEL = "全期間"


# 検索フォームの会議種別（本会議・各委員会）の選択肢を読む JS。
# 取得元によって CabinetName（名前）と Cabinet（番号）に分かれる。
CABINET_OPTIONS_EVAL = r"""
() => {
  const out = [];
  document.querySelectorAll('select').forEach(sel => {
    const raw = sel.name || sel.id || '';
    const key = raw.replace(/\[\]$/, '');
    if (!/^Cabinet(Name)?$/i.test(key)) return;
    Array.from(sel.options).forEach(o => {
      const v = (o.value || '').trim();
      if (v) out.push({key: key, value: v, text: (o.text || '').trim()});
    });
  });
  return out;
}
"""


def read_cabinet_options(page) -> list[dict]:
    """会議種別の選択肢を返す。メニューに並ばない委員会もここから拾える。"""
    try:
        return page.evaluate(CABINET_OPTIONS_EVAL) or []
    except Exception:
        return []


def record_offered_types(sink: list[str] | None, options: list[dict]) -> None:
    """取得元が「こういう会議種別がある」と自分で示している一覧を控える。

    収録できたかどうかとは別に、**取得元が何を持っていると言っているか**を
    残しておくと、「委員会が無いのは公開していないからか、こちらの見落としか」
    を後から機械で判定できる。
    """
    if sink is None:
        return
    for option in options:
        text = normalize_space(str(option.get("text") or option.get("value") or ""))
        if text and text not in sink:
            sink.append(text)


def collect_cabinet_options(page, source_url: str, timeout_ms: int) -> list[dict]:
    """会議種別の選択肢を、載っていそうなページを順に開いて集める。

    検索フォームがどのページにあるかは取得元によって違う。いま開いている
    ページ・検索ページ・閲覧メニューの順に見て、最初に見つかったものを返す。
    見つけたページへ移動するので、一覧を読み終えてから呼ぶこと。
    """
    options = read_cabinet_options(page)
    if options:
        return options

    candidates: list[str] = []
    try:
        candidates.append(find_search_library_url(page, source_url))
    except Exception:
        pass
    try:
        links = page.locator(MENU_TEMPLATE_SELECTOR)
        for index in range(links.count()):
            href = safe_href(links.nth(index))
            if not href:
                continue
            menu_url = canonicalize_template_url(urljoin(source_url, href))
            if menu_url not in candidates:
                candidates.append(menu_url)
    except Exception:
        pass

    for candidate in candidates:
        if not candidate:
            continue
        try:
            page.goto(candidate, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            continue
        options = read_cabinet_options(page)
        if options:
            return options
    return []


def cabinet_of(list_url: str) -> str:
    """一覧 URL が指している会議種別を返す。指定が無ければ空。"""
    query = dict(cleaned_query_pairs(list_url))
    return query.get("CabinetName", query.get("Cabinet", ""))


def missing_cabinet_list_pages(
    items: dict[str, ListPage], cabinet_options: list[dict] | None
) -> list[ListPage]:
    """一覧に出てこない会議種別ぶんの、全期間一覧を作る。

    年度別の一覧が全期間そろっていても、それが本会議だけということがある。
    愛知県は `Cabinet=1`（本会議）の一覧だけで 1996 年から 2026 年まで揃うので、
    「全部取れた」ように見えて委員会が丸ごと欠けていた。検索フォームに並ぶ
    会議種別のうち一覧に出てこないものを、期間を全期間にして補う。
    """
    if not items or not cabinet_options:
        return []
    seen = {cabinet_of(url) for url in items}
    template_url = next(
        (
            url
            for url in items
            if "TermStart" in dict(cleaned_query_pairs(url))
            or "TermEnd" in dict(cleaned_query_pairs(url))
        ),
        "",
    )
    if not template_url:
        return []

    parts = urlsplit(template_url)
    base_query = dict(cleaned_query_pairs(template_url))
    base_query["TermStart"] = WIDENED_PERIOD_START
    # 年度別一覧が年内の先の日付を含むことがあるので、終端は年末にそろえる。
    base_query["TermEnd"] = datetime.date(datetime.date.today().year, 12, 31).isoformat()

    pages: list[ListPage] = []
    for option in cabinet_options:
        key = str(option.get("key") or "").strip()
        value = str(option.get("value") or "").strip()
        if not key or not value or value in seen:
            continue
        seen.add(value)
        query = dict(base_query)
        query.pop("CabinetName", None)
        query.pop("Cabinet", None)
        query[key] = value
        url = canonicalize_template_url(
            urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(sorted(query.items())), "")
            )
        )
        label = normalize_space(str(option.get("text") or value))
        pages.append(
            ListPage(
                title=f"全期間（{label}）",
                year_label=WIDENED_PERIOD_LABEL,
                url=url,
                # 会議種別が分かっているので必ず渡す。空にすると収録した会議が
                # 種別なしになり、委員会を取れているかどうかを後から判定できない。
                meeting_group=meeting_group_from_meeting_name(label),
                auxiliary_docs=[],
            )
        )
    return pages


def widened_period_list_pages(
    items: dict[str, ListPage], cabinet_options: list[dict] | None = None
) -> list[ListPage]:
    """直近しか指していない一覧リンクを、全期間を指す形に組み替える。

    期間は URL の TermStart / TermEnd で決まるので、そこだけ広げれば同じ
    会議種別のまま古い会議録まで辿れる。種別（CabinetName）ごとに 1 本に
    まとめる。広げられる形のリンクが無ければ空を返す。
    """
    by_cabinet: dict[str, str] = {}
    today = datetime.date.today().isoformat()
    for list_url in items:
        parts = urlsplit(list_url)
        query = dict(cleaned_query_pairs(list_url))
        if "TermStart" not in query and "TermEnd" not in query:
            continue
        query["TermStart"] = WIDENED_PERIOD_START
        query["TermEnd"] = today
        # 会議種別は取得元によって CabinetName（名前）と Cabinet（番号）に
        # 分かれる。どちらで区切っているかを見て、種別ごとに 1 本ずつ作る。
        cabinet = query.get("CabinetName", query.get("Cabinet", ""))
        if cabinet in by_cabinet:
            continue
        widened = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(sorted(query.items())), "")
        )
        by_cabinet[cabinet] = canonicalize_template_url(widened)

    # メニューに並ぶのは本会議だけ、という取得元がある（福岡県など）。
    # 検索フォームには委員会も並んでいるので、同じ形の URL を種別ぶん作る。
    if by_cabinet and cabinet_options:
        template_url = next(iter(by_cabinet.values()))
        parts = urlsplit(template_url)
        base_query = dict(cleaned_query_pairs(template_url))
        for option in cabinet_options:
            key = str(option.get("key") or "").strip()
            value = str(option.get("value") or "").strip()
            if not key or not value or value in by_cabinet:
                continue
            query = dict(base_query)
            query.pop("CabinetName", None)
            query.pop("Cabinet", None)
            query[key] = value
            widened = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(sorted(query.items())), "")
            )
            by_cabinet[value] = canonicalize_template_url(widened)

    return [
        ListPage(
            title=f"全期間（{cabinet or '全種別'}）",
            year_label=WIDENED_PERIOD_LABEL,
            url=url,
            meeting_group="",
            auxiliary_docs=[],
        )
        for cabinet, url in sorted(by_cabinet.items())
    ]


def recent_or_widened(items: dict[str, ListPage]) -> tuple[list[ListPage], str]:
    """直近分しか無い一覧なら、期間を広げた一覧に差し替えて返す。"""
    if list_links_cover_recent_years_only(items):
        widened = widened_period_list_pages(items)
        if widened:
            print("[INFO] 直近分の一覧しか無いので、期間を全期間へ広げます", flush=True)
            return widened, DISCOVERY_SOURCE_FULL_PERIOD
        print("[INFO] 直近分の会議一覧しか見つかりませんでした", flush=True)
        return list(items.values()), DISCOVERY_SOURCE_RECENT
    return list(items.values()), DISCOVERY_SOURCE_RECENT


# 入口ページに会議一覧を置かず、閲覧メニューや会議名検索の先に置く
# テンプレートがある。どれも一覧へ辿り着く中継ページなので順に開く。
MENU_TEMPLATE_SELECTOR = ", ".join(
    (
        "a[href*='Template=perusal-' i]",
        "a[href*='Template=search-top' i]",
        "a[href*='Template=search-meeting' i]",
        "a[href*='Template=search-document' i]",
    )
)


def select_every_search_condition(form, deadline: float | None) -> None:
    """絞り込み条件をすべて選ぶ。

    会議種別をチェックボックスで選ばせる形（福岡市など）と、複数選択の
    リストで選ばせる形（鹿児島県など）がある。単一選択のときは既定の
    「選択なし」がそのまま全件を指すので触らない。
    """
    boxes = form.locator("input[type='checkbox']")
    try:
        total = boxes.count()
    except Exception:
        total = 0
    for box_index in range(total):
        ensure_discovery_time(deadline, f"検索条件 {box_index + 1}/{total}")
        try:
            boxes.nth(box_index).check(timeout=2_000)
        except Exception:
            continue

    selects = form.locator("select[multiple]")
    try:
        total = selects.count()
    except Exception:
        total = 0
    for select_index in range(total):
        ensure_discovery_time(deadline, f"検索条件の一覧 {select_index + 1}/{total}")
        select = selects.nth(select_index)
        options = select.locator("option")
        values: list[str] = []
        for option_index in range(options.count()):
            value = (options.nth(option_index).get_attribute("value") or "").strip()
            if value:
                values.append(value)
        if not values:
            continue
        try:
            select.select_option(values, timeout=5_000)
        except Exception:
            continue


def submit_all_conditions_search(
    page, menu_url: str, timeout_ms: int, deadline: float | None, items: dict[str, ListPage]
) -> bool:
    """検索フォームしか入口が無い取得元で、条件をすべて選んで送信する。

    送信結果の URL は別のセッションから開いても同じ一覧が返るので、
    そのまま全期間の会議一覧として扱える。
    """
    forms = page.locator("form")
    try:
        total_forms = forms.count()
    except Exception:
        return False

    # 送信先が一覧と分かるフォームを先に試す。取得元によっては送信先が
    # 入口 URL のままで、Template は hidden 側に入っている。
    order: list[int] = []
    for index in range(total_forms):
        form = forms.nth(index)
        try:
            if form.locator("input[type='checkbox'], select").count() == 0:
                continue
        except Exception:
            continue
        if "template=list" in (form.get_attribute("action") or "").lower():
            order.insert(0, index)
        else:
            order.append(index)

    for index in order:
        ensure_discovery_time(deadline, "検索フォームの送信")
        if page.url != menu_url:
            try:
                page.goto(menu_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except DiscoveryTimeoutError:
                raise
            except Exception:
                return False
        form = page.locator("form").nth(index)
        select_every_search_condition(form, deadline)
        try:
            form.locator("input[type='submit'], button[type='submit']").first.click(timeout=timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            continue
        # 「ことばで探す」のように一覧へ繋がらないフォームもあるので、
        # 実際に文書の行が出たときだけ会議一覧として採用する。
        if not extract_document_rows_from_page(page):
            continue
        list_url = canonicalize_template_url(page.url)
        title = normalize_space(page.title()) or "会議録一覧"
        # 送信結果を後から開き直せる取得元（福岡市など）は、URL だけ控えて
        # 後段に任せる。開き直すと結果が消える取得元（国分寺市など。CSRF
        # トークンがセッションに紐づく）は、この場で最後まで読み切る。
        documents: list[DocumentRow] | None = None
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
            reopened = bool(extract_document_rows_from_page(page))
        except DiscoveryTimeoutError:
            raise
        except Exception:
            reopened = False
        if not reopened:
            try:
                page.goto(menu_url, wait_until="domcontentloaded", timeout=timeout_ms)
                form = page.locator("form").nth(index)
                select_every_search_condition(form, deadline)
                form.locator("input[type='submit'], button[type='submit']").first.click(timeout=timeout_ms)
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                documents = collect_document_rows_from_open_list(page, timeout_ms, deadline)
            except DiscoveryTimeoutError:
                raise
            except Exception:
                documents = None
            if not documents:
                continue

        if list_url not in items:
            items[list_url] = ListPage(
                title=title,
                year_label=WIDENED_PERIOD_LABEL,
                url=list_url,
                meeting_group="",
                auxiliary_docs=[],
                documents=documents,
            )
        return True
    return False


def discover_via_menu_pages(
    page,
    timeout_ms: int,
    deadline: float | None,
    items: dict[str, ListPage],
    offered_types: list[str] | None = None,
) -> str:
    """閲覧メニュー・会議名検索を順に開いて会議一覧を探す。"""
    entry_url = page.url
    menu_urls: list[str] = []
    links = page.locator(MENU_TEMPLATE_SELECTOR)
    for index in range(links.count()):
        href = safe_href(links.nth(index))
        if not href:
            continue
        menu_url = canonicalize_template_url(urljoin(entry_url, href))
        if menu_url not in menu_urls:
            menu_urls.append(menu_url)

    source = DISCOVERY_SOURCE_LIBRARY
    for menu_index, menu_url in enumerate(menu_urls):
        ensure_discovery_time(deadline, f"メニュー {menu_index + 1}/{len(menu_urls)}")
        try:
            page.goto(menu_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except DiscoveryTimeoutError:
            raise
        except Exception:
            continue
        # 直近数年分しか並べないメニューがある（福岡県の search-top）。
        # それを拾って満足すると古い会議録に辿り着けない。期間つきの
        # リンクなら期間だけ広げれば同じ経路で全期間を辿れる。
        added = collect_template_list_links(page, items, deadline, "メニューのリンク")
        if added:
            if not list_links_cover_recent_years_only(items):
                continue
            cabinet_options = read_cabinet_options(page)
            record_offered_types(offered_types, cabinet_options)
            widened = widened_period_list_pages(items, cabinet_options)
            if widened:
                print(
                    "[INFO] 直近分の一覧しか無いので、期間を全期間へ広げます"
                    f"（会議種別 {len(widened)} 件）",
                    flush=True,
                )
                items.clear()
                items.update({page_item.url: page_item for page_item in widened})
                return DISCOVERY_SOURCE_FULL_PERIOD
            continue
        if submit_all_conditions_search(page, menu_url, timeout_ms, deadline, items):
            # 全条件での検索結果なので、収録範囲は全期間とみなせる。
            source = DISCOVERY_SOURCE_FULL_PERIOD
    return source


def discover_list_pages(
    page,
    target: dict,
    timeout_ms: int,
    deadline: float | None = None,
    offered_types: list[str] | None = None,
) -> tuple[list[ListPage], str]:
    ensure_discovery_time(deadline, "開始ページ")
    page.goto(str(target["source_url"]), wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    ensure_discovery_time(deadline, "検索ページ")
    search_library_url = find_search_library_url(page, str(target["source_url"]))
    page.goto(search_library_url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass
    items: dict[str, ListPage] = {}

    def finish_library(source: str) -> tuple[list[ListPage], str]:
        """年度別一覧を読み終えたあと、一覧に出てこない会議種別を補う。"""
        options = collect_cabinet_options(page, str(target["source_url"]), timeout_ms)
        record_offered_types(offered_types, options)
        extra = missing_cabinet_list_pages(items, options)
        if extra:
            print(
                f"[INFO] 一覧に出てこない会議種別 {len(extra)} 件を全期間で補います",
                flush=True,
            )
        return list(items.values()) + extra, source

    cells = page.locator("div.LibraryTable dl.cell")
    if cells.count() > 0:
        for cell_index in range(cells.count()):
            ensure_discovery_time(deadline, f"年度一覧 {cell_index + 1}/{cells.count()}")
            cell = cells.nth(cell_index)
            year_label = safe_inner_text(cell.locator("dt.cell__title").first) or "不明"
            collect_list_page_entries(page, cell.locator("dd.cell__item"), year_label, items)
        return finish_library(DISCOVERY_SOURCE_LIBRARY)

    cells = page.locator("div.LibraryTable dl")
    if cells.count() > 0:
        for cell_index in range(cells.count()):
            ensure_discovery_time(deadline, f"年度一覧 {cell_index + 1}/{cells.count()}")
            cell = cells.nth(cell_index)
            year_label = safe_inner_text(cell.locator("dt").first) or "不明"
            collect_list_page_entries(page, cell.locator("dd"), year_label, items)
        return finish_library(DISCOVERY_SOURCE_LIBRARY)

    # 新しい DBSR テンプレートは table--all を付けず table 系のクラスを使うが、
    # 自治体によってタグ名が異なる。クラス名は共通なのでタグ名は指定しない。
    #   香川県など:   ul.table  > li.table__cell      > dt.table__header + dd.table__item
    #   かほく市など: div.table > section.table__cell > h3.table__header + ul.table__item
    #                 （会議は ul.table__item > li.table__sub-item に入れ子になる）
    cells = page.locator(".table > .table__cell")
    if cells.count() == 0:
        cells = page.locator(".table__cell")
    for cell_index in range(cells.count()):
        ensure_discovery_time(deadline, f"年度一覧 {cell_index + 1}/{cells.count()}")
        cell = cells.nth(cell_index)
        year_label = safe_inner_text(cell.locator(".table__header:not(.visually-hidden)").first)
        if not year_label:
            year_label = safe_inner_text(cell.locator(".table__header").first) or "不明"
        # 入れ子テンプレートでは table__item が年度のまとまりなので、
        # そのまま渡すと 1 年度 = 1 会議に潰れてしまう。会議単位の
        # table__sub-item があるときはそちらを会議として数える。
        entries = cell.locator(".table__sub-item")
        if entries.count() == 0:
            entries = cell.locator(".table__item")
        collect_list_page_entries(page, entries, year_label, items)

    if items:
        return finish_library(DISCOVERY_SOURCE_LIBRARY)

    # 年度ごとのまとまりを組まず、会議一覧へのリンクをそのまま並べる
    # search-library がある（小金井市・福岡県など）。構造セレクタでは
    # 何も拾えないので、ページ内の一覧リンクを直接集める。
    if collect_template_list_links(page, items, deadline, "検索ページのリンク"):
        # ただし直近数年分しか並べない取得元がある（福岡県）。その分だけ
        # 取って完了と記録すると、公開されている古い会議録が抜けたまま
        # 「全部取れた」ように見える。期間指定つきのリンクしか無いときは、
        # 検索フォームから全期間を出せないか先に試す。
        if list_links_cover_recent_years_only(items):
            return recent_or_widened(items)
        return finish_library(DISCOVERY_SOURCE_LIBRARY)

    # search-library ページを持たないテンプレート（あきる野市・大野城市など）は
    # 入口ページ自体に会議一覧が並ぶ。この形では search-library が 404 になる。
    ensure_discovery_time(deadline, "入口ページ")
    page.goto(str(target["source_url"]), wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    # 入口ページの一覧は「直近４年分または12年分」しか出さない。それ以前は
    # 「くわしく検索」でしか辿れないので、まず期間を最大に広げた文書一覧を試す。
    start_year, end_year = detail_search_year_bounds(page)
    if start_year and end_year:
        full_url = full_period_list_url(str(target["source_url"]), start_year, end_year)
        ensure_discovery_time(deadline, "全期間一覧")
        try:
            page.goto(full_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass
            has_rows = bool(extract_document_rows_from_page(page))
        except DiscoveryTimeoutError:
            raise
        except Exception:
            has_rows = False
        if has_rows:
            print(f"[INFO] 全期間の文書一覧を使います（{start_year}年〜{end_year}年）", flush=True)
            return (
                [
                    ListPage(
                        title=f"{start_year}年〜{end_year}年",
                        year_label=f"{start_year}年〜{end_year}年",
                        url=full_url,
                        meeting_group="",
                        auxiliary_docs=[],
                    )
                ],
                DISCOVERY_SOURCE_FULL_PERIOD,
            )
        page.goto(str(target["source_url"]), wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

    # 全期間一覧が使えないときだけ、入口ページの「最近の会議録」を拾う。
    # ここで取れるのは直近分だけなので、収録範囲は complete とみなさない。
    groups = page.locator("ul.recent__links")
    for group_index in range(groups.count()):
        ensure_discovery_time(deadline, f"入口ページ一覧 {group_index + 1}/{groups.count()}")
        group = groups.nth(group_index)
        year_label = safe_inner_text(
            group.locator("xpath=preceding-sibling::*[contains(@class,'recent__lib-header')][1]")
        ) or "不明"
        collect_list_page_entries(page, group.locator("li"), year_label, items)

    if items:
        return recent_or_widened(items)

    # 入口ページに会議一覧へのリンクが直接並ぶ取得元がある（宇佐市・多摩市）。
    # search-library 側に一覧が無いだけなので、入口ページのリンクを拾う。
    if collect_template_list_links(page, items, deadline, "入口ページのリンク"):
        return recent_or_widened(items)

    # 入口ページにも一覧が無く、閲覧メニューや会議名検索の先にしか
    # 一覧を置かない取得元がある（碧南市・福岡市など）。
    menu_source = discover_via_menu_pages(page, timeout_ms, deadline, items, offered_types)
    if items:
        return list(items.values()), menu_source
    return [], DISCOVERY_SOURCE_LIBRARY


def list_url_with_origin(list_url: str, origin_base: str) -> str:
    joined = urljoin(origin_base, list_url)
    return canonicalize_template_url(joined)


def infer_day_title_from_held_on(held_on: str) -> str:
    match = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", held_on)
    if match:
        return f"{match.group(1)}月{match.group(2)}日"
    return held_on


def document_suffix(title: str) -> str:
    normalized = normalize_space(title)
    for suffix in ("本文", "名簿", "署名", "一般質問"):
        if suffix in normalized:
            return suffix

    tail = re.sub(r"^.*?[）)]\s*", "", normalized)
    return tail or "会議録"


def is_disabled(locator) -> bool:
    try:
        return locator.is_disabled(timeout=1_000)
    except Exception:
        aria_disabled = (locator.get_attribute("aria-disabled") or "").strip().lower()
        disabled = locator.get_attribute("disabled")
        return aria_disabled == "true" or disabled is not None


def extract_document_rows_from_page(page) -> list[DocumentRow]:
    rows: list[DocumentRow] = []
    # 行の中身（.ans-title__name / .ans-title__date）は共通だが、外側の
    # コンテナ名が取得元によって違う（東京都議会は .result__item）。
    items = page.locator("ul.result-document li.result-document__item")
    if items.count() == 0:
        items = page.locator(".result__item")
    for index in range(items.count()):
        item = items.nth(index)
        # 見出しのクラスが入れ物に付く取得元と、リンクそのものに付く
        # 取得元がある（八王子市は a.ans-title__name）。
        anchor = item.locator(".ans-title__name a").first
        if anchor.count() == 0:
            anchor = item.locator("a.ans-title__name").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        date_text = safe_inner_text(item.locator(".ans-title__date").first)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    if rows:
        return rows

    # ハイフン区切りのクラス名を使う取得元（広島県など）。
    #   div.result-list > div.result-document
    #     > span.result-document-date + a
    items = page.locator(".result-document:not(.result-document__item)")
    for index in range(items.count()):
        item = items.nth(index)
        anchor = item.locator("a").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        date_text = safe_inner_text(item.locator(".result-document-date").first)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    if rows:
        return rows

    # 同じ 2 つのクラスでも入れ子の向きが逆の取得元がある（山口市は
    # div.title > div.recordcol）。どちらでも行を拾えるようにする。
    items = page.locator("div.recordcol div.title")
    if items.count() == 0:
        items = page.locator("div.title div.recordcol")
    for index in range(items.count()):
        item = items.nth(index)
        anchor = item.locator("a").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        # 開催日をクラスなしの span で出す取得元がある（桜井市など）。
        # span.date が無いときは行全体の文字列から拾う。
        date_text = safe_inner_text(item.locator("span.date").first)
        if not date_text:
            date_text = safe_inner_text(item)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    if rows:
        return rows

    # ここまでのどのクラス構成にも当てはまらないテンプレート向けの受け皿。
    # 取得元ごとにクラス名や入れ子が違っても、文書リンクと同じ行に開催日が
    # 出る点は共通なので、リンクを起点に行を組み立てる。
    # 文書を開くテンプレート名は取得元で違う。1 発言ずつ出す doc-one-frame、
    # 全文を出す doc-all-frame（泉南市）、document のいずれか。
    anchors = page.locator(
        "a[href*='Template=doc-one-frame' i], a[href*='Template=doc-all-frame' i], "
        "a[href*='Template=document' i]"
    )
    seen_urls: set[str] = set()
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        href = safe_href(anchor)
        title = safe_inner_text(anchor)
        if not href or not title:
            continue
        absolute = canonicalize_template_url(urljoin(page.url, href))
        if absolute in seen_urls:
            continue
        # 直近の親 2 階層までを行とみなして開催日を探す。
        row_text = ""
        for depth in ("xpath=..", "xpath=../.."):
            row_text = safe_inner_text(anchor.locator(depth).first)
            if held_on_from_text(row_text):
                break
        held_on = held_on_from_text(row_text) or held_on_from_text(title)
        if not held_on:
            continue
        seen_urls.add(absolute)
        rows.append(DocumentRow(title=title, url=absolute, held_on=held_on))
    if rows:
        return rows

    # 文書一覧が document-list 形式のテンプレート（あきる野市・大野城市など）。
    # 表題に西暦が入らないので、開催日は .document-list__date の <time> から取る。
    items = page.locator(".document-list")
    for index in range(items.count()):
        item = items.nth(index)
        anchor = item.locator(".document-list__title a").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        date_text = safe_inner_text(item.locator(".document-list__date").first)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    if rows:
        return rows

    # 単文表示テンプレート（彦根市・石岡市など）。本文は doc-one-frame の
    # フレーム内にあり、開催日は .result-title__date に「開催日: YYYY-MM-DD」で入る。
    items = page.locator(".result-doc")
    for index in range(items.count()):
        item = items.nth(index)
        anchor = item.locator("a.result-title__name").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        date_text = safe_inner_text(item.locator(".result-title__date").first)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    return rows


def collect_list_page_documents(
    page,
    list_url: str,
    timeout_ms: int,
    *,
    known_urls: set[str] | None = None,
    stop_after_known_page: bool = False,
    deadline: float | None = None,
) -> list[DocumentRow]:
    ensure_discovery_time(deadline, list_url)
    page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass
    return collect_document_rows_from_open_list(
        page,
        timeout_ms,
        deadline,
        known_urls=known_urls,
        stop_after_known_page=stop_after_known_page,
    )


def collect_document_rows_from_open_list(
    page,
    timeout_ms: int,
    deadline: float | None = None,
    *,
    known_urls: set[str] | None = None,
    stop_after_known_page: bool = False,
) -> list[DocumentRow]:
    """いま開いている一覧を、ページ送りの終わりまで読む。"""
    collected: list[DocumentRow] = []
    seen_urls: set[str] = set()
    seen_page_signatures: set[tuple[str, int]] = set()

    while True:
        ensure_discovery_time(deadline, page.url)
        page_rows = extract_document_rows_from_page(page)
        signature = (page_rows[0].url if page_rows else page.url, len(page_rows))
        if signature in seen_page_signatures:
            break
        seen_page_signatures.add(signature)

        page_is_known = bool(page_rows) and bool(known_urls) and all(row.url in known_urls for row in page_rows)

        for row in page_rows:
            if row.url in seen_urls:
                continue
            seen_urls.add(row.url)
            collected.append(row)

        if stop_after_known_page and page_is_known:
            break

        # ページ送りを nav で囲む取得元と ul のまま置く取得元がある
        # （宮崎市など）。クラス名は共通なのでタグ名は指定しない。
        next_button = page.locator(".pagination button[aria-label='次のページ']").first
        if next_button.count() > 0 and not is_disabled(next_button):
            try:
                next_button.click(timeout=timeout_ms)
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=3_000)
                except Exception:
                    pass
                continue
            except Exception:
                break

        next_url = ""
        links = page.locator(".pagination a")
        for index in range(links.count()):
            link = links.nth(index)
            text = safe_inner_text(link)
            href = safe_href(link)
            if href and "次" in text:
                next_url = canonicalize_template_url(urljoin(page.url, href))
                break
        if next_url == "":
            break

        try:
            page.goto(next_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass
        except Exception:
            break

    return collected


def meeting_item_from_dict(payload: dict) -> MeetingItem | None:
    try:
        title = normalize_space(str(payload.get("title", "") or ""))
        url = str(payload.get("url", "") or "").strip()
        year_label = normalize_space(str(payload.get("year_label", "") or ""))
        if not title or not url:
            return None
        doc_urls = payload.get("doc_urls")
        if not isinstance(doc_urls, list):
            doc_urls = [url]
        return MeetingItem(
            title=title,
            url=url,
            year_label=year_label or "不明",
            meeting_group=payload.get("meeting_group"),
            list_url=payload.get("list_url"),
            held_on=payload.get("held_on"),
            doc_urls=[str(value) for value in doc_urls if str(value or "").strip()],
            doc_kind=payload.get("doc_kind"),
        )
    except Exception:
        return None


def load_previous_meeting_items(index_json: Path) -> list[MeetingItem]:
    loaded = gijiroku_storage.load_json(index_json, [])
    if not isinstance(loaded, list):
        return []
    items: list[MeetingItem] = []
    for entry in loaded:
        if not isinstance(entry, dict):
            continue
        item = meeting_item_from_dict(entry)
        if item is not None:
            items.append(item)
    return items


def meeting_merge_key(item: MeetingItem) -> tuple[str, str, str, str, str, str]:
    return (
        str(item.list_url or ""),
        str(item.held_on or ""),
        str(item.doc_kind or ""),
        str(item.url or ""),
        str(item.year_label or ""),
        str(item.title or ""),
    )


def merge_meeting_items(new_items: list[MeetingItem], previous_items: list[MeetingItem]) -> list[MeetingItem]:
    merged: list[MeetingItem] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for item in [*new_items, *previous_items]:
        key = meeting_merge_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def previous_doc_urls_by_list_url(previous_items: list[MeetingItem]) -> dict[str, set[str]]:
    urls_by_list_url: dict[str, set[str]] = {}
    for item in previous_items:
        list_url = str(item.list_url or "").strip()
        if not list_url:
            continue
        urls = urls_by_list_url.setdefault(list_url, set())
        for doc_url in item.doc_urls or []:
            if doc_url:
                urls.add(str(doc_url))
    return urls_by_list_url


def should_quick_update_from_state(state: dict) -> bool:
    summary = state.get("plan_summary")
    if not isinstance(summary, dict):
        return False
    try:
        missing_total = int(summary.get("missing_total") or 0)
    except Exception:
        return False
    return (
        missing_total == 0
        and bool(summary.get("source_order_trustworthy"))
        and str(summary.get("source_date_order") or "") == "descending"
        and str(summary.get("date_precision") or "") in {"day", "mixed"}
    )


def build_day_groups(list_page: ListPage, list_url: str, rows: list[DocumentRow]) -> list[DayDocumentGroup]:
    """1 つの一覧ページの文書を、開催日ごとの会議にまとめる。

    全期間の一覧から作ったページは年ラベルを持たないので、開催日から組み直す。
    「全期間」のままにすると、呼び出し側が (年ラベル, 会議種別, 表題) で重複を
    除くときに、同じ委員会の同じ月日が年をまたいで 1 件に潰れてしまう。
    """
    grouped: dict[str, list[DocumentRow]] = {}
    ordered_dates: list[str] = []
    for row in rows:
        if row.held_on not in grouped:
            grouped[row.held_on] = []
            ordered_dates.append(row.held_on)
        grouped[row.held_on].append(row)

    groups: list[DayDocumentGroup] = []
    for held_on in ordered_dates:
        doc_rows = grouped[held_on]
        body_rows = [row for row in doc_rows if "本文" in normalize_space(row.title)]
        chosen_rows = body_rows or doc_rows
        suffix = document_suffix(chosen_rows[0].title if chosen_rows else doc_rows[0].title)
        title = f"{infer_day_title_from_held_on(held_on)}－{suffix}"
        year_label = (
            era_year_label(held_on)
            if list_page.year_label == WIDENED_PERIOD_LABEL
            else list_page.year_label
        )
        groups.append(
            DayDocumentGroup(
                title=title,
                year_label=year_label,
                meeting_group=list_page.meeting_group,
                list_url=list_url,
                doc_urls=[row.url for row in chosen_rows],
                held_on=held_on,
            )
        )
    return groups


def title_from_heading_or_filename(page_html: str, fallback: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, flags=re.I | re.S)
    if match:
        text = html_to_text(match.group(1))
        if text:
            return text
    return fallback


def discover_meeting_items(
    page,
    target: dict,
    timeout_ms: int,
    max_meetings: int = 0,
    *,
    previous_items: list[MeetingItem] | None = None,
    quick_update: bool = False,
    discovery_timeout_seconds: int = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    coverage_report: dict | None = None,
) -> list[MeetingItem]:
    deadline = discovery_deadline(discovery_timeout_seconds)
    base_url = str(target["base_url"])
    offered_types: list[str] = []
    list_pages, discovery_source = discover_list_pages(
        page, target, timeout_ms, deadline, offered_types
    )
    print(f"[INFO] 会議一覧ページ {len(list_pages)} 件", flush=True)
    if coverage_report is not None:
        coverage_report.clear()
        coverage_report.update(
            {
                "list_page_count": len(list_pages),
                "failed_list_page_count": 0,
                "limit_reached": False,
                "discovery_source": discovery_source,
                "offered_meeting_types": list(offered_types),
            }
        )

    meetings: list[MeetingItem] = []
    seen_titles: set[tuple[str, str, str]] = set()
    known_urls_by_list_url = previous_doc_urls_by_list_url(previous_items or []) if quick_update else {}

    for list_index, list_page in enumerate(list_pages, start=1):
        ensure_discovery_time(deadline, f"{list_index}/{len(list_pages)} {list_page.title}")
        print(
            f"[INFO] 会議一覧を確認中 {list_index}/{len(list_pages)} {list_page.year_label} {list_page.meeting_group}",
            flush=True,
        )
        try:
            list_url = list_url_with_origin(list_page.url, base_url)
            if list_page.documents is not None:
                # 開き直せない一覧は探索時に読み切ってある。
                rows = list_page.documents
            else:
                rows = collect_list_page_documents(
                    page,
                    list_url,
                    timeout_ms,
                    known_urls=known_urls_by_list_url.get(list_url),
                    stop_after_known_page=quick_update,
                    deadline=deadline,
                )
        except DiscoveryTimeoutError:
            raise
        except Exception as exc:
            if coverage_report is not None:
                coverage_report["failed_list_page_count"] = int(
                    coverage_report.get("failed_list_page_count") or 0
                ) + 1
            print(f"[WARN] 会議一覧の確認に失敗: {list_page.title} ({exc})", flush=True)
            continue

        if discovery_source == DISCOVERY_SOURCE_FULL_PERIOD:
            # 全期間の一覧は会議種別も年度も混ざるので、表題から会議名を、
            # 開催日から年ラベルを組み直す。
            day_groups = build_full_period_day_groups(list_url, rows)
        else:
            day_groups = build_day_groups(list_page, list_url, rows)

        for group in day_groups:
            key = (group.year_label, group.meeting_group, group.title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            meetings.append(
                MeetingItem(
                    title=group.title,
                    url=group.doc_urls[0] if group.doc_urls else group.list_url,
                    year_label=group.year_label,
                    meeting_group=group.meeting_group,
                    list_url=group.list_url,
                    held_on=group.held_on,
                    doc_urls=group.doc_urls,
                    doc_kind="minutes",
                )
            )
            if max_meetings > 0 and len(meetings) >= max_meetings:
                if coverage_report is not None:
                    coverage_report["limit_reached"] = True
                return meetings

        for auxiliary_doc in list_page.auxiliary_docs:
            aux_title = normalize_space(auxiliary_doc.get("title", "")) or "補助資料"
            aux_url = str(auxiliary_doc.get("url", "")).strip()
            if not aux_url:
                continue
            title = f"{list_page.meeting_group}－{aux_title}"
            key = (list_page.year_label, list_page.meeting_group, title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            meetings.append(
                MeetingItem(
                    title=title,
                    url=aux_url,
                    year_label=list_page.year_label,
                    meeting_group=list_page.meeting_group,
                    list_url=list_url,
                    held_on=None,
                    doc_urls=[aux_url],
                    doc_kind="toc" if aux_title == "目次" else "aux",
                )
            )
            if max_meetings > 0 and len(meetings) >= max_meetings:
                if coverage_report is not None:
                    coverage_report["limit_reached"] = True
                return meetings

    if quick_update and previous_items:
        return merge_meeting_items(meetings, previous_items)
    return meetings


def extract_document_body(page_html: str) -> str:
    voice_paragraphs = re.findall(r'<p[^>]*class="[^"]*voice__text[^"]*"[^>]*>(.*?)</p>', page_html, flags=re.I | re.S)
    voice_sections = [html_to_text(fragment) for fragment in voice_paragraphs]
    voice_sections = [section for section in voice_sections if section]
    if voice_sections:
        return "\n\n".join(voice_sections).strip()

    preferred_patterns = [
        r"<pre[^>]*>(.*?)</pre>",
        r'<main[^>]*>(.*?)</main>',
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
    ]
    for pattern in preferred_patterns:
        match = re.search(pattern, page_html, flags=re.I | re.S)
        if not match:
            continue
        text = html_to_text(match.group(1))
        if text:
            return text

    return html_to_text(page_html)


def extract_document_heading(page_html: str) -> str:
    title = title_from_heading_or_filename(page_html, "")
    if title:
        return title
    text = html_to_text(page_html)
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    return lines[0] if lines else ""


def document_date_label(page_html: str, meeting_item: MeetingItem) -> str | None:
    explicit = japanese_date_label(meeting_item.year_label, meeting_item.held_on)
    if explicit:
        return explicit

    heading = extract_document_heading(page_html)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", heading)
    if match:
        return f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日"
    return None


# 単文表示テンプレートは本文をフレーム内で 1 発言ずつ出すため、文書 URL を
# 辿っても全文にならない。画面の「全文表示」と同じ download を使う。
def full_text_download_url(url: str) -> str:
    if "template=doc-one-frame" not in url.lower():
        return ""
    document_id = query_value(url, "DocumentID")
    if document_id == "":
        return ""
    parts = urlsplit(url)
    query = urlencode(
        [
            ("Template", "download"),
            ("Download", "yes"),
            ("VoiceType", "all"),
            ("DocumentID", document_id),
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def fetch_meeting_text(request_context, item: MeetingItem, timeout_ms: int) -> tuple[int, str]:
    if not item.doc_urls:
        return 0, ""

    sections: list[str] = []
    fragment_count = 0
    for doc_url in item.doc_urls:
        download_url = full_text_download_url(doc_url)
        if download_url:
            text = request_text(request_context, download_url, timeout_ms, referer=doc_url).strip()
            if text:
                sections.append(text)
                fragment_count += 1
            continue
        page_html = request_text(request_context, doc_url, timeout_ms, referer=item.list_url or item.url)
        body_text = extract_document_body(page_html)
        heading = extract_document_heading(page_html)
        section_lines: list[str] = []
        if heading and normalize_space(heading) != normalize_space(item.meeting_group or ""):
            section_lines.append(heading)
            section_lines.append("-" * min(max(len(normalize_space(heading)), 8), 40))
        if body_text:
            section_lines.append(body_text)
        section_text = "\n".join(section_lines).strip()
        if section_text:
            sections.append(section_text)
            fragment_count += 1

    if not sections:
        return 0, ""

    header_lines = [item.title]
    if item.meeting_group:
        header_lines.append(item.meeting_group)
    header_lines.append(item.year_label)

    sample_url = item.doc_urls[0]
    if full_text_download_url(sample_url):
        # download はテキストなので、日付は一覧から拾った開催日を使う。
        held_on_label = japanese_date_label(item.year_label, item.held_on) or item.held_on
    else:
        sample_html = request_text(request_context, sample_url, timeout_ms, referer=item.list_url or item.url)
        held_on_label = document_date_label(sample_html, item)
    if held_on_label:
        header_lines.append(f"開催日: {held_on_label}")
    header_lines.append(f"Source URL: {item.url}")
    if item.list_url and item.list_url != item.url:
        header_lines.append(f"List URL: {item.list_url}")
    header_lines.append("")
    header_lines.append("\n\n".join(sections).strip())
    return fragment_count, "\n".join(header_lines).strip() + "\n"


def save_debug_html(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    default_slug = gijiroku_targets.default_slug_for_system("dbsr")
    parser = argparse.ArgumentParser(
        description="dbsr / db-search / kaigiroku-indexphp 系の議会会議録一覧を巡回し、日程単位の本文テキストを保存します。"
    )
    parser.add_argument(
        "--slug",
        default=default_slug,
        help="自治体slug。data/municipalities から対象を解決します。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="取得データの保存先ディレクトリ（未指定時は slug 規約から自動決定）",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="ブラウザを表示して実行（デフォルトはヘッドレス）",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.5,
        help="各会議アクセス間の待機秒数（サーバー負荷軽減）",
    )
    parser.add_argument(
        "--max-meetings",
        type=int,
        default=0,
        help="処理件数上限（0は無制限）",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_WAIT_MS,
        help="Playwright / HTTP 操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--discovery-timeout-seconds",
        type=int,
        default=DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        help="会議一覧収集の最大秒数（0 は無制限）",
    )
    parser.add_argument(
        "--ack-robots",
        action="store_true",
        help="robots.txt・利用規約・許諾確認済みとして実行する",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="取得失敗時や調査用に HTML を保存する",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="既存の保存結果を無視して最初から取り直す",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="dbsr")

    if not args.ack_robots:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。")
        print(f"        robots.txt: {target['robots_txt_url']}")
        return 2

    output_dir: Path = (args.output_dir or target["work_dir"]).resolve()
    work_dir: Path = (args.output_dir or target["work_dir"]).resolve()
    downloads_dir = (
        (output_dir / "downloads").resolve()
        if args.output_dir is not None
        else Path(target["downloads_dir"]).resolve()
    )
    index_json = (
        (output_dir / "meetings_index.json").resolve()
        if args.output_dir is not None
        else Path(target["index_json_path"]).resolve()
    )
    pages_dir = work_dir / "pages"
    result_csv = work_dir / f"run_result_{now_ts()}.csv"
    state_path = work_dir / "scrape_state.json"
    state = gijiroku_storage.load_state(state_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if args.save_html:
        pages_dir.mkdir(parents=True, exist_ok=True)

    previous_source_coverage = state.get("source_coverage")
    previous_source_state = str(previous_source_coverage.get("state") or "").strip() \
        if isinstance(previous_source_coverage, dict) else ""
    if args.max_meetings <= 0 and index_json.exists() and previous_source_state != "complete":
        state["source_coverage"] = {
            "mode": "source_discovery_coverage",
            "state": "partial_planned",
            "discovered_count": len(load_previous_meeting_items(index_json)),
            "list_page_count": 0,
            "failed_list_page_count": 0,
            "limit": 0,
            "updated_at": now_ts(),
        }
        gijiroku_storage.save_state(state_path, state)

    print(f"[INFO] Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print(f"[INFO] Base URL: {target['base_url']}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headful)
        context = browser.new_context(accept_downloads=False, locale="ja-JP", user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        print("[INFO] 会議一覧を収集中...")
        previous_items = [] if args.no_resume or args.max_meetings > 0 else load_previous_meeting_items(index_json)
        quick_update = bool(previous_items) and should_quick_update_from_state(state)
        if quick_update:
            print(
                f"[INFO] Quick update listing enabled: previous_index={len(previous_items)}",
                flush=True,
            )
        coverage_report: dict[str, int | bool] = {}
        meeting_items = discover_meeting_items(
            page,
            target,
            args.timeout_ms,
            args.max_meetings,
            previous_items=previous_items,
            quick_update=quick_update,
            discovery_timeout_seconds=args.discovery_timeout_seconds,
            coverage_report=coverage_report,
        )
        print(f"[INFO] 会議候補 {len(meeting_items)} 件")
        if not meeting_items:
            raise RuntimeError(
                "会議候補を1件も取得できませんでした。"
                "取得元の画面構造変更または一時的な取得エラーとして扱います: "
                f"{target['source_url']}"
            )

        limit_reached = bool(coverage_report.get("limit_reached"))
        failed_list_page_count = max(0, int(coverage_report.get("failed_list_page_count") or 0))
        discovery_source = str(coverage_report.get("discovery_source") or "")
        if limit_reached:
            source_coverage_state = "partial_limit"
        elif failed_list_page_count > 0:
            source_coverage_state = "partial_error"
        elif discovery_source == DISCOVERY_SOURCE_RECENT:
            # 入口ページの「最近の会議録」は直近４年分または12年分しか出さない。
            # ここで取れた分を完了とみなすと、部分収録を完了と誤表示することになる。
            source_coverage_state = "partial_recent_only"
        else:
            source_coverage_state = "complete"
        state["source_coverage"] = {
            "mode": "source_discovery_coverage",
            "state": source_coverage_state,
            "discovered_count": len(meeting_items),
            "list_page_count": max(0, int(coverage_report.get("list_page_count") or 0)),
            "failed_list_page_count": failed_list_page_count,
            "discovery_source": discovery_source,
            "offered_meeting_types": list(coverage_report.get("offered_meeting_types") or []),
            "limit": max(0, int(args.max_meetings)),
            "updated_at": now_ts(),
        }
        gijiroku_storage.save_state(state_path, state)

        index_json.parent.mkdir(parents=True, exist_ok=True)
        index_json.write_text(
            json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        emit_progress(0, len(meeting_items), state_path, state)

        with result_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["title", "year", "url", "status", "output", "error", "documents", "fragments"],
            )
            writer.writeheader()

            planned_items = [
                gijiroku_planning.attach_text_output(plan)
                for plan in gijiroku_planning.build_base_plans(meeting_items, downloads_dir)
            ]
            previous_missing = gijiroku_planning.previous_missing_count(state)
            planned_items, work_items, missing_count = gijiroku_planning.select_work_items(
                planned_items,
                no_resume=args.no_resume,
                previous_missing_count=previous_missing,
            )
            date_range = gijiroku_planning.describe_date_range(planned_items)
            if date_range:
                print(f"[INFO] Discovered meeting date range: {date_range}", flush=True)
            gijiroku_planning.save_plan_summary(state_path, state, planned_items, missing_count, previous_missing)
            if missing_count > 0:
                work_mode = gijiroku_planning.work_mode_label(missing_count, previous_missing)
                if work_mode == "update_check":
                    print(f"[INFO] Update check found new outputs: {missing_count}/{len(planned_items)}", flush=True)
                else:
                    print(f"[INFO] Resume missing outputs first: {missing_count}/{len(planned_items)}", flush=True)
            if not args.no_resume and not work_items:
                print("[INFO] All expected outputs already exist; skipping download loop.", flush=True)
                emit_progress(len(meeting_items), len(meeting_items), state_path, state)

            for idx, plan in enumerate(work_items, start=1):
                item = plan["item"]
                print(f"[{idx}/{len(work_items)}] {item.year_label} {item.title}")
                status = ""
                output_path = ""
                error_msg = ""
                document_count = len(item.doc_urls or [])
                fragment_count = 0
                year_dir_name = plan["year_dir_name"]
                meeting_group_dir = plan["meeting_group_dir"]
                stem = plan["stem"]
                resume_key = plan["resume_key"]
                dest_base = plan["dest_base"]
                existing_output = plan["existing_output"]

                if not args.no_resume and existing_output is not None:
                    output_path = str(existing_output)
                    status = "skipped_existing"
                    state["items"][resume_key] = {
                        "title": item.title,
                        "year_label": item.year_label,
                        "url": item.url,
                        "status": "saved_text",
                        "output_rel_path": str(existing_output.relative_to(downloads_dir)),
                        "updated_at": now_ts(),
                    }
                    gijiroku_storage.save_state(state_path, state)
                    writer.writerow(
                        {
                            "title": item.title,
                            "year": item.year_label,
                            "url": item.url,
                            "status": status,
                            "output": output_path,
                            "error": "",
                            "documents": len(item.doc_urls or []),
                            "fragments": 0,
                        }
                    )
                    handle.flush()
                    emit_progress(len(meeting_items) - len(work_items) + idx, len(meeting_items), state_path, state)
                    continue

                try:
                    fragment_count, meeting_text = fetch_meeting_text(context.request, item, args.timeout_ms)
                    if not meeting_text:
                        status = "not_found"
                    else:
                        dest = gijiroku_storage.write_text(dest_base, meeting_text, compress=True)
                        output_path = str(dest)
                        status = "saved_text"
                except PlaywrightTimeoutError as exc:
                    status = "timeout"
                    error_msg = str(exc)
                except Exception as exc:
                    status = "error"
                    error_msg = str(exc)
                    if args.save_html and item.doc_urls:
                        debug_path = pages_dir / year_dir_name
                        if meeting_group_dir:
                            debug_path = debug_path / meeting_group_dir
                        try:
                            sample_html = request_text(context.request, item.doc_urls[0], args.timeout_ms, referer=item.url)
                            gijiroku_storage.write_text(
                                debug_path / (stem + ".html"),
                                sample_html,
                                compress=True,
                            )
                        except Exception:
                            pass

                state["items"][resume_key] = {
                    "title": item.title,
                    "year_label": item.year_label,
                    "url": item.url,
                    "status": status,
                    "output_rel_path": str(Path(output_path).relative_to(downloads_dir)) if output_path else "",
                    "updated_at": now_ts(),
                }
                gijiroku_storage.save_state(state_path, state)

                writer.writerow(
                    {
                        "title": item.title,
                        "year": item.year_label,
                        "url": item.url,
                        "status": status,
                        "output": output_path,
                        "error": error_msg,
                        "documents": document_count,
                        "fragments": fragment_count,
                    }
                )
                handle.flush()
                emit_progress(len(meeting_items) - len(work_items) + idx, len(meeting_items), state_path, state)
                if args.delay_seconds > 0 and idx < len(work_items):
                    time.sleep(args.delay_seconds)

            downloaded_count = 0
            status_counts: dict[str, int] = {}
            for plan in planned_items:
                if gijiroku_storage.existing_output(plan["dest_base"]) is not None:
                    downloaded_count += 1
                    continue
                item_state = state.get("items", {}).get(plan["resume_key"], {})
                status = str(item_state.get("status") or "").strip() or "not_found"
                status_counts[status] = status_counts.get(status, 0) + 1
            validation = gijiroku_storage.apply_classified_scrape_validation(
                state_path,
                state,
                discovered_count=len(meeting_items),
                downloaded_count=downloaded_count,
                status_counts=status_counts,
            )
            emit_progress(
                int(validation["progress_current"]),
                int(validation["progress_total"]),
                state_path,
                state,
            )

        browser.close()

    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
