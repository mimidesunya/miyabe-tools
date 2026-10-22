import unittest

from tools.gijiroku.scrapers import html_list_sites


ZENBUN_URL = "https://www3.pref.iwate.jp/gikai/user/www/Zenbun/"

# 入口のツリー。年 → 定例会 → 会議種別（目次へのリンク）の順に並ぶ。
ZENBUN_INDEX = """
<ul><li><a class="noline" href="#" onclick="clickTree(&#39;令和8年&#39;);"><span>令和8年</span></a>
  <span id="令和8年Children"><ul>
    <li><a class="noline" href="#" onclick="clickTree(&#39;令和8年2月定例会&#39;);"><span>令和8年2月定例会</span></a>
      <span><ul><li><a href="/gikai/user/www/Zenbun/mokuji/351" shape="rect"><span>予算特別委員会</span></a></li></ul>
      <ul><li><a href="/gikai/user/www/Zenbun/mokuji/352" shape="rect"><span>本会議</span></a></li></ul></span>
    </li></ul></span></li></ul>
<ul><li><a class="noline" href="#" onclick="clickTree(&#39;平成7年&#39;);"><span>平成7年</span></a>
  <span><ul><li><a class="noline" href="#" onclick="clickTree(&#39;平成7年12月定例会&#39;);"><span>平成7年12月定例会</span></a>
    <span><ul><li><a href="/gikai/user/www/Zenbun/mokuji/92" shape="rect"><span>本会議</span></a></li></ul></span>
  </li></ul></span></li></ul>
"""

# 目次。日の見出しのあとに、その日の発言者ごとの範囲が続く。
MOKUJI_352 = """
<a href="/gikai/user/www/Zenbun/page/352/1180240/1180270">第１号（２月13日）</a>
<a href="/gikai/user/www/Zenbun/page/352/1180271/1180293">佐々木（朋）議員</a>
<a href="/gikai/user/www/Zenbun/page/352/1180294/1180299">佐藤教育委員会教育長</a>
<a href="/gikai/user/www/Zenbun/page/352/1180300/1180310">第２号（２月19日）</a>
<a href="/gikai/user/www/Zenbun/page/352/1180311/1180432">臼澤議員</a>
"""
MOKUJI_351 = '<a href="/gikai/user/www/Zenbun/page/351/1172774/1173098">第１号<br>３月４日（水）</a>'
MOKUJI_92 = '<a href="/gikai/user/www/Zenbun/page/92/10/20">第１号（12月１日）</a>'

PAGE_HTML = """<html><body>
<table><tr><td>令和8年2月定例会　第15回岩手県議会定例会会議録</td></tr></table>
<table><tr><td>前へ</td><td><a href="/gikai/user/www/Zenbun/page/352/1180271/1180293">次へ</a></td></tr></table>
<div style="width:600px;max-width:600px;word-break:break-all;">第 15 回 岩 手 県 議 会 定 例 会 会 議 録（第１号）<br>〇議長（城内愛彦君）　これより本日の会議を開きます。<br></div>
<table><tr><td>前へ</td><td>次へ</td></tr></table>
</body></html>"""


class IwateKengikaiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_fetch = html_list_sites.fetch
        self.original_sleep = html_list_sites.time.sleep
        html_list_sites.time.sleep = lambda _seconds: None
        pages = {
            ZENBUN_URL: ZENBUN_INDEX,
            ZENBUN_URL + "mokuji/351": MOKUJI_351,
            ZENBUN_URL + "mokuji/352": MOKUJI_352,
            ZENBUN_URL + "mokuji/92": MOKUJI_92,
            ZENBUN_URL + "page/352/1180240/1180299": PAGE_HTML,
        }

        def fetch(_session, url, **_kwargs):
            if url not in pages:
                raise RuntimeError(f"404: {url}")
            return pages[url]

        html_list_sites.fetch = fetch

    def tearDown(self) -> None:
        html_list_sites.fetch = self.original_fetch
        html_list_sites.time.sleep = self.original_sleep

    def test_one_meeting_per_day_spanning_its_speakers(self) -> None:
        days = html_list_sites.IwateKengikai.day_ranges(MOKUJI_352, ZENBUN_URL + "mokuji/352")

        self.assertEqual(
            days,
            [
                ("第１号（２月13日）", ZENBUN_URL + "page/352/1180240/1180299"),
                ("第２号（２月19日）", ZENBUN_URL + "page/352/1180300/1180432"),
            ],
        )

    def test_tree_gives_session_kind_and_year(self) -> None:
        walk: dict = {}

        items = html_list_sites.IwateKengikai().discover(None, ZENBUN_URL, 1_000, walk)

        self.assertEqual(
            [(item.title, item.year_label, item.meeting_group) for item in items],
            [
                ("令和8年2月定例会 予算特別委員会 第１号 ３月４日（水）", "令和8年", "予算特別委員会"),
                ("令和8年2月定例会 本会議 第１号（２月13日）", "令和8年", "本会議"),
                ("令和8年2月定例会 本会議 第２号（２月19日）", "令和8年", "本会議"),
                ("平成7年12月定例会 本会議 第１号（12月１日）", "平成7年", "本会議"),
            ],
        )
        self.assertEqual(walk["missed_pages"], 0)

    def test_unopened_table_of_contents_is_counted(self) -> None:
        # 目次を開けなければ、その会議の日はまるごと見えない。数えて残す。
        del_url = ZENBUN_URL + "mokuji/92"
        original = html_list_sites.fetch

        def fetch(session, url, **kwargs):
            if url == del_url:
                raise RuntimeError("timeout")
            return original(session, url, **kwargs)

        html_list_sites.fetch = fetch
        walk: dict = {}

        items = html_list_sites.IwateKengikai().discover(None, ZENBUN_URL, 1_000, walk)

        self.assertEqual(len(items), 3)
        self.assertEqual(walk["missed_pages"], 1)

    def test_body_is_the_minutes_without_navigation(self) -> None:
        item = html_list_sites.MeetingItem(
            title="令和8年2月定例会 本会議 第１号（２月13日）",
            url=ZENBUN_URL + "page/352/1180240/1180299",
            year_label="令和8年",
            meeting_group="本会議",
        )

        text = html_list_sites.IwateKengikai().fetch_text(None, item, 1_000)

        self.assertTrue(text.startswith("第 15 回 岩 手 県 議 会 定 例 会 会 議 録（第１号）"))
        self.assertIn("これより本日の会議を開きます。", text)
        self.assertNotIn("次へ", text)


class _FakeResponse:
    def __init__(self, status: int, text: str = "") -> None:
        import requests

        self.status_code = status
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self._requests = requests

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise self._requests.HTTPError(f"{self.status_code}", response=self)


class _FakeSession:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes
        self.timeouts: list[float] = []

    def get(self, url, headers=None, timeout=None):
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FetchRetryTest(unittest.TestCase):
    """時間切れ・接続断 1 回で一覧のページを諦めない（出水市）。"""

    def setUp(self) -> None:
        from unittest import mock

        html_list_sites._consecutive_exhausted_fetches = 0
        self.addCleanup(setattr, html_list_sites, "_consecutive_exhausted_fetches", 0)
        patcher = mock.patch.object(html_list_sites.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_timeout_is_retried_with_a_longer_wait(self) -> None:
        import requests

        session = _FakeSession([requests.Timeout("read timed out"), _FakeResponse(200, "ok")])

        self.assertEqual(html_list_sites.fetch(session, "https://example.jp/detail_select/1", timeout_ms=10_000), "ok")
        self.assertEqual(session.timeouts, [10.0, html_list_sites.FETCH_RETRY_MIN_TIMEOUT_MS / 1000.0])

    def test_not_found_is_not_retried(self) -> None:
        import requests

        session = _FakeSession([_FakeResponse(404)])

        with self.assertRaises(requests.HTTPError):
            html_list_sites.fetch(session, "https://example.jp/detail_select/9999", timeout_ms=10_000)
        self.assertEqual(len(session.timeouts), 1)


VOICETECHNO_URL = "https://www.voicetechno.net/MinutesSystem/Kama/"
H29_MARCH = [f"平成29年 第１回嘉麻市議会定例会会議録（第{d}日：平成29年３月{d + 1}日）" for d in (1, 2, 3)]
H29_MAY = ["平成29年 第１回嘉麻市議会臨時会会議録（第１日：平成29年５月９日）"]
H29_JUNE = [f"平成29年 第２回嘉麻市議会定例会会議録（第{d}日：平成29年６月{d + 8}日）" for d in (1, 2)]
# 年 → 種別 → 開催回数 → 会議名。嘉麻市の平成29年と同じく、会議 1 件だけの臨時会が
# 定例会に挟まる。
VOICETECHNO_SITE = {
    "2016(平成28年)": {"本会議": {"第01回(３月定例会)": ["平成28年 第１回嘉麻市議会定例会会議録（第１日：平成28年３月１日）"]}},
    "2017(平成29年)": {
        "本会議": {
            "第01回(３月定例会)": H29_MARCH,
            "第01回(５月臨時会)": H29_MAY,
            "第02回(６月定例会)": H29_JUNE,
        }
    },
}


class _FakeVoicetechnoPage:
    """voicetechno の画面のまね。項目を押すと UpdatePanel の postback が始まり、
    描き直しは wait_for_function で待つか、決め打ちの待ち時間が取得元の応答
    （latency_ms）を超えたときに終わる。選んである項目を押しても postback は
    起きない（DevExpress は選択が変わらないと SelectedIndexChanged を出さない）。"""

    ORDER = ("year", "kind", "kaisu", "name")

    def __init__(self, *, latency_ms: int = 2_000, failing_waits: int = 0) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        self.timeout_error = PlaywrightTimeoutError
        self.adapter = html_list_sites.Voicetechno
        self.key_by_cid = {cid: key for key, cid in self.adapter.IDS.items()}
        self.latency_ms = latency_ms
        self.failing_waits = failing_waits
        self.clicks: list[tuple[str, int]] = []
        self.gotos = 0
        self._reset()

    def _reset(self) -> None:
        # 開いた直後は最新の年が選ばれ、種別は「選択してください」。
        self.selected = {"year": len(VOICETECHNO_SITE) - 1, "kind": -1, "kaisu": -1, "name": -1}
        self.postbacks = None
        self.pending = None
        self.dropped = ""

    def _branch(self, key: str):
        node = VOICETECHNO_SITE
        for upper in self.ORDER[: self.ORDER.index(key)]:
            index = self.selected[upper]
            if index < 0:
                return {}
            node = node[list(node)[index]]
        return node

    def items(self, key: str) -> list[str]:
        return list(self._branch(key))

    def _apply_pending(self) -> None:
        if self.pending is None:
            return
        key, index = self.pending[:2]
        self.selected[key] = index
        for lower in self.ORDER[self.ORDER.index(key) + 1 :]:
            self.selected[lower] = -1
        self.postbacks += 1
        self.pending = None

    # ---- Playwright の page のうち、アダプタが使う分 ----
    def goto(self, url, **_kwargs):
        self.gotos += 1
        self._reset()

    def set_default_timeout(self, _timeout_ms):
        pass

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def evaluate(self, script, arg=None):
        adapter = self.adapter
        if script == adapter.HOOK_JS:
            self.postbacks = 0 if self.postbacks is None else self.postbacks
            return None
        if script == adapter.POSTBACKS_JS:
            return self.postbacks
        key = self.key_by_cid[arg]
        if script == adapter.ITEMS_JS:
            return self.items(key)
        if script == adapter.SELECTED_JS:
            return self.selected[key]
        if script == adapter.OPEN_JS:
            self.dropped = key
            return None
        raise AssertionError(f"知らない script: {script}")

    def query_selector(self, selector):
        import re

        match = re.fullmatch(r"#(\w+?)_DDD_L_LBI(\d+)T0", selector)
        key = self.key_by_cid[match.group(1)]
        return object() if int(match.group(2)) < len(self.items(key)) else None

    def click(self, selector, **_kwargs):
        import re

        match = re.fullmatch(r"#(\w+?)_DDD_L_(?:LBI(\d+)T0|LBT tr:nth-child\((\d+)\) td)", selector)
        key = self.key_by_cid[match.group(1)]
        index = int(match.group(2)) if match.group(2) is not None else int(match.group(3)) - 1
        if self.dropped != key or index >= len(self.items(key)):
            raise self.timeout_error(f'Page.click: Timeout exceeded. waiting for locator("{selector}")')
        self.dropped = ""
        self.clicks.append((key, index))
        if index != self.selected[key]:
            self.pending = [key, index, 0]

    def wait_for_timeout(self, ms):
        if self.pending is not None:
            self.pending[2] += ms
            if self.pending[2] >= self.latency_ms:
                self._apply_pending()

    def wait_for_function(self, script, arg=None, timeout=None):
        assert script == self.adapter.DONE_JS
        if self.failing_waits > 0:
            self.failing_waits -= 1
            raise self.timeout_error(f"Timeout {timeout}ms exceeded.")
        self._apply_pending()
        before, cid, index = arg
        if not (self.postbacks > before and self.selected[self.key_by_cid[cid]] == index):
            raise self.timeout_error(f"Timeout {timeout}ms exceeded.")

    def inner_text(self, selector):
        assert selector == self.adapter.PANEL
        if self.selected["name"] < 0:
            return ""
        title = self.items("name")[self.selected["name"]]
        return f"閲覧する\n{title}\n\n○議長　ただいまから本日の会議を開きます。\n" + "発言。" * 100 + f"\n{self.adapter.TRAILER}"


class VoicetechnoTest(unittest.TestCase):
    """コンボの描き直しを待ってから次を読む（嘉麻市 平成29年）。"""

    def setUp(self) -> None:
        from unittest import mock

        patcher = mock.patch.object(html_list_sites.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def adapter_on(self, page: _FakeVoicetechnoPage) -> html_list_sites.Voicetechno:
        adapter = html_list_sites.Voicetechno()
        adapter._page = page
        adapter._needs_reload = True
        return adapter

    def item(self, title: str, fragment: str) -> html_list_sites.MeetingItem:
        return html_list_sites.MeetingItem(title=title, url=VOICETECHNO_URL + fragment, year_label="平成29年", meeting_group="本会議")

    def test_names_belong_to_the_session_picked_even_when_the_redraw_is_slow(self) -> None:
        # 応答が 2 秒かかっても、臨時会の位置に定例会の会議名を控えない。
        page = _FakeVoicetechnoPage(latency_ms=2_000)
        walk: dict = {}

        items = self.adapter_on(page).discover(None, VOICETECHNO_URL, 10_000, walk)

        self.assertEqual(
            [(item.url.split("#")[1], item.title) for item in items if item.year_label == "平成29年"],
            [(f"y=1&k=0&s=0&n={n}", title) for n, title in enumerate(H29_MARCH)]
            + [("y=1&k=0&s=1&n=0", H29_MAY[0])]
            + [(f"y=1&k=0&s=2&n={n}", title) for n, title in enumerate(H29_JUNE)],
        )
        self.assertEqual(walk["missed_pages"], 0)

    def test_meeting_body_follows_the_title(self) -> None:
        page = _FakeVoicetechnoPage()

        text = self.adapter_on(page).fetch_text(None, self.item(H29_MARCH[1], "#y=1&k=0&s=0&n=1"), 10_000)

        self.assertTrue(text.startswith(H29_MARCH[1]))
        self.assertNotIn(html_list_sites.Voicetechno.TRAILER, text)

    def test_already_selected_year_is_not_clicked(self) -> None:
        # 開いた直後に選ばれている年を押しても postback は来ない。待たずに次へ進む。
        page = _FakeVoicetechnoPage()

        self.adapter_on(page).fetch_text(None, self.item(H29_MAY[0], "#y=1&k=0&s=1&n=0"), 10_000)

        self.assertEqual(page.clicks, [("kind", 0), ("kaisu", 1), ("name", 0)])

    def test_moved_position_is_found_again_by_title(self) -> None:
        page = _FakeVoicetechnoPage()

        text = self.adapter_on(page).fetch_text(None, self.item(H29_MARCH[2], "#y=1&k=0&s=0&n=0"), 10_000)

        self.assertTrue(text.startswith(H29_MARCH[2]))

    def test_title_missing_from_the_session_stops_instead_of_saving_another_meeting(self) -> None:
        # 前の一覧がずれて控えた「５月臨時会の 2 件目 = ３月定例会 第２日」。
        # 臨時会の 1 件目を押してその本文を保存してはいけない。
        page = _FakeVoicetechnoPage()

        with self.assertRaisesRegex(RuntimeError, "会議名のコンボに"):
            self.adapter_on(page).fetch_text(None, self.item(H29_MARCH[1], "#y=1&k=0&s=1&n=1"), 10_000)
        self.assertNotIn("name", [key for key, _index in page.clicks])
        self.sleep.assert_not_called()

    def test_timeout_reopens_the_page_and_retries(self) -> None:
        page = _FakeVoicetechnoPage(failing_waits=1)

        text = self.adapter_on(page).fetch_text(None, self.item(H29_JUNE[1], "#y=1&k=0&s=2&n=1"), 10_000)

        self.assertTrue(text.startswith(H29_JUNE[1]))
        self.assertEqual(page.gotos, 2)
        self.sleep.assert_called_once_with(html_list_sites.FETCH_RETRY_WAITS_SECONDS[0])

    def test_year_that_keeps_timing_out_is_counted_as_missed(self) -> None:
        page = _FakeVoicetechnoPage(failing_waits=99)
        walk: dict = {}

        items = self.adapter_on(page).discover(None, VOICETECHNO_URL, 10_000, walk)

        # 平成28年は押すたびに時間切れ。平成29年は開いた直後から選ばれていて押さずに済むが、
        # 種別で時間切れになる。どちらも数えて残す。
        self.assertEqual(items, [])
        self.assertEqual(walk["missed_pages"], 2)


if __name__ == "__main__":
    unittest.main()
