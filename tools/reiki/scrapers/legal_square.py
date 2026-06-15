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

制約: 詳細検索は最大 1000 件で打ち切られる。1000 件超の自治体は超過分を取得
できない（その場合は警告を出す）。

共通の正規化 HTML/Markdown/manifest 生成は static_catalog のヘルパを再利用する。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

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


def extract_body_html(popup) -> str:
    popup.wait_for_load_state("networkidle")
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
        open_search(page, source_url, timeout_ms)

        cap = page.query_selector("text=/1000件を超え/")
        if cap is not None:
            print("[WARN] 検索結果が1000件超のため最初の1000件のみ取得します。", flush=True)

        emit_total = 0
        page_no = 0
        while True:
            page_no += 1
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
                seen_stems.add(stem)
                filename = f"{stem}.html"
                clean_path = html_dir / filename
                markdown_path = markdown_dir / f"{stem}.md"
                iso_date = static_catalog.to_seireki(date_text)

                if not force and not check_updates and reiki_io.existing_path(clean_path) is not None:
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
                    failed += 1
                    continue

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
                break
            # 次ページ。「次へ」が無効/無ければ終了。
            nxt = page.query_selector("a:has-text('次へ')")
            if nxt is None:
                break
            cls = (nxt.get_attribute("class") or "")
            if "disable" in cls.lower():
                break
            try:
                nxt.click()
                page.wait_for_timeout(1800)
            except Exception:
                break

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
