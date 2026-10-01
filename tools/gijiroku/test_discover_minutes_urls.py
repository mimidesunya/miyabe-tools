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
    # 勝浦市。議事日程から始まる会議録の本文で、取得側（minutes_kind）も落とさない。
    ("議事日程・本文 [PDFファイル／282KB]", "令和7年会議録", GIKAI_ROUTE, True),
    ("議事日程 [PDFファイル／82KB]", "令和7年会議録", GIKAI_ROUTE, False),
    ("1号 R7.12.15（PDF：613KB）", "令和7年 会議録 – 八丈町公式サイト", GIKAI_ROUTE, True),     # 八丈町
    ("第１号（開会から閉会）", "２０２５（令和７）年 会議録 - 室戸市", GIKAI_ROUTE, True),         # 室戸市
    # 剣淵町「会議記録 | 議決結果」。会議録・議事録を名乗らず結果を名乗る頁は会議録の棚ではない。
    ("第１回定例会（3月3日）", "会議記録 | 議決結果", GIKAI_ROUTE, False),
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


# --- 2026-10-01 第71ラウンド: 取得元未特定の人手確認で分かった探索器の穴 ----------------

def _discover(pages: dict, code: str, name: str, home: str, counts: dict | None = None,
              max_pages: int = 18):
    session = _FakeSession(pages)
    original = _with_probe(counts or {})
    try:
        found = disc.discover_one(session, code, name, home, max_pages, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original
    return found, session


def test_splash_top_page_redirects_by_script_or_frame() -> None:
    # 穴1: スプラッシュ型のトップ。リンクが無く、JavaScript か frame で本体へ送る。
    for top in (
        "<html><head><title>例村</title><script>location.href='/portal/';</script></head><body></body></html>",
        "<html><head><title>例村</title></head><frameset><frame src='/portal/'></frameset></html>",
    ):
        home = "https://www.vill.example6.lg.jp/"
        pages = {
            home: top,
            home + "portal/": _page("例村ポータル", [("/sonsei/gikai/", "村議会")]),
            home + "sonsei/gikai/": _page("村議会", [("/sonsei/gikai/kaigiroku/", "会議録")]),
            home + "sonsei/gikai/kaigiroku/": _page("会議録", []),
        }
        found, _session = _discover(pages, "99993", "例村", home, {home + "sonsei/gikai/kaigiroku/": 6})
        assert found.confidence == "medium", found
        assert found.candidate_url == home + "sonsei/gikai/kaigiroku/", found


def test_menu_relay_without_keywords_is_opened_when_the_frontier_runs_dry() -> None:
    # 穴1・3: トップのメニューに手掛かりの語が無い（東川町は議会を「町の紹介」の下に置く）。
    # 行き先が尽きたら、メニューのリンクを開く。
    # トップの「行政情報」「組織から探す」は辿るが、どちらも行き止まり。
    home = "https://www.example7.jp/"
    pages = {
        home: _page("例町", [("/gyosei/", "行政情報"), ("/soshiki/", "組織から探す"),
                             ("/portal/meguru", "町をめぐる"), ("/portal/machi", "町のこと")]),
        home + "gyosei/": _page("行政情報", []),
        home + "soshiki/": _page("組織から探す", []),
        home + "portal/meguru": _page("町をめぐる", []),
        home + "portal/machi": _page("町のこと", [("/portal/machi/panel/105", "例町議会")]),
        home + "portal/machi/panel/105": _page("例町議会", [("/portal/machi/kaigiroku", "会議録")]),
        home + "portal/machi/kaigiroku": _page("会議録", []),
    }
    found, _session = _discover(pages, "99992", "例町", home, {home + "portal/machi/kaigiroku": 5})
    assert found.confidence == "medium" and found.candidate_url == home + "portal/machi/kaigiroku", found


def test_image_only_links_are_named_by_alt() -> None:
    # 穴2: 議会へのリンクが画像だけ（粕屋町 <img alt="粕屋町議会">）。イメージマップの area も読む。
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(
        '<a href="/li/080/index.html"><img src="b.png" alt="粕屋町議会"></a>'
        '<map><area href="/gikai/" alt="町議会" coords="0,0,1,1"></map>'
        '<a href="/x/" title="議会事務局"></a>',
        "html.parser",
    )
    links = disc.iter_links(soup, "https://www.town.example.lg.jp/")
    assert ("https://www.town.example.lg.jp/li/080/index.html", "粕屋町議会") in links, links
    assert ("https://www.town.example.lg.jp/gikai/", "町議会") in links, links
    assert ("https://www.town.example.lg.jp/x/", "議会事務局") in links, links

    home = "https://www.town.example8.lg.jp/"
    noise = [(f"/kurashi/{n}.html", f"お知らせ{n}") for n in range(12)]
    image_link = '<a href="/li/080/index.html"><img src="gikai.png" alt="例町議会"></a>'
    pages = {
        home: _page("例町", noise).replace("</body>", image_link + "</body>"),
        home + "li/080/index.html": _page("例町議会", [("/li/080/030/index.html", "会議録")]),
        home + "li/080/030/index.html": _page("会議録", []),
    }
    found, _session = _discover(pages, "99991", "例町", home, {home + "li/080/030/index.html": 9})
    assert found.confidence == "medium" and found.candidate_url == home + "li/080/030/index.html", found


def test_relay_pages_named_without_the_minutes_word() -> None:
    # 穴3: 中継ページの名前が会議録を含まない（北島町「会議結果の閲覧」・豊富町「議事結果等一覧」）。
    home_host = "www.town.example.lg.jp"
    assert disc.link_priority("https://www.town.example.lg.jp/gikai/kekka/", "会議結果の閲覧", home_host) >= 20
    assert disc.link_priority("https://www.town.example.lg.jp/section/a.html", "議事結果等一覧", home_host) >= 20
    assert disc.link_priority("https://www.town.example.lg.jp/k/kiroku/", "会議の記録", home_host) >= 20
    assert disc.link_priority("https://www.town.example.lg.jp/portal/machi", "町の紹介", home_host) >= 6
    # 議会だよりは議会の語があっても辿らない。
    assert disc.link_priority("https://www.town.example.lg.jp/dayori/", "議会だより", home_host) < 6

    home = "https://www.town.example9.lg.jp/"
    encoded = "/fs/6/4/%E4%BC%9A%E8%AD%B0%E9%8C%B2HP%E7%94%A8%20R{n}.pdf"   # 会議録HP用 R{n}.pdf
    pdfs = [(encoded.format(n=n), f"第{n}回定例会 (PDF 698KB)") for n in (1, 2, 3, 4)]
    pages = {
        home: _page("例町", [("/gikai/", "町議会")]),
        home + "gikai/": _page("町議会 - 例町", [("/gikai/bocho/", "議会の傍聴"), ("/gikai/kekka/", "会議結果の閲覧")]),
        home + "gikai/kekka/": _page("会議結果の閲覧 - 例町", [("/docs/402721.html", "会議結果の閲覧(平成29年～)")]),
        home + "docs/402721.html": _page("会議結果の閲覧(平成29年～) - 例町", pdfs),
    }
    found, _session = _discover(pages, "99990", "例町", home, {home + "docs/402721.html": 8})
    assert found.confidence == "medium" and found.candidate_url == home + "docs/402721.html", found


def test_pdf_named_only_by_its_size_takes_the_page_name() -> None:
    # 三沢市「会議録の閲覧（定例会・臨時会）」: PDF のリンク文字列が「[248KB pdf]」だけ。
    # 取得側と同じく、置き場の頁の名前で呼ぶ。
    assert disc.is_file_note_only("[248KB pdf]")
    assert disc.is_file_note_only("(PDF：1.2MB)")
    assert not disc.is_file_note_only("議事日程・本文 [PDFファイル／282KB]")
    assert disc.pdf_item_kind("[248KB pdf]", "会議録の閲覧（定例会・臨時会）", GIKAI_ROUTE) == "minutes"
    assert disc.pdf_item_kind("[248KB pdf]", "議会だより", GIKAI_ROUTE) == ""


def test_deep_links_whose_url_names_the_assembly_are_followed() -> None:
    # 穴3・4: 浦河町は会議録を「審議の結果」→ 年 → 本会議 → 会議の頁と、議会の区画
    # （/gyosei/council/）の奥に置く。リンク文字列に手掛かりが無くても、URL が議会を
    # 名乗るリンクは深く辿る。手掛かりの無いリンクは深い階層では辿らない。
    home = "https://www.town.example17.lg.jp/"
    council = home + "gyosei/council/"
    pages = {
        home: _page("例町", [("/gyosei/council/", "町議会")]),
        council: _page("町議会", [("/gyosei/council/?category=220", "審議の結果")]),
        council + "?category=220": _page("審議の結果", [("/gyosei/council/?category=330", "2025年（令和7年分）")]),
        council + "?category=330": _page("2025年（令和7年分）", [("/gyosei/council/?category=331", "一覧"),
                                                                  ("/gyosei/kurashi/?category=9", "一覧")]),
        council + "?category=331": _page("会議録（令和7年）", []),
    }
    found, session = _discover(pages, "99982", "例町", home, {council + "?category=331": 8})
    assert found.confidence == "medium" and found.candidate_url == council + "?category=331", found
    assert home + "gyosei/kurashi/?category=9" not in session.requested, session.requested


def test_minutes_section_inside_an_assembly_page() -> None:
    # 穴3: 「議会」の頁の中の節に会議録が並ぶ（豊富町「議事録」の節）。隣の「議事日程・結果」
    # の節にも同じ形の PDF が並ぶが、そちらは数えない。
    from bs4 import BeautifulSoup
    html = (
        "<h1>議事結果等一覧</h1>"
        "<h2>議事録</h2>"
        + "".join(f'<a href="/att/m{n}.pdf">第{n}回定例議会（令和6年3月{n}日開催）</a>' for n in (1, 2, 3))
        + "<h2>議事日程・結果</h2>"
        + "".join(f'<a href="/att/k{n}.pdf">第{n}回定例会（令和7年3月{n}日開催）</a>' for n in (1, 2, 3))
    )
    soup = BeautifulSoup(html, "html.parser")
    kinds = {}
    for url, label, section in disc.pdf_links_with_sections(soup, "https://www.town.example.lg.jp/s/a.html"):
        kinds[url.rsplit("/", 1)[-1]] = disc.pdf_item_kind(label, disc.section_page_title(section, "議事結果等一覧"), GIKAI_ROUTE, url)
    assert kinds == {"m1.pdf": "minutes", "m2.pdf": "minutes", "m3.pdf": "minutes",
                     "k1.pdf": "", "k2.pdf": "", "k3.pdf": ""}, kinds


def test_minutes_four_to_six_levels_deep() -> None:
    # 穴4: 一覧がトップから 5 段（藤崎町: トップ → 入口 → 議会 → 定例会・臨時会 → 年 → PDF）。
    # 会議録の手掛かりがあるリンクだけを深く辿り、年別の頁の親の棚を入口にする。
    home = "https://www.town.example10.lg.jp/"
    year_pdfs = [(f"/f/{n}.pdf", f"令和7年第{n}回定例会会議録.pdf [ 369 KB pdfファイル]") for n in (1, 2, 3)]
    pages = {
        home: _page("例町", [("/index.cfm/1,html", "例町公式ホームページ")]),
        home + "index.cfm/1,html": _page("例町ホーム", [("/index.cfm/9,html", "行政情報")]),
        home + "index.cfm/9,html": _page("行政情報", [("/index.cfm/9,0,83,html", "例町議会"), ("/kanko/", "観光案内")]),
        home + "index.cfm/9,0,83,html": _page("例町議会", [("/index.cfm/9,0,83,221,html", "定例会・臨時会"),
                                                         ("/index.cfm/9,0,83,9,html", "議員の紹介")]),
        home + "index.cfm/9,0,83,221,html": _page("定例会・臨時会", [("/index.cfm/9,20469,83,221,html", "令和7年 定例会・臨時会"),
                                                                   ("/index.cfm/9,18933,83,221,html", "令和6年 定例会・臨時会"),
                                                                   ("/index.cfm/9,0,10,html", "お問い合わせ")]),
        home + "index.cfm/9,20469,83,221,html": _page("令和7年 定例会・臨時会", year_pdfs),
    }
    found, session = _discover(pages, "99989", "例町", home, {home + "index.cfm/9,0,83,221,html": 30})
    assert found.confidence == "medium", found
    assert found.candidate_url == home + "index.cfm/9,0,83,221,html", found
    # 手掛かりの無いリンクは深い階層で開かない。
    assert home + "index.cfm/9,0,10,html" not in session.requested, session.requested


def test_year_shelf_links_without_keywords_are_followed_and_checked_by_body() -> None:
    # 穴4: 「定例会・臨時会」の棚の年のリンクが「2026年(令和8年)」だけで、PDF の題名も
    # 会議録を名乗らない（白川町）。年の頁を開き、会議の PDF の本文を見て決める。
    home = "https://www.town.example11.lg.jp/"
    pdfs = [(f"/res/r8_teireikai1_{n}.pdf", f"第1回定例会（第{n}日） （PDF 417.5KB）") for n in (1, 2, 3)]
    pages = {
        home: _page("例町", [("/chousei/gikai/index.html", "町議会")]),
        home + "chousei/gikai/index.html": _page("町議会", [("/chousei/gikai/1002058/index.html", "定例会・臨時会")]),
        home + "chousei/gikai/1002058/index.html": _page("定例会・臨時会", [("/chousei/gikai/1002058/1003304.html", "2026年(令和8年)"),
                                                                          ("/chousei/gikai/1002058/1002880.html", "2025年(令和7年)")]),
        home + "chousei/gikai/1002058/1003304.html": _page("2026年(令和8年)", pdfs),
    }
    original_probe = disc.probe_minutes_pdfs
    original_body = disc.check_minutes_body

    def fake_probe(_session, url, _timeout, _delay, **_kwargs):
        meetings = 6 if url == home + "chousei/gikai/1002058/index.html" else 0
        return {"items": meetings, "minutes": 0, "meetings": meetings, "pages": 3, "examples": [],
                "samples": [("第1回定例会（第1日）", home + "res/r8_teireikai1_1.pdf")] if meetings else []}

    for body, expected in (((True, "本文確認"), "medium"), ((False, "本文が会議録でない（agenda_only）"), "low")):
        disc.probe_minutes_pdfs = fake_probe
        disc.check_minutes_body = lambda *_args, _body=body: _body
        try:
            found = disc.discover_one(_FakeSession(pages), "99988", "例町", home, 18, 3, 5.0, 0.0)
        finally:
            disc.probe_minutes_pdfs = original_probe
            disc.check_minutes_body = original_body
        assert found.confidence == expected, (body, found)
        if expected == "medium":
            assert found.candidate_url == home + "chousei/gikai/1002058/index.html", found
            assert found.system_type == "独自", found


def test_year_or_meeting_page_prefers_the_minutes_shelf() -> None:
    # 穴4: 深い年・会議の頁で見つけたら、全年が並ぶ棚を入口にする。
    # 勝浦市は議会トップから「令和8年会議録」へ直に行け、その頁が棚「会議録」へリンクする。
    home = "https://www.city.example13.lg.jp/"
    pdfs = [(f"/uploaded/attachment/{n}.pdf", "議事日程・本文 [PDFファイル／282KB]") for n in (1, 2, 3)]
    pages = {
        home: _page("例市", [("/site/gikai/", "例市議会")]),
        home + "site/gikai/": _page("例市議会", [("/site/gikai/11726.html", "令和8年会議録")]),
        home + "site/gikai/11726.html": _page("令和8年会議録 - 例市議会", pdfs + [("/site/gikai/list27-103.html", "会議録")]),
    }
    found, _session = _discover(pages, "99986", "例市", home,
                                {home + "site/gikai/list27-103.html": 20, home + "site/gikai/11726.html": 6})
    assert found.candidate_url == home + "site/gikai/list27-103.html", found

    # 粕屋町: 議会トップに年で始まるリンクが並んでも、名前が違えば年の棚ではない。
    # 「令和8年 議会会議録」の頁からは、棚「議会会議録」を入口にする（議会トップではない）。
    home = "https://www.town.example14.lg.jp/"
    top = home + "li/080/index.html"
    meeting = home + "s043/030/160/1.html"
    shelf = home + "li/080/030/index.html"
    pdfs = [(f"/s043/030/160/R8-{n}.pdf", f"第{n}回（6月）定例会議録") for n in (1, 2, 3)]
    pages = {
        home: _page("例町", [("/li/080/index.html", "例町議会")]),
        top: _page("例町議会", [("/s043/050/1.html", "令和8年 一般質問通告書"), ("/s043/030/160/1.html", "令和8年 議会会議録"),
                                ("/li/080/030/index.html", "議会会議録")]),
        meeting: _page("令和8年 議会会議録｜例町", pdfs + [("/li/080/030/index.html", "議会会議録")]),
    }
    found, _session = _discover(pages, "99985", "例町", home, {top: 9, shelf: 9, meeting: 9})
    assert found.candidate_url == shelf, found


def test_list_of_one_meeting_articles_is_the_entry() -> None:
    # 五木村: 会議録は 1 回分ずつ「令和8年第2回定例会会議録の公開」の記事になり、記事 1 つに
    # PDF が 1 件しかない。記事が並ぶ一覧を入口として確かめる。
    home = "https://www.vill.example18.lg.jp/"
    articles = [(f"/kiji{n}/index.html", f"令和8年第{n}回定例会会議録の公開") for n in (1, 2, 3)]
    pages = {
        home: _page("例村", [("/gikai/list00354.html", "村議会")]),
        home + "gikai/list00354.html": _page("村議会 / 例村", articles),
    }
    found, _session = _discover(pages, "99981", "例村", home, {home + "gikai/list00354.html": 4})
    assert found.confidence == "medium" and found.candidate_url == home + "gikai/list00354.html", found


def test_vendor_search_system_behind_the_minutes_page() -> None:
    # 城里町: 自ホストの会議録は令和3年まで。新しい年はベンダの検索システムにあり、
    # 会議録の頁から「会議録検索システム」の頁を挟んでリンクしている。
    home = "https://www.town.example15.lg.jp/"
    pdfs = [(f"/data/doc/{n}.pdf", f"{n}号ー本文") for n in (1, 2, 3)]
    pages = {
        home: _page("例町", [("/gikai/", "例町議会")]),
        home + "gikai/": _page("例町議会", [("/gikai/kaigiroku/index.html", "会議録")]),
        home + "gikai/kaigiroku/index.html": _page("会議録 | 例町", pdfs + [("/gikai/kaigiroku/page7437.html", "会議録検索システム（令和3年以降）")]),
        home + "gikai/kaigiroku/page7437.html": _page("会議録検索システム", [("https://ssp.kaigiroku.net/tenant/reicho/SpTop.html", "会議録検索システム")]),
    }
    found, _session = _discover(pages, "99984", "例町", home, {home + "gikai/kaigiroku/index.html": 12})
    assert found.confidence == "high" and found.system_type == "kaigiroku.net", found
    assert found.candidate_url == "https://ssp.kaigiroku.net/tenant/reicho/SpTop.html", found


def test_single_year_page_the_scraper_cannot_leave_stays_low() -> None:
    # 月形町: 会議録は「令和8年 会議結果」の年の頁にあるが、年のリンクの名前に会議録の語が
    # 無いので、取得側は議会トップからも年の頁からも他の年へ辿れない。年の頁を入口にすると
    # その年の分しか取れないので、取得の対象にはせず人に回す。
    home = "https://www.town.example16.lg.jp/"
    top = home + "life/6/23/111/"
    year8 = home + "page/6952.html"
    pdfs = [(f"/uploaded/attachment/{n}.pdf", f"3月{n}日 [PDFファイル／367KB]") for n in (3, 4, 13)]
    years = [("/page/6952.html", "令和8年 会議結果"), ("/page/4071.html", "令和7年 会議結果")]
    pages = {
        home: _page("例町", [("/life/6/23/111/", "町議会")]),
        top: _page("町議会 - 例町", years),
        year8: _page("令和8年 会議結果 - 例町", pdfs + years + [("/life/6/23/111/", "町議会")]),
    }
    original_probe = disc.probe_minutes_pdfs
    original_body = disc.check_minutes_body

    def fake_probe(_session, url, _timeout, _delay, **_kwargs):
        meetings = 6 if url == year8 else 0
        return {"items": meetings, "minutes": 0, "meetings": meetings, "pages": 1, "examples": [],
                "samples": [("3月3日", home + "uploaded/attachment/3.pdf")] if meetings else []}

    disc.probe_minutes_pdfs = fake_probe
    disc.check_minutes_body = lambda *_args: (True, "本文確認: 3月3日")
    try:
        found = disc.discover_one(_FakeSession(pages), "99983", "例町", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original_probe
        disc.check_minutes_body = original_body
    assert found.confidence == "low" and found.candidate_url == year8, found
    assert "年の頁しか入口にできない" in found.note, found


def test_assembly_site_on_another_host() -> None:
    # 穴5: 議会が独自のドメインにある。公式サイトから議会のリンクで来て、頁が自治体名を名乗り、
    # 公式サイトへリンクしていれば、その自治体のサイトとして扱う。
    home = "https://www.town.example12.lg.jp/"
    other = "https://www.reigikai-site.jp/"
    pages = {
        home: _page("例町", [(other, "町議会のページ")]),
        other: _page("例町議会", [("/kaigiroku/", "会議録"), (home, "例町役場")]),
        other + "kaigiroku/": _page("会議録 - 例町議会", []),
    }
    found, _session = _discover(pages, "99987", "例町", home, {other + "kaigiroku/": 7})
    assert found.confidence == "medium" and found.candidate_url == other + "kaigiroku/", found

    # 別の自治体を名乗る頁は、リンクされていても候補にしない。
    pages[other] = _page("他町議会", [("/kaigiroku/", "会議録"), (home, "例町役場")])
    pages[other + "kaigiroku/"] = _page("会議録 - 他町議会", [])
    found, _session = _discover(pages, "99987", "例町", home, {other + "kaigiroku/": 7})
    assert found.confidence != "medium", found


def _with_master(rows: dict):
    original = disc._MASTER_ROWS
    merged = dict(disc.master_rows())
    merged.update(rows)
    disc._MASTER_ROWS = merged
    return original


def test_tenant_tokens_strip_host_derived_romaji() -> None:
    original = _with_master({
        "99901": {"jis_code": "99901", "entity_type": "municipality", "name": "南牧例村", "name_romaji": "minamimakimura-mura"},
        "99902": {"jis_code": "99902", "entity_type": "municipality", "name": "京例町", "name_romaji": "town-kyogoku-cho"},
        "99903": {"jis_code": "99903", "entity_type": "municipality", "name": "上川例町", "name_romaji": "hokkaido-kamikawa-cho"},
        "99904": {"jis_code": "99904", "entity_type": "municipality", "name": "大村例市", "name_romaji": "omura-shi"},
    })
    try:
        assert disc.tenant_tokens("99901") == ["minamimaki"]
        assert disc.tenant_tokens("99902") == ["kyogoku"]
        assert disc.tenant_tokens("99903") == ["kamikawa"]
        assert disc.tenant_tokens("99904") == ["omura"]   # 種別が違う「mura」は外さない
    finally:
        disc._MASTER_ROWS = original


def _kensaku(tenant: str, title: str, home: str, see_text: str = "令和 8年 3月定例会") -> dict:
    """kensakusystem のテナントのトップと閲覧の頁（See.exe）。"""
    top = f"https://www.kensakusystem.jp/{tenant}/index.html"
    return {
        top: (f"<html><head><title>{title}</title></head><body>"
              f'<map name="head"><area href="{home}" coords="0,0,1,1"></map>'
              '<a href="cgi-bin3/See.exe?Code=abc"><img alt="会議録の閲覧"></a></body></html>'),
        f"https://www.kensakusystem.jp/{tenant}/cgi-bin3/See.exe?Code=abc": f"<html><body>{see_text}</body></html>",
    }


def test_unlinked_vendor_tenant_is_guessed_and_verified() -> None:
    # 穴5: 公式サイトのどこからもリンクされていない kensakusystem のテナント（上松町・木祖村）。
    home = "https://www.vill.reiso.nagano.jp/"
    original = _with_master({"99905": {"jis_code": "99905", "entity_type": "municipality",
                                       "name": "例祖村", "name_romaji": "reiso-mura"}})
    try:
        pages = {
            home: _page("例祖村", [("/gikai/", "村議会")]),
            home + "gikai/": _page("議会事務局", []),
            **_kensaku("reiso", "例祖村議会/会議録検索システム", home),
        }
        found, _session = _discover(pages, "99905", "例祖村", home)
        # 確かめても推したテナントは low（定期の探索で人の確認なしに載せない）。
        assert found.system_type == "kensakusystem" and found.confidence == "low", found
        assert "名乗りと公式サイトへのリンクあり" in found.note, found
        assert found.candidate_url == "https://www.kensakusystem.jp/reiso/index.html", found

        # 頁はあるが会議録が登録されていないテナントは採らない（十津川村）。
        pages.update(_kensaku("reiso", "例祖村議会/会議録検索システム", home, "議会名が登録されていません。"))
        found, _session = _discover(pages, "99905", "例祖村", home)
        assert found.system_type == "" and "閲覧の頁に会議が無い" in found.note, found

        # kaigiroku.net は message.js に自治体名と公式サイトへのリンクがある（城里町）。
        pages.pop("https://www.kensakusystem.jp/reiso/index.html")
        pages["https://ssp.kaigiroku.net/tenant/reiso/js/message.js"] = (
            f"'#landing-footer' : '<li><a href=\"{home}\">例祖村HP</a></li>'"
        )
        found, _session = _discover(pages, "99905", "例祖村", home)
        assert found.system_type == "kaigiroku.net" and found.confidence == "low", found
        assert found.candidate_url == "https://ssp.kaigiroku.net/tenant/reiso/SpMinuteSearch.html", found
    finally:
        disc._MASTER_ROWS = original


def test_same_romaji_tenant_of_another_municipality_is_rejected() -> None:
    # 罠1: 同じローマ字の別の自治体のテナント。kensakusystem の kiso は木祖村のもので、
    # 木曽町（kiso-machi）の会議録ではない。
    home = "https://www.town.reikiso.nagano.jp/"
    original = _with_master({"99906": {"jis_code": "99906", "entity_type": "municipality",
                                       "name": "例曽町", "name_romaji": "reikiso-machi"}})
    try:
        pages = {
            home: _page("例曽町", []),
            **_kensaku("reikiso", "例祖村議会/会議録検索システム", "https://www.vill.reikiso.nagano.jp/"),
        }
        found, _session = _discover(pages, "99906", "例曽町", home)
        assert found.system_type == "" and found.confidence == "none", found
        assert "例曽町を名乗らない" in found.note, found
    finally:
        disc._MASTER_ROWS = original


def test_same_named_town_tenant_needs_a_link_to_our_own_host() -> None:
    # 罠1: 同じ名前の町が別の県にある。kensakusystem の asahi は三重県朝日町のもので、
    # 山形県朝日町ではない。town.asahi.* の組が同じでも、自分の公式サイトのホストへの
    # リンクが無ければ採らない。
    home = "https://www.town.reihi.yamagata.jp/"
    original = _with_master({
        "99907": {"jis_code": "99907", "entity_type": "municipality", "name": "例日町", "name_romaji": "reihi-machi"},
        "99908": {"jis_code": "99908", "entity_type": "municipality", "name": "例日町", "name_romaji": "reihi-cho"},
    })
    try:
        pages = {
            home: _page("例日町", []),
            **_kensaku("reihi", "例日町議会/会議録検索システム", "http://www2.town.reihi.mie.jp/www/index.html"),
        }
        found, _session = _discover(pages, "99907", "例日町", home)
        assert found.system_type == "" and found.confidence == "none", found
        assert "同名の自治体" in found.note, found
        # 自分のホストへリンクしていれば候補に出す（人が確かめて登録する）。
        pages.update(_kensaku("reihi", "例日町議会/会議録検索システム", home))
        found, _session = _discover(pages, "99907", "例日町", home)
        assert found.system_type == "kensakusystem" and found.confidence == "low", found
    finally:
        disc._MASTER_ROWS = original


def test_unreadable_samples_without_named_minutes_stay_low() -> None:
    # 香春町「令和8年第2回議会定例会のお知らせ」: 名前が「(PDF:262KB)」だけの PDF を取得側は
    # 「会議録」と呼ぶ。本文を読めず、頁のリンク文字列でも会議録と分からないなら人に回す。
    home = "https://www.town.example19.lg.jp/"
    notice = home + "s036/gikai/090/1.html"
    pdfs = [("/s036/gikai/090/nittei1.pdf", "議事日程（第1号）"), ("/s036/gikai/090/nittei1.pdf", "(PDF:262KB)"),
            ("/s036/gikai/090/tsuukoku.pdf", "一般質問通告一覧"), ("/s036/gikai/090/tsuukoku.pdf", "(PDF:3,966KB)")]
    pages = {
        home: _page("例町", [("/s036/gikai/", "町議会")]),
        home + "s036/gikai/": _page("町議会", [("/s036/gikai/090/1.html", "令和8年第2回議会定例会会議録")]),
        notice: _page("令和8年第2回議会定例会（6月16日開会）のお知らせ", pdfs),
    }
    original_probe = disc.probe_minutes_pdfs

    def fake_probe(_session, url, _timeout, _delay, **_kwargs):
        hit = url == notice
        return {"items": 3 if hit else 0, "minutes": 3 if hit else 0, "meetings": 0, "pages": 1,
                "examples": ["会議録"] * (3 if hit else 0),
                "samples": [("会議録", home + "s036/gikai/090/nittei1.pdf")] if hit else []}

    disc.probe_minutes_pdfs = fake_probe
    try:
        found = disc.discover_one(_FakeSession(pages), "99980", "例町", home, 18, 3, 5.0, 0.0)
    finally:
        disc.probe_minutes_pdfs = original_probe
    assert found.confidence == "low" and found.candidate_url == notice, found
    assert "本文を読めない" in found.note, found


def test_results_only_page_is_not_minutes() -> None:
    # 罠2: 「会議記録」の頁が議決結果だけ（剣淵町）。会議録の入口にも、会議録の棚にもしない。
    assert not disc.is_minutes_entry_title("会議記録 | 議決結果")
    assert disc.is_minutes_entry_title("会議結果・会議録")
    assert disc.pdf_item_kind("第１回定例会（3月3日）", "会議記録 | 議決結果", GIKAI_ROUTE) == ""
    assert disc.pdf_item_kind("→第１回町議会定例会議決結果（3月3日、18日）", "会議記録", GIKAI_ROUTE) == ""


def test_body_check_tells_minutes_from_results_and_schedules() -> None:
    # 罠2: 題名では分からない PDF は本文で決める。取得側と同じ判定（minutes_kind）を使う。
    minutes = (
        "令和７年第１回例町議会定例会会議録\n令和７年３月１０日（月曜日）\n出席議員（１０名）\n"
        "欠席議員（なし）\n午前１０時００分 開会\n○議長（例田一郎君） ただいまから会議を開きます。\n"
    )
    results = (
        "令和７年第１回定例会 議決結果\n議案第１号 例町税条例の一部を改正する条例 原案可決\n"
        "議案第２号 令和７年度例町一般会計予算 原案可決\n"
    )
    schedule = (
        "令和７年第１回例町議会定例会 日程表\n３月１０日（月） 開会 会期の決定 議案上程 提案理由の説明\n"
        "３月１２日（水） 一般質問\n３月１３日（木） 議案審議\n３月１４日（金） 閉会\n"
    )
    assert disc.judge_minutes_body("第1回定例会（第1日）", minutes)[0] is True
    assert disc.judge_minutes_body("第1回定例会", results)[0] is False
    assert disc.judge_minutes_body("第1回定例会", schedule)[0] is False
    assert disc.judge_minutes_body("第1回定例会", "")[0] is None
    # 大島町: 特別委員会の「報告」。委員長・副委員長の語はあるが、会議の記録ではない。
    report = (
        "第19回議会基本条例制定特別委員会報告\n令和5年2月16日（木）に第19回議会基本条例制定特別委員会を"
        "開催しました。議論の内容等については、下記のとおりです。\n１．議員からの意見、質問について\n"
        "議員から提出された意見、質問について委員長が回答した。副委員長から補足があった。\n"
        "議会基本条例制定特別委員会\n委員長 例田一郎\n副委員長 例川二郎\n議長（オブザーバー） 例山三郎\n"
    )
    assert disc.judge_minutes_body("第19回（令和5年2月16日開催）", report)[0] is False
    # 北島町: 冒頭が会議録を名乗る要旨の形（議案の概要と結果、一般質問の質問と答弁）は会議録。
    summary = (
        "会議録\n例町議会\n令和８年第３回定例会は、９月４日に開会され、９月１８日に閉会いたしました。\n"
        "議案第５５号 例町医療費の助成に関する条例の一部改正について\n原案可決\n一般質問\n"
        "質問者 例田\n（質問１）ごみ処理有料化に伴う問題について伺う。\n（答弁）環境課長 有料とする予定です。\n"
    )
    assert disc.judge_minutes_body("第3回定例会", summary)[0] is True
    # 開成町: 議会の頁から辿れても、本文が教育委員会の会議録なら議会の会議録ではない。
    board = (
        "令和７年4月例町教育委員会定例会会議録\n日時： 令和７年4月25日(金) 15時00分～17時00分\n"
        "場所： 例町役場201会議室\n出席委員 例田教育長、例川委員\n会議録署名委員の指名\n"
        "○教育長（例田） ただいまから4月定例会を開会します。\n"
    )
    assert disc.judge_minutes_body("議事録", board) == (False, "議会以外の会議の会議録")
    # 議会の会議録の議事日程に「教育委員会委員の任命」が出ても、議会の会議録である。
    agenda_item = minutes.replace("午前１０時００分 開会", "日程第４ 議案第３２号 例町教育委員会委員の任命について\n午前１０時００分 開会")
    assert disc.judge_minutes_body("第1回定例会（第1日）", agenda_item)[0] is True


def test_year_titles_and_links() -> None:
    assert disc.is_year_only_title("令和7年1月～")
    assert disc.is_year_only_title("令和7年 | 鮫川村公式ホームページ")
    assert disc.is_year_only_title("2026年(令和8年)")
    assert not disc.is_year_only_title("令和7年 会議録")
    assert disc.is_year_link("２０２５(令和７)年 会議録")
    assert disc.is_year_link("令和7年 定例会・臨時会")
    assert not disc.is_year_link("第3回定例会")


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
