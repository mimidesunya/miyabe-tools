#!/usr/bin/env python3
"""legal-square（ぎょうせい Super Reiki-Base インターネット版）の例規集を取得する。

JSF + RichFaces + クライアント側ビューアで構成され、本文(条文)は JavaScript
ビューア(ポップアップ SVDocumentView)でしか開けないため Playwright で取得する。
（一覧メタデータは requests でも取れるが本文が取れないため headless に統一。）

フロー:
1. SJSrbLogin.jsf を開くと onload で自動的に「例規一覧」へ遷移
2. 「詳細」タブ(#detailSearch) → 条件空のまま「検索」(#searchDetail) で全例規一覧
3. 一覧 1 行ごとに 例規名(a.viewerOpener)・公布日・番号・所管課 を取得
4. 行クリックでポップアップ(本文ビューア)が開き .viewer-jobun に条文 → 取得
5. 「次へ」でページ送り

制約: 1 回の検索で返る件数には取得元ごとの上限がある（100/250/500/1000 件など。
ページ送り自体は正しく動くが、上限に達すると総件数そのものが上限値で頭打ちになる）。
そのため条件を空にした 1 回の検索では取り切れない。

分割の方針:
1. 詳細検索の「種別」ツリー（条例・規則・告示・訓令など）で分ける
2. それでも上限に張り付く種別は「制定年月日」の範囲で二分し、上限を下回るまで
   細かくする（最小単位は 1 か月）
題名・番号・公布日から作る stem で重複を除くので、範囲が重なっても二重には取らない。
種別ツリーが無い取得元や、そもそも上限に達しない取得元は 1 回だけ検索する。

共通の正規化 HTML/Markdown/manifest 生成は static_catalog のヘルパを再利用する。
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import reiki_io  # noqa: E402
import reiki_targets  # noqa: E402
import static_catalog  # noqa: E402
from static_catalog import ParsedArticle  # noqa: E402


USER_AGENT = static_catalog.USER_AGENT
# 詳細検索結果に表示される行を取り出す JS（例規名アンカーと同じ行のセルを読む）。
ROW_EVAL = """
() => Array.from(document.querySelectorAll('a.viewerOpener')).map(a => {
  const tds = Array.from(a.closest('tr').querySelectorAll('td'));
  const cell = (kw) => { const t = tds.map(td => td.innerText.trim()); return t; };
  const texts = tds.map(td => td.innerText.trim());
  return { title: a.innerText.trim(), cells: texts };
})
"""


def stem_for(title: str, number: str, date: str) -> str:
    digest = hashlib.sha1(f"{title}|{number}|{date}".encode("utf-8")).hexdigest()[:16]
    return digest


def _cell_after_title(cells: list[str]) -> tuple[str, str, str]:
    # cells = [例規名, 公布日, 番号, 所管課, ...] の並び。題名セルを除いた最初の3つを使う。
    rest = [c for c in cells if c]
    # 題名は a.viewerOpener 由来で別取得済みなので、日付/番号/所管を推定する。
    date = ""
    number = ""
    dept = ""
    for c in rest:
        if not date and static_catalog.extract_wareki(c):
            date = static_catalog.extract_wareki(c)
        elif not number and ("第" in c and "号" in c):
            number = c
        elif date and number and not dept:
            dept = c
    return date, number, dept


def open_search(page, source_url: str, timeout_ms: int) -> None:
    page.goto(source_url, wait_until="networkidle", timeout=timeout_ms)
    # 詳細タブ → 検索（条件空＝全件）
    page.click("#detailSearch", timeout=timeout_ms)
    page.wait_for_timeout(1200)
    page.click("#searchDetail", timeout=timeout_ms)
    page.wait_for_selector("a.viewerOpener", timeout=timeout_ms)
    page.wait_for_timeout(1500)


# 詳細検索の「種別」ツリーの第1階層（条例・規則・告示…）を読む JS。
KIND_EVAL = """
() => Array.from(document.querySelectorAll('ul.treeview li')).map(li => {
  const cb = li.querySelector('input[type=checkbox]');
  if (!cb || !/kind_tree01$/.test(cb.id)) return null;
  const text = Array.from(li.childNodes)
    .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
  return text ? {id: cb.id, text: text} : null;
}).filter(Boolean)
"""


def list_kind_segments(page) -> list[dict]:
    """種別ツリーの第1階層を返す。無ければ空。"""
    try:
        return page.evaluate(KIND_EVAL) or []
    except Exception:
        return []


# 1 回の検索で返る件数の上限としてありそうな値。取得元ごとに設定が違う。
CAP_HINTS = (100, 200, 250, 300, 400, 500, 1000, 2000, 3000, 5000)

# 元号ごとの (開始西暦, 使い始める年月日, 使い終わる年月日)。
# 詳細検索は実在しない和暦日付を「年月日(FROM) に正しい日付をご記入ください。」で
# 弾き、しかも前の検索結果を残したままにする。件数を読み違えないよう、
# 範囲の端は必ず実在する日付にする。
# 明治1年は弾かれるので 2 年から始める（明治元年の例規は実質存在しない）。
ERA_SPEC: tuple[tuple[str, int, tuple[int, int, int], tuple[int, int, int]], ...] = (
    ("明治", 1868, (2, 1, 1), (45, 7, 30)),
    ("大正", 1912, (1, 7, 30), (15, 12, 25)),
    ("昭和", 1926, (1, 12, 25), (64, 1, 7)),
    ("平成", 1989, (1, 1, 8), (31, 4, 30)),
)
REIWA_BASE = 2019

# 「1～10件目/100件」から総件数を読む JS。
TOTAL_EVAL = "() => { const d = document.querySelector('#pager dt'); return d ? d.innerText.trim() : ''; }"


def read_pager_text(page) -> str:
    """「1～50件目/1000件」のような件数表示をそのまま返す。"""
    try:
        return page.evaluate(TOTAL_EVAL) or ""
    except Exception:
        return ""


def read_result_total(page) -> int:
    """検索結果の総件数を返す。読めなければ 0。"""
    text = read_pager_text(page)
    match = re.search(r"/\s*([0-9,]+)\s*件", text) or re.match(r"\s*([0-9,]+)\s*件", text)
    return int(match.group(1).replace(",", "")) if match else 0


def read_page_range(page_text: str) -> tuple[int, int] | None:
    """件数表示から (このページの末尾の番号, 総件数) を読む。読めなければ None。"""
    match = re.search(
        r"([0-9,]+)\s*[〜～~-]\s*([0-9,]+)\s*件目\s*/\s*([0-9,]+)\s*件", page_text
    )
    if match is None:
        return None
    return int(match.group(2).replace(",", "")), int(match.group(3).replace(",", ""))


def detect_cap(page, total: int) -> int:
    """この取得元の検索件数上限を推定する。上限に達していなければ 0。"""
    try:
        over = page.query_selector("text=/件を超え/") is not None
    except Exception:
        over = False
    return total if (over or total in CAP_HINTS) and total > 0 else 0


class MonthSlot(NamedTuple):
    """分割の刻み 1 つ。端の月は元号の開始日・終了日に丸めてある。"""

    era: str
    year: int
    month: int
    first_day: int
    last_day: int


def month_slots() -> list[MonthSlot]:
    """明治2年1月から今月までを 1 か月刻みで並べる。"""
    today = datetime.date.today()
    # 末尾は今年いっぱいまで取る。今日までで切ると、公布日が先の例規が
    # どの区間にも入らず、期間で割ったときに取りこぼす。
    specs = list(ERA_SPEC) + [
        ("令和", REIWA_BASE, (1, 5, 1), (today.year - REIWA_BASE + 1, 12, 31))
    ]
    slots: list[MonthSlot] = []
    for era, base, (from_year, from_month, from_day), (to_year, to_month, to_day) in specs:
        year, month = from_year, from_month
        while (year, month) <= (to_year, to_month):
            first = from_day if (year, month) == (from_year, from_month) else 1
            if (year, month) == (to_year, to_month):
                last = to_day
            else:
                last = calendar.monthrange(base + year - 1, month)[1]
            slots.append(MonthSlot(era, year, month, first, last))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return slots


MONTH_SLOTS = month_slots()


# 種別と制定年月日を詳細検索フォームへ流し込む JS。
FILTER_EVAL = """
(a) => {
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const setChecked = (id, on) => {
    const el = document.getElementById(id);
    if (!el || el.checked === on) return;
    el.checked = on;
    el.dispatchEvent(new Event('click', {bubbles: true}));
  };
  document.querySelectorAll('ul.treeview input[type=checkbox]').forEach(c => { c.checked = false; });
  if (a.kindId) {
    const el = document.getElementById(a.kindId);
    if (el) { el.checked = true; el.dispatchEvent(new Event('click', {bubbles: true})); }
  }
  setChecked('ymdClass_Seitei', a.from !== null);
  ['ymdClass_Revise', 'ymdClass_LastRevise', 'ymdClass_Abolish']
    .forEach(id => setChecked(id, false));
  setValue('ymdFrom', a.from ? a.from[0] : '');
  setValue('ymdFrom-Y', a.from ? String(a.from[1]) : '');
  setValue('ymdFrom-M', a.from ? String(a.from[2]) : '');
  setValue('ymdFrom-D', a.from ? String(a.from[3]) : '');
  setValue('ymdTo', a.to ? a.to[0] : '');
  setValue('ymdTo-Y', a.to ? String(a.to[1]) : '');
  setValue('ymdTo-M', a.to ? String(a.to[2]) : '');
  setValue('ymdTo-D', a.to ? String(a.to[3]) : '');
}
"""


def apply_filters(page, kind_id: str, span: tuple[int, int] | None) -> None:
    """種別と制定年月日の範囲を設定する。span は MONTH_SLOTS の添字 [lo, hi]。"""
    payload: dict = {"kindId": kind_id or None, "from": None, "to": None}
    if span is not None:
        head, tail = MONTH_SLOTS[span[0]], MONTH_SLOTS[span[1]]
        payload["from"] = [head.era, head.year, head.month, head.first_day]
        payload["to"] = [tail.era, tail.year, tail.month, tail.last_day]
    page.evaluate(FILTER_EVAL, payload)


def span_label(span: tuple[int, int] | None) -> str:
    if span is None:
        return "全期間"
    head, tail = MONTH_SLOTS[span[0]], MONTH_SLOTS[span[1]]
    return f"{head.era}{head.year}.{head.month}〜{tail.era}{tail.year}.{tail.month}"


# 検索前に古い件数表示へ印を付け、印が消える＝結果が差し替わったとみなす。
STAMP_EVAL = """
() => { const d = document.querySelector('#pager dt');
        if (!d) return false; d.setAttribute('data-stale', '1'); return true; }
"""
FRESH_EVAL = """
() => { const d = document.querySelector('#pager dt');
        return !d || !d.hasAttribute('data-stale'); }
"""


# 日付を弾かれると検索が実行されず、前の結果が残る。件数を読み違えないよう見張る。
DATE_ERROR_EVAL = "() => /に正しい日付をご記入ください/.test(document.body.innerText)"


# run_search の結果。
#   "ok"    … 結果が差し替わり、件数を信用してよい
#   "empty" … 0 件、または日付を弾かれた
#   "stale" … 差し替えを確認できなかった。表示は前の検索のままかもしれない
SEARCH_OK = "ok"
SEARCH_EMPTY = "empty"
SEARCH_STALE = "stale"


def run_search(page, timeout_ms: int) -> str:
    """検索を実行し、結果が差し替わるのを待つ。SEARCH_* のいずれかを返す。"""
    try:
        stamped = bool(page.evaluate(STAMP_EVAL))
    except Exception:
        stamped = False
    page.click("#searchDetail", timeout=timeout_ms)
    stale = False
    if stamped:
        try:
            page.wait_for_function(FRESH_EVAL, timeout=timeout_ms)
        except PlaywrightTimeoutError:
            # 前の検索結果が残っている可能性がある。件数を信用してはいけない。
            print("[WARN] 検索結果の差し替えを待ちきれませんでした", flush=True)
            stale = True
    try:
        page.wait_for_selector("#pager", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return SEARCH_EMPTY
    page.wait_for_timeout(600)
    try:
        if page.evaluate(DATE_ERROR_EVAL):
            print("[WARN] 日付が不正として弾かれました（この範囲は取得できません）", flush=True)
            return SEARCH_EMPTY
    except Exception:
        pass
    if stale:
        return SEARCH_STALE
    return SEARCH_OK if read_result_total(page) > 0 else SEARCH_EMPTY


def reopen_detail(page, timeout_ms: int) -> None:
    """検索結果から詳細検索タブへ戻る。"""
    page.click("#detailSearch", timeout=timeout_ms)
    page.wait_for_timeout(1200)


def extract_body_html(popup) -> str:
    # networkidle に到達しないビューアがあるので、待ちには必ず上限を付ける。
    try:
        popup.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    popup.wait_for_timeout(400)
    parts = popup.eval_on_selector_all(
        ".viewer-jobun", "els => els.map(e => e.innerHTML)"
    )
    return "\n".join(p for p in parts if p and p.strip())


def run(slug: str, expected_system: str, *, force: bool, check_updates: bool, limit: int, headful: bool, timeout_ms: int) -> int:
    target = reiki_targets.load_reiki_target(slug, expected_system=expected_system)
    source_dir = Path(target["source_dir"])
    html_dir = Path(target["html_dir"])
    markdown_dir = Path(target["markdown_dir"])
    work_root = Path(target["work_root"])
    manifest_path = work_root / "source_manifest.json.gz"
    state_path = work_root / "scrape_state.json"
    source_url = str(target["source_url"])
    for d in (source_dir, html_dir, markdown_dir):
        d.mkdir(parents=True, exist_ok=True)

    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})", flush=True)
    print(f"Source URL: {source_url}", flush=True)

    manifest: list[dict] = []
    seen_stems: set[str] = set()
    downloaded = failed = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful, args=["--ignore-certificate-errors"])
        context = browser.new_context(ignore_https_errors=True, locale="ja-JP", user_agent=USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        # ポップアップ(本文ビューア)にも既定のタイムアウトを効かせる。
        context.set_default_timeout(timeout_ms)
        open_search(page, source_url, timeout_ms)

        emit_total = 0
        stopped = False

        def harvest_pages(label: str) -> int:
            """いま表示中の検索結果をページ送りしながら取り込む。取り込んだ件数を返す。

            取得済みの例規は読み飛ばすだけなので何も出力せずに何分も進むことがある。
            見張りに故障と誤解されないよう、ページ送りのたびに現在地を知らせる。
            """
            nonlocal emit_total, downloaded, failed, stopped
            start_total = emit_total
            page_no = 0
            while True:
                page_no += 1
                pager_text = read_pager_text(page)
                print(f"[INFO] {label}: {page_no}ページ目（累計 {emit_total}件）", flush=True)
                rows = page.evaluate(ROW_EVAL)
                anchors = page.query_selector_all("a.viewerOpener")
                count = min(len(rows), len(anchors))
                for i in range(count):
                    meta = rows[i]
                    title = str(meta.get("title", "")).strip()
                    date_text, number, _dept = _cell_after_title(meta.get("cells", []))
                    stem = stem_for(title, number, date_text)
                    if stem in seen_stems:
                        continue
                    filename = f"{stem}.html"
                    clean_path = html_dir / filename
                    markdown_path = markdown_dir / f"{stem}.md"
                    iso_date = static_catalog.to_seireki(date_text)

                    if not force and not check_updates and reiki_io.existing_path(clean_path) is not None:
                        seen_stems.add(stem)
                        manifest.append(_manifest_row(filename, source_url, title, number, iso_date))
                        emit_total += 1
                        continue

                    body_html = ""
                    try:
                        with page.expect_popup(timeout=timeout_ms) as pi:
                            anchors[i].click()
                        popup = pi.value
                        body_html = extract_body_html(popup)
                        popup.close()
                    except PlaywrightTimeoutError:
                        print(f"[WARN] popup timeout: {title[:30]}", flush=True)
                    except Exception as exc:
                        print(f"[WARN] body fetch failed {title[:30]}: {exc}", flush=True)

                    if not body_html.strip():
                        # ここで seen_stems へ入れない。期間を割った別の検索で
                        # 同じ例規に当たったとき、もう一度だけ試せるようにする。
                        failed += 1
                        continue

                    seen_stems.add(stem)
                    parsed = ParsedArticle(title=title, content_html=body_html, date_text=date_text, number=number)
                    content_text = static_catalog.html_to_plain(body_html)
                    reiki_io.write_text(source_dir / filename, body_html, compress=True)
                    reiki_io.write_text(clean_path, static_catalog.build_clean_html(parsed, iso_date))
                    reiki_io.write_text(markdown_path, static_catalog.build_markdown(parsed, iso_date, content_text), compress=True)
                    manifest.append(_manifest_row(filename, source_url, title, number, iso_date))
                    downloaded += 1
                    emit_total += 1
                    static_catalog.emit_progress(emit_total, max(emit_total, len(rows) * page_no), state_path)
                    reiki_io.write_json(manifest_path, manifest, compress=True)
                    time.sleep(0.1)
                    if limit > 0 and emit_total >= limit:
                        break

                if limit > 0 and emit_total >= limit:
                    stopped = True
                    break
                # 最終ページかどうかは件数表示で判断する。「次へ」は最終ページでも
                # 無効にならず、押しても同じページが返るだけで永久に回ってしまう。
                span_seen = read_page_range(pager_text)
                if span_seen is not None and span_seen[0] >= span_seen[1]:
                    break
                nxt = page.query_selector("a:has-text('次へ')")
                if nxt is None:
                    break
                cls = (nxt.get_attribute("class") or "")
                if "disable" in cls.lower():
                    break
                try:
                    nxt.click()
                except Exception:
                    break
                # 固定時間で待つと、遅い応答を「進めない」と誤判定して
                # 残りのページを捨ててしまう。表示が変わるまで待つ。
                try:
                    page.wait_for_function(
                        "(previous) => {"
                        " const d = document.querySelector('#pager dt');"
                        " return d && d.innerText.trim() !== previous; }",
                        arg=pager_text,
                        timeout=timeout_ms,
                    )
                except PlaywrightTimeoutError:
                    # 本当に最終ページなら表示は変わらない。
                    break
                page.wait_for_timeout(400)
            return emit_total - start_total

        def collect(kind: dict, span: tuple[int, int] | None, depth: int) -> None:
            """種別 × 制定年月日で検索し、上限に張り付くなら期間を二分してやり直す。"""
            if stopped:
                return
            reopen_detail(page, timeout_ms)
            apply_filters(page, kind["id"], span)
            outcome = run_search(page, timeout_ms)
            if outcome == SEARCH_EMPTY:
                return
            if outcome == SEARCH_STALE:
                # 件数を信用できないので、取り込まずに期間を割って確かめ直す。
                if span is not None and span[0] >= span[1]:
                    print(
                        f"[WARN] {kind['text']} {span_label(span)}: "
                        "検索結果を確認できませんでした",
                        flush=True,
                    )
                    return
                lo, hi = (0, len(MONTH_SLOTS) - 1) if span is None else span
                mid = (lo + hi) // 2
                collect(kind, (lo, mid), depth + 1)
                collect(kind, (mid + 1, hi), depth + 1)
                return
            total = read_result_total(page)
            if total <= 0:
                return
            capped = cap > 0 and total >= cap
            # 上限に張り付いた中間ノードは、どうせ二分するので本文取得は省く。
            # ただし期間指定なしの初回だけは、制定年月日が無い例規を拾う保険として取り込む。
            if not capped or span is None or span[0] >= span[1]:
                got = harvest_pages(f"{kind['text']} {span_label(span)}")
                print(
                    f"[INFO] {kind['text']} {span_label(span)}: 総数{total}件 → 新規{got}件"
                    f"（累計 {emit_total}件）",
                    flush=True,
                )
            else:
                # 見張りが無出力を故障とみなすので、分割の途中も必ず知らせる。
                print(
                    f"[INFO] {kind['text']} {span_label(span)}: 上限{cap}件に達したので期間を二分します",
                    flush=True,
                )
            if not capped:
                return
            if span is not None and span[0] >= span[1]:
                print(
                    f"[WARN] {kind['text']} {span_label(span)}: 単月でも上限{cap}件に達しており取り切れません。",
                    flush=True,
                )
                return
            lo, hi = (0, len(MONTH_SLOTS) - 1) if span is None else span
            mid = (lo + hi) // 2
            collect(kind, (lo, mid), depth + 1)
            collect(kind, (mid + 1, hi), depth + 1)

        total0 = read_result_total(page)
        cap = detect_cap(page, total0)
        if cap == 0:
            print(f"[INFO] 条件なしで全{total0}件を取得します", flush=True)
            harvest_pages("全件")
        else:
            print(f"[INFO] 1回の検索は{cap}件で打ち切られます。種別と制定年月日で分割します。", flush=True)
            reopen_detail(page, timeout_ms)
            segments = list_kind_segments(page)
            if not segments:
                print("[INFO] 種別ツリーが無いので制定年月日だけで分割します", flush=True)
                segments = [{"id": "", "text": "全件"}]
            else:
                print(f"[INFO] 種別 {len(segments)} 件で分割します", flush=True)
            for seg_index, segment in enumerate(segments, 1):
                if stopped:
                    break
                collect(segment, None, 0)
                print(
                    f"[INFO] {seg_index}/{len(segments)} {segment['text']} 完了（累計 {emit_total}件）",
                    flush=True,
                )

        browser.close()

    if not manifest:
        raise RuntimeError("No ordinances collected; refusing to mark target as scraped.")
    reiki_io.write_json(manifest_path, manifest, compress=True)
    static_catalog.emit_progress(emit_total, emit_total, state_path)
    print(f"Finished. downloaded={downloaded} failed={failed} manifest={len(manifest)} -> {manifest_path}", flush=True)
    return 0


def _manifest_row(filename: str, source_url: str, title: str, number: str, iso_date: str) -> dict:
    return {
        "source_file": filename,
        "detail_url": source_url,
        "source_url": source_url,
        "title": title,
        "number": number,
        "enactment_date": iso_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download ordinances from legal-square (Reiki-Base) systems.")
    parser.add_argument("--slug", default="")
    parser.add_argument("--system-type", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    args = parser.parse_args()

    slug = args.slug.strip() or reiki_targets.default_slug_for_system("legal-square")
    target = reiki_targets.load_reiki_target(slug)
    return run(
        slug,
        str(target["system_type"]),
        force=args.force,
        check_updates=args.check_updates,
        limit=args.limit,
        headful=args.headful,
        timeout_ms=args.timeout_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
