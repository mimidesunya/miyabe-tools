#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discover_minutes_urls.classify_vendor / link_priority のオフライン単体テスト。

外部アクセスは行わない。分類指紋と探索優先度の回帰を守る。
    python tools/gijiroku/test_discover_minutes_urls.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("discover_minutes_urls", _HERE / "discover_minutes_urls.py")
disc = importlib.util.module_from_spec(_spec)
sys.modules["discover_minutes_urls"] = disc  # dataclass introspection 用に登録
_spec.loader.exec_module(disc)


VENDOR_CASES = {
    "https://ssp.kaigiroku.net/tenant/hakodate/SpMinuteSearch.html": "kaigiroku.net",
    "https://giji.city.yokohama.lg.jp/x/y": None,  # 自ホスト系は指紋外（低信頼側で拾う）
    "http://www.town.otofuke.hokkaido.dbsr.jp/index.php/": "dbsr",
    "http://www.db-search.com/ogi-c/index.php/": "dbsr",
    "http://www.kensakusystem.jp/hirosaki/index.html": "kensakusystem",
    "https://ami-search.amivoice.com/toride/usr/": "amivoice",
    "https://smart.discussvision.net/smart/tenant/tozawa/WebView/rd/council_1.html": "discussvision",
    "https://www.voicetechno.net/MinutesSystem/Asago/": "voicetechno",
    "https://pref-hokkaido.gijiroku.com/voices/g07v_search.asp": "gijiroku.com",
    "http://www2.city.hachinohe.aomori.jp/kaigiroku/voices/g07v_search.asp": "voices",
    "https://kaigiroku.city.shinagawa.tokyo.jp/index.php/": "kaigiroku-indexphp",
    "https://www.city.otaru.lg.jp/categories/bunya/gikai/kaigoroku/": None,
    "https://example.com/": None,
}


def test_classify_vendor() -> None:
    for url, expected in VENDOR_CASES.items():
        got = disc.classify_vendor(url)
        assert got == expected, f"classify_vendor({url!r}) = {got!r}, expected {expected!r}"


def test_link_priority_prefers_minutes_and_vendor() -> None:
    home = "www.city.hakodate.hokkaido.jp"
    vendor = disc.link_priority("https://ssp.kaigiroku.net/tenant/hakodate/", "会議録検索", home)
    minutes = disc.link_priority("https://www.city.hakodate.hokkaido.jp/gikai/kaigiroku/", "会議録", home)
    hub = disc.link_priority("https://www.city.hakodate.hokkaido.jp/gov/", "市政の情報", home)
    plain = disc.link_priority("https://www.city.hakodate.hokkaido.jp/kanko/", "観光", home)
    external_noise = disc.link_priority("https://www.instagram.com/travel/", "Instagram", home)

    assert vendor > minutes > hub, (vendor, minutes, hub)
    # ハブは辿る閾値(>=6)を超え、手掛かりの無い同ホストや外部ノイズは超えない。
    assert hub >= 6
    assert plain < 6
    assert external_noise < 6


def _run() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print("ALL PASS" if failures == 0 else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
