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


if __name__ == "__main__":
    unittest.main()
