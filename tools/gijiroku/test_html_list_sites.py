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


if __name__ == "__main__":
    unittest.main()
