"""更新確認のとき、目録の枝まで取り直す。

以前は手元に控えがあれば枝を二度と取りに行かなかった。石狩市は 4 月の
控えを歩き続け、取得元が消した 20 件を毎回 404 で取りに行き、7 月に
増えた 24 件は一度も見つけていなかった（島原市・江別市・京都市・
留寿都村・古平町も同じ形）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT / "tools" / "reiki"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "reiki" / "scrapers"))

import reiki_targets  # noqa: E402
from scrapers import d1_law  # noqa: E402

BASE = "https://en3-jg.example.test/city/d1w_reiki/"
ENTRY_HTML = '<a href="mokuji_bunya_index.html">目次検索へ</a>'


class FakeResponse:
    def __init__(self, url: str, status_code: int, body: str) -> None:
        self.url = url
        self.status_code = status_code
        self.content = body.encode("utf-8")
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSite:
    """取得元の今の姿。載っていない URL は 404 を返す。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url, headers=None, timeout=None):  # noqa: ANN001
        self.requested.append(url)
        name = url[len(BASE):]
        if name in self.pages:
            return FakeResponse(url, 200, self.pages[name])
        return FakeResponse(url, 404, "not found")


class CatalogRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data_dir = self.root / "source"
        self.data_dir.mkdir()
        # 休止の記録を本物の作業ディレクトリへ書かない。
        original_root = reiki_targets.WORK_ROOT
        reiki_targets.WORK_ROOT = self.root
        self.addCleanup(lambda: setattr(reiki_targets, "WORK_ROOT", original_root))
        for bucket in (d1_law.DOWNLOAD_FAILURES, d1_law.DOWNLOAD_MISSING, d1_law.SKIPPED_BY_HOST_BLOCK):
            bucket.clear()
            self.addCleanup(bucket.clear)
        # 書き換えのたびの待ちはテストでは要らない。
        patcher = mock.patch.object(d1_law, "DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 4 月に取った控え。OLD1 は取得元から消えている。
        self.store("mokuji_bunya_index.html", '<a href="bunya_0010000.html">第1類</a>')
        self.store("bunya_0010000.html", "<a href=\"javascript:OpenResDataWin('OLD1')\">旧</a>")

    def store(self, name: str, body: str) -> None:
        (self.data_dir / name).write_text(body, encoding="utf-8")

    def walk(self, site: FakeSite, *, check_updates: bool) -> tuple[list[str], dict]:
        walk: dict = {}
        with mock.patch.object(d1_law.requests, "get", side_effect=site.get):
            found = d1_law.get_hno_list(
                BASE,
                self.data_dir,
                check_updates=check_updates,
                walk=walk,
                entry_html=ENTRY_HTML,
            )
        return found, walk

    def test_update_check_reads_the_current_branch(self) -> None:
        site = FakeSite(
            {
                "mokuji_bunya_index.html": '<a href="bunya_0010000.html">第1類</a>',
                # 7 月の版。載せ方も JavaScript から普通のリンクに変わった。
                "bunya_0010000.html": '<a href="NEW1/NEW1_j.html">新</a>',
            }
        )
        found, walk = self.walk(site, check_updates=True)
        self.assertEqual(found, ["NEW1"])
        self.assertIn(BASE + "bunya_0010000.html", site.requested)
        self.assertEqual(walk["missed_pages"], 0)

    def test_without_update_check_the_saved_copy_is_used(self) -> None:
        # 更新確認をしない実行は、これまでどおり手元の控えで済ませる。
        site = FakeSite({"bunya_0010000.html": '<a href="NEW1/NEW1_j.html">新</a>'})
        found, _walk = self.walk(site, check_updates=False)
        self.assertEqual(found, ["OLD1"])
        self.assertNotIn(BASE + "bunya_0010000.html", site.requested)

    def test_a_branch_the_source_removed_is_not_read_from_the_old_copy(self) -> None:
        # 目次はまだ枝を指しているのに、枝は 404。古い控えの OLD1 を数え続けない。
        # 開けなかった枝として数える（控えが無いときと同じ扱い）。
        site = FakeSite(
            {
                "mokuji_bunya_index.html": (
                    '<a href="bunya_0010000.html">第1類</a><a href="bunya_0020000.html">第2類</a>'
                ),
                "bunya_0020000.html": '<a href="NEW2/NEW2_j.html">新</a>',
            }
        )
        found, walk = self.walk(site, check_updates=True)
        self.assertEqual(found, ["NEW2"])
        self.assertEqual(walk["missed_pages"], 1)
        self.assertEqual(walk["missed_examples"], ["bunya_0010000.html"])

    def test_a_guessed_menu_that_is_gone_is_neither_read_nor_counted(self) -> None:
        # 決め打ちの目次の古い控えが残っていても、取得元に無いなら読まない。
        self.store("mokuji_index_index.html", "<a href=\"javascript:OpenResDataWin('OLD9')\">旧</a>")
        site = FakeSite(
            {
                "mokuji_bunya_index.html": '<a href="bunya_0010000.html">第1類</a>',
                "bunya_0010000.html": '<a href="NEW1/NEW1_j.html">新</a>',
            }
        )
        found, walk = self.walk(site, check_updates=True)
        self.assertEqual(found, ["NEW1"])
        self.assertEqual(walk["missed_pages"], 0)
        # 無くてもよいページなので、取得元から消えた本文の数にも入れない。
        self.assertEqual(d1_law.DOWNLOAD_MISSING, [])

    def test_a_transient_failure_falls_back_to_the_saved_copy(self) -> None:
        # 通信の失敗は取得元が消したのではない。控えで歩き続け、失敗は数える。
        site = FakeSite({"mokuji_bunya_index.html": '<a href="bunya_0010000.html">第1類</a>'})
        original_get = site.get

        def flaky(url, headers=None, timeout=None):  # noqa: ANN001
            if url.endswith("bunya_0010000.html"):
                site.requested.append(url)
                return FakeResponse(url, 503, "busy")
            return original_get(url, headers=headers, timeout=timeout)

        site.get = flaky  # type: ignore[method-assign]
        found, walk = self.walk(site, check_updates=True)
        self.assertEqual(found, ["OLD1"])
        self.assertEqual(walk["missed_pages"], 0)
        self.assertEqual(d1_law.DOWNLOAD_FAILURES, [BASE + "bunya_0010000.html"])


if __name__ == "__main__":
    unittest.main()
