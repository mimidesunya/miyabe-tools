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


# --- 2026-09-15 点検で見つけた、判定できなかった原因ごとの回帰 ---------------------

def test_decode_html_without_charset_header_is_not_latin1() -> None:
    # Content-Type に charset が無いと requests は ISO-8859-1 と見なし、日本語が化けていた
    # （勝浦市・美祢市・有田町）。化けると「会議録」「議会」のどの語にも当たらない。
    raw = "<html><head><title>会議録</title></head><body><a href='/x'>令和8年会議録</a></body></html>".encode("utf-8")
    assert "令和8年会議録" in disc.decode_html(raw, "text/html", "ISO-8859-1")
    sjis = "<html><head><meta charset='Shift_JIS'></head><body>議会</body></html>".encode("cp932")
    assert "議会" in disc.decode_html(sjis, "text/html")
    # ヘッダが Latin-1 を名乗っても、日本の自治体サイトの実体は UTF-8。
    assert "議会" in disc.decode_html("<p>議会</p>".encode("utf-8"), "text/html; charset=ISO-8859-1")


GIKAI_ROUTE = "市議会 https://www.city.example.lg.jp/gikai/"
MINUTES_ITEM_CASES = [
    # (PDF の題名, 一覧ページの題名, 辿ってきた経路, 会議録として数えるか)
    ("1月23日 議案審査 (PDFファイル: 1.2MB)", "令和8年 会議録・議決結果", GIKAI_ROUTE, True),   # 士別市
    ("令和7年2月28日第1回定例会本会議会議録", "会議録／美祢市ホームページ", "", True),           # 美祢市
    ("令和４年３月定例会会議録（第１号）.pdf", "会議録", "", True),                                # 遠野市
    ("3月4日 会議録（1日目） PDF(3165KB)", "会議結果・会議録｜行政・まちづくり", GIKAI_ROUTE, True),  # 湧別町
    ("3月4日 会議録（1日目） PDF(3165KB)", "会議結果・会議録｜行政・まちづくり", "", False),     # 議会と分からない
    ("議決結果 (PDFファイル: 80KB)", "令和8年 会議録・議決結果", GIKAI_ROUTE, False),
    ("開催予定表（R8.9.3更新）", "宮城県村田町 | 会期日程・各種行事", GIKAI_ROUTE, False),       # 村田町（誤って取得対象にしていた）
    ("一般質問項目", "宮城県村田町 | 会期日程・各種行事", GIKAI_ROUTE, False),
    ("令和8年第3回定例会一般質問順序表こちらからご覧になれます", "岩内町議会定例会＜一般質問順序表＞", GIKAI_ROUTE, False),
    ("2～3ページ　第3回定例会", "北海道比布町｜ 議会だより　第120号", GIKAI_ROUTE, False),         # 比布町
    ("平成３０年第１回子ども議会会議録（PDF形式：1.58MB）", "子ども議会の開催状況について", GIKAI_ROUTE, False),
    ("第116号 （6月定例会）", "とよおか議会だより", GIKAI_ROUTE, False),
    ("議事日程・本文 [PDFファイル／282KB]", "令和7年会議録", GIKAI_ROUTE, False),  # 取得側も議事日程として落とす
    ("第２回６月定例会一般質問", "一般質問｜議会", GIKAI_ROUTE, False),              # 会議録を名乗らない頁
    ("令和８年７月会議録", "教育委員会定例会会議録 | 葛巻町", GIKAI_ROUTE, False),    # 葛巻町（教育委員会）
    ("令和8年第4回月形町農業委員会総会議事録", "総会日程・議事録（農業委員会）", "", False),  # 月形町（農業委員会）
    ("令和8年第1回議会運営委員会会議録", "委員会会議録", "", True),                  # 議会の委員会は残す
]


def test_is_minutes_pdf_item() -> None:
    for label, page, route, expected in MINUTES_ITEM_CASES:
        got = disc.is_minutes_pdf_item(label, page, route)
        assert got == expected, f"is_minutes_pdf_item({label!r}, {page!r}, {route!r}) = {got}, expected {expected}"


def test_minutes_entry_title_excludes_newsletters_and_youth_assemblies() -> None:
    assert disc.is_minutes_entry_title("令和8年会議録 - 勝浦市議会")
    assert disc.is_minutes_entry_title("会議録検索")
    assert not disc.is_minutes_entry_title("議会だより")
    assert not disc.is_minutes_entry_title("子ども議会会議録")
    assert not disc.is_minutes_entry_title("会期日程・各種行事")
    assert not disc.is_minutes_entry_title("第3回定例会")
    assert not disc.is_minutes_entry_title("教育委員会定例会会議録")
    assert disc.is_minutes_entry_title("議会運営委員会会議録")


def test_meta_refresh_in_noscript_is_followed() -> None:
    # 遠野市のトップは JavaScript の入口ページで、本体へは noscript の meta refresh で送る。
    from bs4 import BeautifulSoup
    soup = BeautifulSoup('<html><head><noscript><meta http-equiv="refresh" content="1;URL=/index.cfm/1,html"></noscript></head></html>', "html.parser")
    assert disc.meta_refresh_links(soup, "https://www.city.tono.iwate.jp/") == [("https://www.city.tono.iwate.jp/index.cfm/1,html", "refresh")]


def test_video_only_vendors_are_not_sources() -> None:
    assert disc.is_video_only_vendor("https://smart.discussvision.net/smart/tenant/ibusuki/WebView/rd/council_1.html", "discussvision")
    assert disc.is_video_only_vendor("http://www.kensakusystem.jp/aridagawa-vod/index.html", "kensakusystem")
    assert not disc.is_video_only_vendor("http://www.kensakusystem.jp/hirosaki/index.html", "kensakusystem")


def test_video_link_does_not_stop_the_search() -> None:
    # 議会中継のリンクで探索を打ち切らず、その先の会議録ページを見つける。
    home = "https://www.city.example3.lg.jp/"
    pages = {
        home: _page("例市", [("https://smart.discussvision.net/smart/tenant/example/WebView/rd/council_1.html", "議会中継"), ("/gikai/", "市議会")]),
        home + "gikai/": _page("市議会", [("/gikai/kaigiroku/", "会議録")]),
        home + "gikai/kaigiroku/": _page("会議録", []),
    }
    original = _with_probe({home + "gikai/kaigiroku/": 5})
    try:
        found = disc.discover_one(_FakeSession(pages), "99996", "例市", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    assert found.system_type == "独自" and found.candidate_url == home + "gikai/kaigiroku/", found


def test_same_site_across_old_domain_and_lg_jp() -> None:
    hosts = {"www.city.tonami.toyama.jp"}
    assert disc.is_same_site("https://info.city.tonami.lg.jp/", hosts)
    assert disc.is_same_site("https://www.city.tonami.toyama.jp/gikai/", hosts)
    assert not disc.is_same_site("https://www.city.toyama.toyama.jp/", hosts)
    assert not disc.is_same_site("https://www.instagram.com/", hosts)


class _FakeResponse:
    def __init__(self, url: str, body: str, content_type: str = "text/html") -> None:
        self.url = url
        self.status_code = 200 if body is not None else 404
        self.headers = {"Content-Type": content_type}
        self.content = (body or "").encode("utf-8")
        self.apparent_encoding = "utf-8"


class _FakeSession:
    def __init__(self, pages: dict) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url, **_kwargs):
        self.requested.append(url)
        return _FakeResponse(url, self.pages.get(url))


def _page(title: str, links: list[tuple[str, str]]) -> str:
    anchors = "".join(f'<a href="{href}">{text}</a>' for href, text in links)
    return f"<html><head><title>{title}</title></head><body>{anchors}</body></html>"


def _with_probe(counts: dict):
    """取得側の巡回（ネットワーク）を、URL ごとの数へ差し替える。"""
    original = disc.probe_minutes_pdfs

    def fake(_session, url, _timeout, _delay, **_kwargs):
        minutes = counts.get(url, 0)
        return {"items": minutes, "minutes": minutes, "pages": 1, "examples": ["第1回定例会会議録"] * min(minutes, 3)}

    disc.probe_minutes_pdfs = fake
    return original


def test_entrance_page_charset_less_site_reaches_minutes_page() -> None:
    # 入口ページ（くらし・行政 だけ）→ 組織一覧 → 議会 → 会議録。charset はヘッダに無い。
    home = "https://www.city.example.lg.jp/"
    pages = {
        home: _page("例市", [("/main/", "例市公式サイト"), ("https://kanko.example.jp/", "観光")]),
        home + "main/": _page("行政情報", [("/soshiki/", "組織"), ("/soshiki/a.html", "総務課"), ("/gikai/", "市議会")]),
        home + "soshiki/": _page("組織", [("/soshiki/b.html", "財政課")]),
        home + "gikai/": _page("市議会", [("/gikai/nittei.html", "会期日程"), ("/gikai/list27.html", "会議録")]),
        home + "gikai/nittei.html": _page("会期日程", []),
        home + "gikai/list27.html": _page("会議録 - 例市議会", [("/gikai/r8.html", "令和8年会議録")]),
    }
    original = _with_probe({home + "gikai/list27.html": 12})
    try:
        found = disc.discover_one(_FakeSession(pages), "99999", "例市", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    assert found.confidence == "medium", found
    assert found.system_type == "独自", found
    assert found.candidate_url == home + "gikai/list27.html", found


def test_schedule_page_is_not_registered_as_minutes() -> None:
    # 会議録の PDF を数えられなければ、会議録らしいページがあっても取得対象にしない。
    home = "https://www.town.example.lg.jp/"
    pages = {
        home: _page("例町", [("/gikai/", "町議会")]),
        home + "gikai/": _page("町議会", [("/gikai/nittei/", "会期日程・各種行事"), ("/gikai/dayori/", "議会だより")]),
        home + "gikai/nittei/": _page("会期日程・各種行事", [("/gikai/nittei/1.pdf", "第3回定例会 開催予定表")]),
        home + "gikai/dayori/": _page("議会だより", [("/gikai/dayori/1.pdf", "第3回定例会")]),
    }
    original = _with_probe({})
    try:
        found = disc.discover_one(_FakeSession(pages), "99998", "例町", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    assert found.confidence in ("low", "none"), found
    assert found.system_type == "", found


def test_minutes_page_without_countable_pdfs_stays_low() -> None:
    # 仁淀川町: 会議録ページはあるが PDF が /download/?t=LD&id= の配信口で、取得側が拾えない。
    home = "https://www.town.example2.lg.jp/"
    pages = {
        home: _page("例町", [("/gikai/", "議会情報")]),
        home + "gikai/": _page("議会情報", [("/life/life_dtl.php?hdnKey=3275", "令和８年 例町議会 会議録")]),
        home + "life/life_dtl.php?hdnKey=3275": _page("令和８年 例町議会 会議録", [("/download/?t=LD&id=3275&fid=1", "第１回臨時会（PDF：399KB）")]),
    }
    original = _with_probe({})
    try:
        found = disc.discover_one(_FakeSession(pages), "99997", "例町", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    assert found.confidence == "low", found
    assert found.candidate_url == home + "life/life_dtl.php?hdnKey=3275", found


# --- 2026-09-16 の点検で足した分 ------------------------------------------------

def test_file_name_names_minutes() -> None:
    # 一覧ページの題名が会議録を名乗らなくても、PDF の名前で分かる（和束町）。
    assert disc.is_minutes_pdf_item(
        "定例会1日目 (PDFファイル: 897.6KB)", "令和8年9月定例会", GIKAI_ROUTE,
        "https://www.town.wazuka.lg.jp/material/files/group/15/gijiroku_teireikai202635.pdf")
    # 会議録の棚にあっても、日程・名簿・議決結果のファイルは数えない。
    assert not disc.is_minutes_pdf_item(
        "9月定例会", "令和8年9月定例会", GIKAI_ROUTE,
        "https://www.town.example.lg.jp/gikai/kaigiroku/r8-nittei.pdf")
    assert not disc.is_minutes_pdf_item(
        "議会だより 第120号", "議会だより", GIKAI_ROUTE,
        "https://www.town.example.lg.jp/gikai/kaigiroku/dayori120.pdf")
    # 議会と分からない経路では、ファイル名だけでは採らない。
    assert not disc.is_minutes_pdf_item(
        "総会", "農業委員会", "", "https://www.town.example.lg.jp/nougyou/gijiroku202603.pdf")


def test_page_url_names_the_minutes_list() -> None:
    # 題名を持たない一覧ページ（昭和村）や、題名が「令和8年度」だけの年別ページ（勝浦町）。
    # 頁の URL が会議録の棚を名乗っていれば、そこに並ぶ会議の PDF を数える。
    assert disc.is_minutes_pdf_item(
        "令和８年第１回定例会（第１号）_本文", "", GIKAI_ROUTE,
        "https://www.vill.showa.gunma.jp/kurashi/gyousei/assembly/files/01/20260305_honbun.pdf",
        "https://www.vill.showa.gunma.jp/kurashi/gyousei/assembly/kaigiroku.html")
    assert disc.is_minutes_pdf_item(
        "5月会議", "令和8年度", GIKAI_ROUTE,
        "https://www.town.katsuura.lg.jp/_files/00661089/r8_5.pdf",
        "https://www.town.katsuura.lg.jp/gikai/kaigiroku/")
    # 会議録の棚でも、日程・名簿の PDF は数えない。
    assert not disc.is_minutes_pdf_item(
        "会期日程", "令和8年度", GIKAI_ROUTE,
        "https://www.town.katsuura.lg.jp/_files/00661090/r8_nittei.pdf",
        "https://www.town.katsuura.lg.jp/gikai/kaigiroku/")
    # 会議録の棚でない頁では、これまでどおり題名で決める。
    assert not disc.is_minutes_pdf_item(
        "5月会議", "令和8年度", GIKAI_ROUTE,
        "https://www.town.example.lg.jp/_files/1/r8_5.pdf",
        "https://www.town.example.lg.jp/gikai/nittei/")


def test_assembly_only_host_is_followed() -> None:
    # 議会だけ別ホストに置く自治体（にかほ市 www.nikahoshigikai.akita.jp）。
    hosts = {"www.city.nikaho.akita.jp"}
    assert disc.is_assembly_site("https://www.nikahoshigikai.akita.jp/conference.html", hosts)
    # 名前が違う議会サイトや、ただの外部サイトは通さない。
    assert not disc.is_assembly_site("https://www.othershigikai.akita.jp/", hosts)
    assert not disc.is_assembly_site("https://www.instagram.com/nikaho/", hosts)


def test_homepage_variants() -> None:
    # 吉岡町: www 付きが引けず、www 無しが現行サイトへ送る。
    variants = disc.homepage_variants("https://www.town.yoshioka.gunma.jp/")
    assert "https://town.yoshioka.gunma.jp/" in variants
    assert "http://town.yoshioka.gunma.jp/" in variants


def test_pdf_listing_page_becomes_a_candidate() -> None:
    # 会議録PDFが直に並ぶが、頁の題名が会議録を名乗らない（和束町）。
    home = "https://www.town.example4.lg.jp/"
    pdfs = [(f"/material/files/gijiroku_teireikai{n}.pdf", f"定例会{n}日目 (PDFファイル: 897.6KB)") for n in (1, 2, 3)]
    pages = {
        home: _page("例町", [("/gikai/", "町議会")]),
        home + "gikai/": _page("町議会", [("/gikai/r8/", "令和8年9月定例会")]),
        home + "gikai/r8/": _page("令和8年9月定例会", pdfs),
    }
    original = _with_probe({home + "gikai/r8/": 6})
    try:
        found = disc.discover_one(_FakeSession(pages), "99995", "例町", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    assert found.confidence == "medium" and found.candidate_url == home + "gikai/r8/", found


def test_entrance_page_prefers_the_administrative_side() -> None:
    # 観光の案内が先に並ぶ入口（土佐清水市）。行政側を先に開く。
    home = "https://www.city.example5.lg.jp/"
    pages = {
        home: _page("例市", [("/kanko/g01.html", "足摺岬"), ("/kanko/g02.html", "白山洞門"),
                             ("/kanko/g03.html", "見残し海岸"), ("/kanko/g04.html", "金剛福寺"),
                             ("/kanko/g05.html", "叶崎"), ("/kurashi/", "暮らしの情報")]),
        home + "kurashi/": _page("暮らしの情報", [("/kurashi/gikai/", "市議会")]),
        home + "kurashi/gikai/": _page("市議会", [("/kurashi/gikai/kaigiroku/", "会議録")]),
        home + "kurashi/gikai/kaigiroku/": _page("会議録", []),
    }
    original = _with_probe({home + "kurashi/gikai/kaigiroku/": 8})
    try:
        found = disc.discover_one(_FakeSession(pages), "99994", "例市", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    assert found.confidence == "medium", found
    assert found.candidate_url == home + "kurashi/gikai/kaigiroku/", found


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
