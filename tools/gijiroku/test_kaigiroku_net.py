import json
import unittest

from tools.gijiroku.scrapers import kaigiroku_net


class FakePage:
    def __init__(self) -> None:
        self.current_url = ""
        self.visited: list[str] = []

    def goto(self, url: str, **_kwargs) -> None:
        self.current_url = url
        self.visited.append(url)

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def evaluate(self, _script: str) -> str:
        tenant_id = 631 if self.current_url.endswith("/SpTop.html") else None
        return json.dumps({"tenant_id": tenant_id} if tenant_id is not None else {})


class KaigirokuNetUrlTest(unittest.TestCase):
    def test_tenant_base_url_strips_pc_pg_directory(self) -> None:
        source_url = "https://ssp.kaigiroku.net/tenant/example/pg/index.html"
        self.assertEqual(
            kaigiroku_net.tenant_base_url(source_url),
            "https://ssp.kaigiroku.net/tenant/example/",
        )

    def test_load_tenant_id_falls_back_to_mobile_top(self) -> None:
        source_url = "https://ssp.kaigiroku.net/tenant/example/pg/index.html"
        page = FakePage()

        tenant_id = kaigiroku_net.load_tenant_id(page, source_url, 1_000)

        self.assertEqual(tenant_id, 631)
        self.assertEqual(
            page.visited,
            [source_url, "https://ssp.kaigiroku.net/tenant/example/SpTop.html"],
        )

    def test_schedule_url_uses_tenant_root_for_pc_source(self) -> None:
        source_url = "https://ssp.kaigiroku.net/tenant/example/pg/index.html"
        self.assertEqual(
            kaigiroku_net.build_schedule_url(source_url, 1, 2, 3),
            "https://ssp.kaigiroku.net/tenant/example/MinuteView.html?tenant_id=1&council_id=2&schedule_id=3",
        )


class FakeRequestPage:
    request = object()


def meeting_item(title: str, schedule_id: int) -> kaigiroku_net.MeetingItem:
    return kaigiroku_net.MeetingItem(
        title=title,
        url="https://ssp.kaigiroku.net/tenant/example/MinuteView.html",
        year_label="令和2年",
        tenant_id=461,
        council_id=172,
        schedule_id=schedule_id,
    )


class KaigirokuNetMinutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.original_api_post = kaigiroku_net.api_post

    def tearDown(self) -> None:
        kaigiroku_net.api_post = self.original_api_post

    def fake_api_post(self, fail_index: bool):
        def api_post(_request, _api_root, path, _payload, timeout_ms, referer):
            self.calls.append((path, timeout_ms))
            if path == "minutes/get_index":
                if fail_index:
                    raise RuntimeError("minutes/get_index の取得に失敗しました: minutes/get_index returned HTTP 500")
                return {"councilIndex": {"council_index": "<pre>索引の目次</pre>"}}
            if path == "minutes/get_minute":
                return {"tenant_minutes": [{"title": None, "page_no": 0, "body": "<pre>本文 API の目次</pre>"}]}
            raise AssertionError(path)

        return api_post

    def test_table_of_contents_falls_back_to_minute_api_when_index_api_fails(self) -> None:
        # 四街道市などで get_index が特定の会議だけ HTTP 500 を返す。
        # 同じ目次は get_minute でも取れるので、そこで止めない。
        kaigiroku_net.api_post = self.fake_api_post(fail_index=True)

        count, text = kaigiroku_net.fetch_schedule_minutes(
            FakeRequestPage(), "https://ssp.kaigiroku.net/dnp/search/", meeting_item("03月10日－目次", 1), 10_000
        )

        self.assertEqual(count, 1)
        self.assertIn("本文 API の目次", text)
        self.assertEqual([path for path, _ in self.calls], ["minutes/get_index", "minutes/get_minute"])

    def test_table_of_contents_uses_index_api_when_it_works(self) -> None:
        kaigiroku_net.api_post = self.fake_api_post(fail_index=False)

        count, text = kaigiroku_net.fetch_schedule_minutes(
            FakeRequestPage(), "https://ssp.kaigiroku.net/dnp/search/", meeting_item("03月10日－目次", 1), 10_000
        )

        self.assertEqual((count, text), (1, "索引の目次"))
        self.assertEqual([path for path, _ in self.calls], ["minutes/get_index"])

    def test_minute_api_waits_longer_than_default_timeout(self) -> None:
        # 柏市の長い会議は本文 API の応答が 13MB・16 秒かかる。10 秒で切らない。
        kaigiroku_net.api_post = self.fake_api_post(fail_index=False)

        kaigiroku_net.fetch_schedule_minutes(
            FakeRequestPage(), "https://ssp.kaigiroku.net/dnp/search/", meeting_item("12月15日－08号", 9), 10_000
        )

        self.assertEqual(self.calls, [("minutes/get_minute", kaigiroku_net.MINUTE_API_MIN_TIMEOUT_MS)])
        self.assertGreaterEqual(kaigiroku_net.MINUTE_API_MIN_TIMEOUT_MS, 30_000)


if __name__ == "__main__":
    unittest.main()
