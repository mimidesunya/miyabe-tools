#!/usr/bin/env python3
"""取得元が「こういう会議種別がある」と示している一覧だけを取りに行く。

`audit_meeting_types.py` は「本会議しか無い」自治体を洗い出すが、それが
**取得元が本会議しか公開していない**のか **こちらが委員会の入口を見落として
いる**のかは、取得元の言い分と突き合わせないと分からない。

各スクレイパは取得のついでにこれを `scrape_state.json` の
`source_coverage.offered_meeting_types` へ書くが、それには全件取得をやり直す
必要がある（1 自治体で数時間かかる）。このツールは**取得はせず**、
種別の一覧だけを 1〜数リクエストで取ってきて同じ場所へ書く。

  python tools/gijiroku/probe_offered_meeting_types.py --system dbsr
  python tools/gijiroku/probe_offered_meeting_types.py --slug 45201-miyazaki-shi

対応していない系統:
  kensakusystem — ツリーを全部歩いて取るので、提示＝収録。突き合わせる意味がない
  独自 — 個別実装なので共通の読み取り口が無い
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

SCRAPER_DIR = Path(__file__).resolve().parent / "scrapers"
sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(SCRAPER_DIR))
import gijiroku_storage  # noqa: E402
import gijiroku_targets  # noqa: E402

PROBE_SYSTEMS = ("dbsr", "kaigiroku.net", "gijiroku.com")


def probe_dbsr(page, target: dict, timeout_ms: int) -> list[str]:
    import dbsr  # noqa: PLC0415

    source_url = str(target["source_url"])
    offered: list[str] = []
    page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms)
    dbsr.record_offered_types(offered, dbsr.read_cabinet_options(page))
    if offered:
        return offered

    # 会議種別の選択肢がどのページに載っているかは取得元によって違う。
    # 検索ページと、入口に並ぶ閲覧・検索メニューを順に開いて探す。
    candidates: list[str] = []
    try:
        candidates.append(dbsr.find_search_library_url(page, source_url))
    except Exception:
        pass
    links = page.locator(dbsr.MENU_TEMPLATE_SELECTOR)
    for index in range(links.count()):
        href = dbsr.safe_href(links.nth(index))
        if not href:
            continue
        menu_url = dbsr.canonicalize_template_url(urljoin(source_url, href))
        if menu_url not in candidates:
            candidates.append(menu_url)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            page.goto(candidate, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            continue
        dbsr.record_offered_types(offered, dbsr.read_cabinet_options(page))
        if offered:
            break
    return offered


def probe_kaigiroku_net(page, target: dict, timeout_ms: int, max_years: int) -> list[str]:
    import kaigiroku_net as kn  # noqa: PLC0415

    source_url = str(target["source_url"])
    tenant_id = kn.load_tenant_id(page, source_url, timeout_ms)
    api_root = kn.source_api_root(source_url)
    year_data = kn.api_post(
        page.request,
        api_root,
        "councils/get_view_years",
        {"tenant_id": tenant_id, "power_user": kn.POWER_USER_VALUE},
        timeout_ms,
        referer=source_url,
    )
    year_rows = year_data.get("view_years", [])
    if not isinstance(year_rows, list):
        year_rows = []
    if max_years > 0:
        year_rows = year_rows[:max_years]

    offered: list[str] = []
    for year_index, year_row in enumerate(year_rows):
        if not isinstance(year_row, dict):
            continue
        if year_index > 0:
            # 1 自治体で数十年ぶんを続けて叩くので、間隔を空ける。
            time.sleep(0.15)
        view_year = str(year_row.get("view_year", "")).strip()
        council_data = kn.api_post(
            page.request,
            api_root,
            "councils/index",
            {"tenant_id": tenant_id, "power_user": kn.POWER_USER_VALUE, "view_years": view_year},
            timeout_ms,
            referer=source_url,
        )
        roots = council_data.get("councils", [])
        if not isinstance(roots, list):
            continue
        for root in roots:
            for view_entry in (root.get("view_years") or []) if isinstance(root, dict) else []:
                if not isinstance(view_entry, dict):
                    continue
                for council_type in view_entry.get("council_type") or []:
                    if not isinstance(council_type, dict):
                        continue
                    path = str(council_type.get("council_type_path", "")).strip()
                    if not path.startswith("/0/1/"):
                        # /0/2/ 以下は「資料」で会議録ではない。
                        continue
                    for label in kn.offered_type_labels(council_type):
                        if label not in offered:
                            offered.append(label)
    return offered


def probe_gijiroku_com(page, target: dict, timeout_ms: int) -> list[str]:
    import gijiroku_com as gc  # noqa: PLC0415

    return gc.read_offered_meeting_types(page.context.request, str(target["base_url"]), timeout_ms)


def save_offered_types(target: dict, offered: list[str]) -> Path:
    # scrape_state.json は実行のたびに消される（batch.py の
    # remove_stale_scrape_state）ので、消えない別ファイルへ置く。
    work_dir = Path(str(target["work_dir"]))
    work_dir.mkdir(parents=True, exist_ok=True)
    gijiroku_storage.save_offered_meeting_types(work_dir, offered)
    return gijiroku_storage.offered_meeting_types_path(work_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="取得元が示す会議種別だけを取得し、scrape_state.json へ記録します。"
    )
    parser.add_argument("--system", default="", help=f"system family（{'/'.join(PROBE_SYSTEMS)}）")
    parser.add_argument("--slug", action="append", default=[], help="自治体 slug。複数指定可。")
    parser.add_argument("--limit", type=int, default=0, help="処理する自治体数の上限")
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--max-years",
        type=int,
        default=0,
        help="kaigiroku.net で見る年度数の上限（0 は全年度）。0 以外は取りこぼし判定が甘くなる。",
    )
    parser.add_argument("--headful", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = gijiroku_targets.iter_gijiroku_targets(args.system or None)
    if args.slug:
        wanted = set(args.slug)
        targets = [t for t in targets if str(t["slug"]) in wanted]
    targets = [t for t in targets if str(t["system_family"]) in PROBE_SYSTEMS]
    if args.limit > 0:
        targets = targets[: args.limit]
    if not targets:
        print("[WARN] 対象がありません", flush=True)
        return 1

    print(f"[INFO] 対象 {len(targets)} 自治体", flush=True)
    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headful)
        import kaigiroku_net as kn  # noqa: PLC0415

        # kaigiroku.net は既定の UA だと tenant_id を返さない。
        # スクレイパと同じ UA で通す。
        context = browser.new_context(locale="ja-JP", user_agent=kn.DEFAULT_USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)
        for index, target in enumerate(targets, start=1):
            family = str(target["system_family"])
            slug = str(target["slug"])
            try:
                if family == "dbsr":
                    offered = probe_dbsr(page, target, args.timeout_ms)
                elif family == "kaigiroku.net":
                    offered = probe_kaigiroku_net(page, target, args.timeout_ms, args.max_years)
                else:
                    offered = probe_gijiroku_com(page, target, args.timeout_ms)
            except Exception as exc:
                failures += 1
                print(f"[WARN] {index}/{len(targets)} {slug}: 失敗 [{type(exc).__name__}] {exc}", flush=True)
                continue
            if not offered:
                # 空を保存すると、監査が「取得元にも委員会が無い」と読んでしまう。
                # 読み取れなかったのか本当に無いのかを区別できないので、書かない。
                failures += 1
                print(
                    f"[WARN] {index}/{len(targets)} {slug} ({family}): 会議種別を読み取れませんでした",
                    flush=True,
                )
                continue
            save_offered_types(target, offered)
            print(
                f"[INFO] {index}/{len(targets)} {slug} ({family}): {len(offered)}種別"
                f" {'/'.join(offered[:6])}",
                flush=True,
            )
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)
        browser.close()

    print(f"[INFO] 完了 失敗={failures}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
