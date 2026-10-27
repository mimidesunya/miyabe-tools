#!/usr/bin/env python3
"""包括外部監査の報告書を取得して保存する。

台帳 `data/municipalities/gaibu_kansa_system_urls.tsv` の入口ページ（と、その
1〜2 階層下の年度別ページ）から、包括外部監査の**報告書・概要版・結果に基づく
措置**の PDF を探して取り、`work/kansa/<slug>/` に置く。

    work/kansa/<slug>/
        manifest.json          文書ごとの原典 URL・年度・種別・SHA・取得日時
        source/<doc_id>.pdf    取得した PDF そのもの
        text/<doc_id>.txt.gz   抜き出した本文（頭に題名・年度・出典の行）

**原典の URL と、それを見つけたページを必ず残す。** 検索に載せたとき、
利用者が元の報告書へ戻れるようにするため（会議録・例規と同じ方針）。

包括外部監査ではないもの（定期監査・決算審査・住民監査請求・指導監査・
内部統制など）は取らない。同じ監査委員のページに並んでいることが多いので、
リンクの文字だけでなく、そのリンクが置かれた見出しも見て判断する。
**個別外部監査は別の制度**なので取らず、見つけた数だけ manifest に残す。

取得元への配慮:

- 1 件ごとに間隔を空ける（既定 1.5 秒）。
- 一度取った文書は、既定で 30 日は問い合わせ直さない。問い合わせるときも
  ETag / Last-Modified で条件付きにし、変わっていなければ保存し直さない。
- 同じホストで接続を続けて切られたら、そのホストを数時間休む
  （例規の d1-law で弾かれた経験から。記録は自治体をまたいで共有する）。

取得元から消えた文書は「消えた」と記録し、保存物は消さない。

    python tools/kansa/scrape_kansa_reports.py --slug 01100-sapporo-shi
    python tools/kansa/scrape_kansa_reports.py --all --limit 5 --out-dir /tmp/kansa
    python tools/kansa/scrape_kansa_reports.py --code 13000 --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import heapq
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data" / "municipalities"
REGISTRY = DATA_ROOT / "gaibu_kansa_system_urls.tsv"
DEFAULT_WORK_ROOT = WORKSPACE_ROOT / "work" / "kansa"
sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))

from discover_kansa_urls import (  # noqa: E402
    INDIVIDUAL_WORDS,
    NEGATIVE_WORDS,
    STRONG_WORDS,
)
# 取得・保存・年度・リンクの部品は事務事業評価の取得と共通（tools/document_crawl.py）。
# テストと呼び出し側が使う名前は、ここからも引けるように取り込んでおく。
from document_crawl import (  # noqa: E402,F401
    DEFAULT_DELAY_SECONDS,
    DEFAULT_REFRESH_DAYS,
    JST,
    MIN_TEXT_CHARS,
    Fetcher,
    HostBlocked,
    HostGuard,
    Link,
    Target,
    clean_label,
    doc_id_for,
    fetch_document,
    fiscal_year,
    load_manifest,
    mark_missing,
    note_failure,
    note_fetched,
    now_text,
    page_links,
    page_title,
    same_organization,
    text_document,
    write_bytes,
    write_json,
)
from document_crawl import extract_pdf_text as extract_text  # noqa: E402
from document_crawl import load_targets as load_registry_targets  # noqa: E402

MANIFEST_VERSION = 1

# 1 自治体で開くページの上限。入口 → 年度別 → 報告書ページ、の 2 階層で足りる。
DEFAULT_MAX_PAGES = 30
DEFAULT_MAX_DEPTH = 2
# 1 自治体で取る文書の上限。これを超えるなら取り違えを疑って取らない。
# 報告書を章ごとに分けて 20 年以上置く団体がある（群馬県 354・盛岡市 291・
# 仙台市 260・奈良市 234・京都府 220。中身を見て全部が報告書の部品と確認）。
DEFAULT_MAX_DOCUMENTS = 600
# 1 ファイルの上限。全文は 10MB 台がある（久留米市 13.8MB）。
MAX_DOCUMENT_BYTES = 120 * 1024 * 1024

# 添付として置かれる PDF。拡張子の無い配信口は、応答の実体で確かめる。
PDF_URL_RE = re.compile(r"\.pdf(?:$|[?#])", re.I)
DOWNLOAD_SCRIPT_RE = re.compile(r"/(?:dl|download|file|files|fileoutput|attach(?:ment)?)\.(?:php|aspx?|cgi|do|jsp)$", re.I)
DOWNLOAD_DIRECTORY_RE = re.compile(r"/(?:dl|download|file|files|fileoutput|attach(?:ment)?)/?$", re.I)
PDF_LABEL_RE = re.compile(r"PDF", re.I)
ASSET_RE = re.compile(r"\.(jpg|jpeg|png|gif|svg|css|js|zip|ico|mp4|mp3|xlsx?|docx?|csv|txt)(?:$|\?)", re.I)
# リンク文字の末尾に付く「（PDF：1.2MB）」などの注記。
FILE_NOTE_RE = re.compile(
    r"[（(\[［【]\s*(?:PDF|ＰＤＦ)[^）)\]］】]*[）)\]］】]|(?:PDF|ＰＤＦ)\s*(?:ファイル|形式)?\s*[：:／/]?\s*[\d.,]+\s*[KMG]?B"
    r"|[（(]\s*[\d.,]+\s*(?:キロバイト|メガバイト|KB|MB)\s*[）)]",
    re.I,
)

# 種別。措置を先に見る（「監査の結果に基づく措置」は「結果」も含む）。
MEASURE_WORDS = ("措置", "講じた", "対応状況")
SUMMARY_WORDS = ("概要", "要旨", "ダイジェスト", "サマリー", "要約")
# 監査結果を公表した告示・お知らせ。報告書そのものではない。
NOTICE_WORDS = ("公表について", "の公表", "公告", "報道発表", "報道提供", "記者発表", "公表第")
REPORT_LABEL_RE = re.compile(r"報告書|全文|本文|監査の結果|監査結果|第\s*[0-9０-９一二三四五六七八九十]+|[PpＰ]\s*\d")
CHAPTER_RE = re.compile(r"^(第\s*[0-9０-９一二三四五六七八九十]+\s*[章部編]|表紙|目次)")
# 報告書でないと分かる添付。様式や公募の案内は取らない。
NOT_REPORT_WORDS = (
    "様式", "申請", "応募", "募集", "公募", "仕様書", "入札", "契約書", "要綱",
    "要領", "チラシ", "パンフレット", "アンケート",
    # 制度の説明資料（愛知県「参考資料（外部監査制度について）」）。
    "制度について",
)
# 年度別ページ・報告書ページへ降りるときの手掛かり。
DESCEND_WORDS = STRONG_WORDS + ("外部監査", "監査結果", "結果報告", "報告書")

HOUKATSU_URL_RE = re.compile(r"houkatsu|hokatsu|houkatu|hokatu", re.I)
ORDINANCE_TITLE_RE = re.compile(r"(条例|規則)$")


# ─────────────────────────────────────
# 判定
# ─────────────────────────────────────

def document_kind(label: str, heading: str = "") -> str:
    """報告書・概要版・措置・公表のどれか。

    報告書を章ごとに分けて置く自治体がある（札幌市「第1章 外部監査の概要」）。
    章は報告書の一部で、概要版ではない。章の見出しを持つものは報告書にする。
    """
    stripped = label.strip()
    if any(word in label for word in MEASURE_WORDS):
        return "measure"
    if CHAPTER_RE.match(stripped) or stripped.startswith("本文"):
        return "report"
    if any(word in label for word in NOTICE_WORDS):
        return "notice"
    if any(word in label for word in SUMMARY_WORDS):
        return "summary"
    # リンク文字に手掛かりが無いときだけ、置かれた見出しで決める（群馬県は
    # 見出しに「措置」を含むページに報告書の章が並んでいた）。
    if REPORT_LABEL_RE.search(label):
        return "report"
    if any(word in heading for word in MEASURE_WORDS):
        return "measure"
    if any(word in heading for word in SUMMARY_WORDS) and not any(w in heading for w in ("報告書", "結果")):
        return "summary"
    return "report"


def strip_file_note(label: str) -> str:
    return clean_label(FILE_NOTE_RE.sub("", label)).strip(" 　・-")


def has_strong(text: str) -> bool:
    return any(word in text for word in STRONG_WORDS)


def has_negative(text: str) -> bool:
    return any(word in text for word in NEGATIVE_WORDS)


def has_individual(text: str) -> bool:
    return any(word in text for word in INDIVIDUAL_WORDS)


# ファイル名のローマ字で分かる別の監査。茨城県は「令和4年度第2回監査結果」の
# ような素のリンク文字で、ファイル名だけが teikikansa（定期監査）を名乗っていた。
OTHER_AUDIT_FILENAME_RE = re.compile(
    r"teiki|zuiji|gyousei-?kansa|gyoseikansa|kessan|reigetsu|shido|sidou|naibu-?tousei|zaiseienjo|kouji-?kansa",
    re.I,
)
INDIVIDUAL_FILENAME_RE = re.compile(r"kobetsu|kobetu", re.I)


def classify_link(label: str, url: str, heading: str, page_is_houkatsu: bool) -> str:
    """添付リンクを仕分ける。

    戻り値: `houkatsu`（取る）/ `individual`（個別外部監査。数えるだけ）/
    `negative`（別の監査）/ `unrelated`（様式など）/ `unknown`（手掛かり無し）。

    リンクの文字に加えて、そのリンクが置かれた見出しを見る。監査委員の
    ページは定期監査と包括外部監査を見出しで分けて並べることが多く、
    リンクの文字が「令和6年度 報告書」だけでは区別がつかない。
    """
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    own = f"{label} {filename}"
    strong = has_strong(own) or bool(HOUKATSU_URL_RE.search(filename))
    if not strong and (has_individual(own) or INDIVIDUAL_FILENAME_RE.search(filename)):
        return "individual"
    if any(word in label for word in NOT_REPORT_WORDS):
        return "unrelated"
    # 条例・規則の本文（江戸川区「…外部監査契約に基づく監査に関する条例」）。
    # 報告書の題名にも条例の名前は出るので、末尾が条例・規則のものだけを除く。
    if ORDINANCE_TITLE_RE.search(strip_file_note(label).strip()):
        return "unrelated"
    if strong:
        return "houkatsu"
    if has_negative(own) or OTHER_AUDIT_FILENAME_RE.search(filename):
        return "negative"
    # リンク自体に手掛かりが無い。置かれた見出しで決める。
    if heading:
        if has_individual(heading) and not has_strong(heading):
            return "individual"
        if has_strong(heading):
            return "houkatsu"
        if has_negative(heading):
            return "negative"
        if heading_names_external_audit(heading):
            # 監査委員の総合ページで、外部監査の見出しの下に並ぶ文書（長野県）。
            return "houkatsu"
    return "houkatsu" if page_is_houkatsu else "unknown"


def is_pdf_link(url: str, label: str) -> bool:
    path = urlsplit(url).path
    if PDF_URL_RE.search(path):
        return True
    if ASSET_RE.search(path):
        return False
    # リンク文字に「（PDF 10.1MB）」の注記がある。東京都は
    # `/documents/d/kansa/r7houkatsu_zenbun` のように拡張子も配信口の形も
    # 持たない URL で配る。取得したところで実体を確かめる。
    if FILE_NOTE_RE.search(label):
        return True
    # 拡張子の無い配信口。リンク文字が PDF を名乗るときだけ候補にし、
    # 取得したところで実体を確かめる。
    parts = urlsplit(url)
    endpoint = bool(DOWNLOAD_SCRIPT_RE.search(parts.path)) or (
        bool(parts.query) and bool(DOWNLOAD_DIRECTORY_RE.search(parts.path))
    )
    return endpoint and bool(PDF_LABEL_RE.search(label))


def looks_like_pdf(content_type: str, content_disposition: str, raw: bytes) -> bool:
    if raw[:5] == b"%PDF-":
        return True
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if media_type in ("application/pdf", "application/x-pdf"):
        return True
    return ".pdf" in str(content_disposition or "").lower()


def link_names_houkatsu(link: Link) -> bool:
    """リンク自体が包括外部監査を名乗るか（文字・URL・置かれた見出し）。"""
    return has_strong(link.label) or bool(HOUKATSU_URL_RE.search(link.url)) or has_strong(link.heading)


def descend_priority(link: Link, page_is_houkatsu: bool, may_descend: bool = False) -> int:
    """年度別ページ・報告書ページへ降りる優先度。0 は降りない。

    全体メニューが大きいサイトでは、ページ上の順に辿ると別の監査の
    年度ページ（東京都の「監査措置 令和7年」）でページ数の上限を使い切る。
    包括外部監査を名乗るリンクから先に見る。
    """
    if has_individual(link.label) and not has_strong(link.label):
        return 0
    if has_negative(link.label) and not has_strong(link.label):
        return 0
    if has_strong(link.label) or HOUKATSU_URL_RE.search(link.url):
        return 100
    year, _ = fiscal_year(link.label)
    if year is not None and has_strong(link.heading):
        return 60
    if not page_is_houkatsu and not may_descend:
        return 0
    if year is not None and link.heading:
        # 見出しの下に並ぶ年度リンク。見出しの無い全体メニューの年度は後回し。
        return 30
    text = f"{link.label} {link.heading}"
    if any(word in text for word in DESCEND_WORDS):
        return 20
    if year is not None:
        return 5
    return 0


# ─────────────────────────────────────
# 台帳
# ─────────────────────────────────────

def load_targets(include_review: bool = False) -> list[Target]:
    return load_registry_targets(REGISTRY, include_review=include_review)


# ─────────────────────────────────────
# 取得
# ─────────────────────────────────────

def check_pdf_response(response, raw: bytes) -> None:
    """取った応答が PDF でなければ ValueError（「見つかりません」の HTML を保存しない）。"""
    if not looks_like_pdf(response.headers.get("Content-Type", ""),
                          response.headers.get("Content-Disposition", ""), raw):
        raise ValueError(f"PDF ではない応答: {response.headers.get('Content-Type', '')!r}")


@dataclass
class Candidate:
    url: str
    label: str
    page_url: str
    heading: str
    page_title: str


@dataclass
class RunResult:
    slug: str
    pages: int = 0
    found: int = 0
    downloaded: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_recent: int = 0
    failed: int = 0
    needs_ocr: int = 0
    missing: int = 0
    individual: int = 0
    negative: int = 0
    entry_opened: bool = False
    year_min: int | None = None
    year_max: int | None = None
    kinds: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    status: str = "ok"


def entry_names_external_audit(title: str) -> bool:
    """入口の題名が外部監査を名乗るか（包括とは書いていない）。

    年度ごとにページを分けるサイトは、一覧の題名が「外部監査の結果及び措置状況」
    のように「包括」を書かない（青森市・岐阜市・一宮市）。この入口からは
    年度別ページへ降りてよい。降りた先で取るのは、包括外部監査を名乗る PDF と、
    外部監査の見出しの下にある PDF だけ。
    """
    return "外部監査" in title and not has_negative(title)


def entry_is_external_audit_only(title: str) -> bool:
    """入口が外部監査だけのページか。そうなら素のリンク文字も拾ってよい。

    岐阜市「外部監査報告（平成11年度～）」・滋賀県「外部監査」は、報告書の
    リンク文字が「報告書 下水道事業の…」「令和8年3月16日別冊」で、見出しも
    「令和7年度」だけだった。監査委員の総合ページ（茨城県）と違って、ここに
    並ぶのは外部監査の文書だけなので、ページ全体を文脈にする。
    題名に「個別」が入る（京都市「外部監査（個別・包括）」）ときは混ざるので除く。
    """
    return entry_names_external_audit(title) and not has_individual(title) and "個別" not in title


def heading_names_external_audit(heading: str) -> bool:
    """見出しが外部監査の区画か（「外部監査人による外部監査」長野県）。"""
    return (
        "外部監査" in heading
        and not has_individual(heading)
        and "個別" not in heading
        and not has_negative(heading)
    )


def entry_is_houkatsu(url: str, title: str, html: str) -> bool:
    """入口ページそのものが包括外部監査のページか。

    台帳の入口には、監査委員の総合ページ（定期監査・随時監査・行政監査と
    並んで包括外部監査がある）も入っている。そこで素のリンク文字
    「令和4年度第2回監査結果」を全部拾うと別の監査を取る（茨城県）。
    題名か URL が包括外部監査を名乗るときだけ、ページ全体を文脈にする。
    そうでなければ、見出しかリンク自体が名乗るものだけを取る。
    """
    if has_strong(title) or HOUKATSU_URL_RE.search(urlsplit(url).path):
        return True
    return False


def collect_candidates(fetcher: Fetcher, target: Target, *, max_pages: int, max_depth: int,
                       result: RunResult) -> tuple[list[Candidate], list[dict]]:
    """入口から辿って、取る PDF の候補と個別外部監査の印を集める。"""
    entry_host = (urlsplit(target.url).hostname or "").lower()
    order = 0
    # (-優先度, 順番, URL, 深さ, 包括外部監査のリンクから来たか)
    queue: list[tuple[int, int, str, int, bool]] = [(-1000, order, target.url, 0, True)]
    queued: set[str] = {target.url}
    visited: set[str] = set()
    candidates: dict[str, Candidate] = {}
    individual: dict[str, dict] = {}
    while queue and result.pages < max_pages:
        _priority, _order, url, depth, via_houkatsu = heapq.heappop(queue)
        if url in visited:
            continue
        visited.add(url)
        try:
            fetched = fetcher.html(url)
        except HostBlocked:
            raise
        except requests.RequestException as exc:
            result.pages += 1
            prefix = "入口を開けません" if depth == 0 else "page"
            result.errors.append(f"{prefix} {url}: {type(exc).__name__}")
            continue
        result.pages += 1
        if fetched is None:
            if depth == 0:
                result.errors.append(f"入口を開けません: {url}")
            continue
        if depth == 0:
            result.entry_opened = True
        final_url, html = fetched
        visited.add(final_url)
        title = page_title(html)
        if has_negative(title) and not has_strong(title) and depth > 0:
            continue
        # 入口は台帳で確かめた包括外部監査の入口。降りた先は、包括外部監査を
        # 名乗るリンクから来たか、ページの題名で決める。ただの年度リンクから
        # 来たページは文脈を引き継がない（別の監査の年度ページがある）。
        if depth == 0:
            page_is_houkatsu = (
                entry_is_houkatsu(final_url, title, html) or entry_is_external_audit_only(title)
            )
            may_descend = entry_names_external_audit(title)
        else:
            page_is_houkatsu = via_houkatsu or has_strong(title)
            may_descend = False
        for link in page_links(final_url, html):
            link_host = (urlsplit(link.url).hostname or "").lower()
            if not same_organization(entry_host, link_host):
                continue
            if is_pdf_link(link.url, link.label):
                verdict = classify_link(link.label, link.url, link.heading, page_is_houkatsu)
                if verdict == "houkatsu":
                    candidates.setdefault(link.url, Candidate(
                        url=link.url, label=link.label, page_url=final_url,
                        heading=link.heading, page_title=title,
                    ))
                elif verdict == "individual":
                    individual.setdefault(link.url, {
                        "source_url": link.url, "title": strip_file_note(link.label), "page_url": final_url,
                    })
                elif verdict == "negative":
                    result.negative += 1
                continue
            if depth >= max_depth or link.url in visited or link.url in queued:
                continue
            priority = descend_priority(link, page_is_houkatsu, may_descend)
            if priority <= 0:
                continue
            order += 1
            queued.add(link.url)
            heapq.heappush(queue, (-priority, order, link.url, depth + 1, link_names_houkatsu(link)))
    return list(candidates.values()), list(individual.values())


def scrape_one(target: Target, fetcher: Fetcher, work_root: Path, *, max_pages: int = DEFAULT_MAX_PAGES,
               max_depth: int = DEFAULT_MAX_DEPTH, max_documents: int = DEFAULT_MAX_DOCUMENTS,
               refresh_days: int = DEFAULT_REFRESH_DAYS, dry_run: bool = False,
               max_fetch: int = 0) -> RunResult:
    work_dir = work_root / target.slug
    result = RunResult(slug=target.slug)
    started_at = now_text()
    manifest = load_manifest(work_dir)
    documents: dict[str, dict] = dict(manifest.get("documents") or {})
    try:
        candidates, individual = collect_candidates(
            fetcher, target, max_pages=max_pages, max_depth=max_depth, result=result,
        )
    except HostBlocked as exc:
        result.status = "host_blocked"
        result.errors.append(f"ホストを休止中: {exc}")
        candidates, individual = [], []
    result.found = len(candidates)
    result.individual = len(individual)

    if len(candidates) > max_documents:
        # 年 1〜数本の報告書で上限を超えるのは、取り違えている徴候。取らずに残す。
        result.status = "too_many_documents"
        result.errors.append(f"候補が {len(candidates)} 件あり上限 {max_documents} を超えたため取得しません")
        candidates = []

    # 新しい年度から扱う。取得件数を絞ったときに最新の報告書が残るように。
    candidates.sort(
        key=lambda c: fiscal_year(c.label, c.heading, c.page_title, c.url)[0] or 0,
        reverse=True,
    )
    for candidate in candidates:
        year, _ = fiscal_year(candidate.label, candidate.heading, candidate.page_title, candidate.url)
        if year is not None:
            result.year_min = year if result.year_min is None else min(result.year_min, year)
            result.year_max = year if result.year_max is None else max(result.year_max, year)
        kind = document_kind(candidate.label, candidate.heading)
        result.kinds[kind] = result.kinds.get(kind, 0) + 1

    # 取得元に今も載っている文書。件数を絞っても「消えた」とは数えない。
    listed_ids = {doc_id_for(c.url) for c in candidates}
    if max_fetch > 0:
        candidates = candidates[:max_fetch]

    if dry_run:
        for candidate in candidates:
            year, _ = fiscal_year(candidate.label, candidate.heading, candidate.page_title, candidate.url)
            print(f"  [{year or '----'}] {document_kind(candidate.label, candidate.heading)} "
                  f"{strip_file_note(candidate.label)} <{candidate.url}>", flush=True)
        return result

    now = datetime.now(JST)
    seen_ids: set[str] = set()
    for candidate in candidates:
        doc_id = doc_id_for(candidate.url)
        seen_ids.add(doc_id)
        entry = dict(documents.get(doc_id) or {})
        year, year_label = fiscal_year(candidate.label, candidate.heading, candidate.page_title, candidate.url)
        title = strip_file_note(candidate.label) or strip_file_note(candidate.heading) or candidate.page_title
        entry.update({
            "source_url": candidate.url,
            "page_url": candidate.page_url,
            "page_title": candidate.page_title,
            "heading": candidate.heading,
            "title": title,
            "fiscal_year": year,
            "year_label": year_label,
            "kind": document_kind(candidate.label, candidate.heading),
        })
        entry.setdefault("first_seen_at", now_text())
        entry.pop("missing_since", None)

        try:
            fetched = fetch_document(
                fetcher, candidate.url, entry, work_dir, now=now, refresh_days=refresh_days,
                max_bytes=MAX_DOCUMENT_BYTES, check=check_pdf_response,
            )
        except HostBlocked as exc:
            result.status = "host_blocked"
            result.errors.append(f"ホストを休止中: {exc}")
            documents[doc_id] = entry
            break
        if fetched.state == "recent":
            result.skipped_recent += 1
            documents[doc_id] = entry
            continue
        if fetched.state == "not_modified":
            entry["checked_at"] = now_text()
            result.unchanged += 1
            documents[doc_id] = entry
            continue
        if fetched.state == "failed":
            result.failed += 1
            note_failure(entry, fetched.error)
            result.errors.append(f"{candidate.url}: {type(fetched.error).__name__}")
            documents[doc_id] = entry
            continue

        raw = fetched.raw
        sha256, same = note_fetched(entry, fetched)
        if same:
            result.unchanged += 1
            documents[doc_id] = entry
            continue

        had_previous = bool(fetched.file_ok and entry.get("sha256"))
        relative_pdf = f"source/{doc_id}.pdf"
        relative_text = f"text/{doc_id}.txt.gz"
        write_bytes(work_dir / relative_pdf, raw)
        body, pages = extract_text(raw)
        entry.update({
            "file": relative_pdf,
            "sha256": sha256,
            "bytes": len(raw),
            "content_type": fetched.headers.get("Content-Type", ""),
            "fetched_at": now_text(),
            "pages": pages,
            "text_chars": len(body),
        })
        if len(body) >= MIN_TEXT_CHARS:
            write_bytes(work_dir / relative_text, gzip.compress(text_document(entry, body).encode("utf-8")))
            entry["text_file"] = relative_text
            entry["text_status"] = "ok"
        else:
            # スキャンだけの PDF。本文は OCR の掃き取りに任せる。
            entry["text_file"] = ""
            entry["text_status"] = "needs_ocr" if pages else "unreadable"
            result.needs_ocr += 1
        if had_previous:
            result.updated += 1
        else:
            result.downloaded += 1
        documents[doc_id] = entry

    # 見つからなくなった文書。保存物は残し、消えたことだけ記録する。
    # 入口が開けなかった回は判断しない（一時的な 404 で全部を消えた扱いにしない）。
    if result.status == "ok" and result.entry_opened:
        result.missing += len(mark_missing(documents, seen_ids | listed_ids))

    if not result.entry_opened and result.status == "ok":
        result.status = "entry_unreachable"
    elif result.failed and result.status == "ok":
        result.status = "partial"

    write_json(work_dir / "manifest.json", {
        "version": MANIFEST_VERSION,
        "slug": target.slug,
        "jis_code": target.code,
        "name": target.name,
        "entry_url": target.url,
        "registry_status": target.crawl_status,
        "updated_at": now_text(),
        "last_run": {
            "started_at": started_at,
            "finished_at": now_text(),
            "status": result.status,
            "pages": result.pages,
            "found": result.found,
            "downloaded": result.downloaded,
            "updated": result.updated,
            "unchanged": result.unchanged,
            "skipped_recent": result.skipped_recent,
            "failed": result.failed,
            "needs_ocr": result.needs_ocr,
            "missing": result.missing,
            "negative_links": result.negative,
            "errors": result.errors[:50],
        },
        "individual_audits": individual,
        "documents": dict(sorted(documents.items())),
    })
    return result


# ─────────────────────────────────────
# CLI
# ─────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="包括外部監査の報告書を取得して保存する。")
    pick = parser.add_argument_group("対象")
    pick.add_argument("--slug", action="append", default=[], help="自治体 slug（複数可）")
    pick.add_argument("--code", action="append", default=[], help="自治体コード（複数可）")
    pick.add_argument("--all", action="store_true", help="台帳の enabled をすべて")
    pick.add_argument("--include-review", action="store_true", help="review_required も対象にする")
    pick.add_argument("--limit", type=int, default=0, help="対象の自治体数の上限")
    parser.add_argument("--out-dir", default="", help=f"保存先（既定 {DEFAULT_WORK_ROOT}）")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="1 件ごとの間隔（秒、1 以上）")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="1 自治体で開くページの上限")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="入口から降りる階層")
    parser.add_argument("--max-documents", type=int, default=DEFAULT_MAX_DOCUMENTS, help="1 自治体で取る文書の上限")
    parser.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS, help="取得済みを問い合わせ直すまでの日数")
    parser.add_argument("--dry-run", action="store_true", help="候補を表示するだけで取らない")
    parser.add_argument("--max-fetch", type=int, default=0,
                        help="1 自治体で扱う文書の数（新しい年度から。手元の試験用、0 は無制限）")
    parser.add_argument("--summary-json", default="", help="実行結果の要約を書き出す先")
    return parser


def select_targets(args: argparse.Namespace) -> list[Target]:
    targets = load_targets(include_review=args.include_review or bool(args.slug or args.code))
    if args.slug or args.code:
        wanted_slugs = set(args.slug)
        wanted_codes = {code.strip() for code in args.code}
        targets = [t for t in targets if t.slug in wanted_slugs or t.code in wanted_codes]
    elif not args.all:
        return []
    if args.limit > 0:
        targets = targets[: args.limit]
    return targets


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = select_targets(args)
    if not targets:
        print("対象がありません。--slug / --code / --all のどれかを指定してください。", file=sys.stderr)
        return 2
    work_root = Path(args.out_dir) if args.out_dir else DEFAULT_WORK_ROOT
    work_root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    fetcher = Fetcher(session=session, guard=HostGuard(work_root), delay=max(1.0, float(args.delay)))

    summary: list[dict] = []
    worst = 0
    for index, target in enumerate(targets, 1):
        print(f"[{index}/{len(targets)}] {target.slug} {target.name} <{target.url}>", flush=True)
        result = scrape_one(
            target, fetcher, work_root,
            max_pages=args.max_pages, max_depth=args.max_depth, max_documents=args.max_documents,
            refresh_days=args.refresh_days, dry_run=args.dry_run, max_fetch=args.max_fetch,
        )
        print(
            f"  -> {result.status} ページ{result.pages} 候補{result.found} 取得{result.downloaded} "
            f"更新{result.updated} 変化なし{result.unchanged} 最近確認済み{result.skipped_recent} "
            f"失敗{result.failed} OCR待ち{result.needs_ocr} 消えた{result.missing} "
            f"個別外部監査{result.individual} 別の監査{result.negative}",
            flush=True,
        )
        for error in result.errors[:5]:
            print(f"     {error}", flush=True)
        summary.append({**result.__dict__, "code": target.code, "name": target.name, "entry_url": target.url})
        if result.status not in ("ok",):
            worst = 1
    if args.summary_json:
        write_json(Path(args.summary_json), {"generated_at": now_text(), "results": summary})
    print(f"[DONE] 自治体 {len(targets)} / HTTP 要求 {fetcher.requests_made}", flush=True)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
