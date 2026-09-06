#!/usr/bin/env python3
"""失効した取得元 URL の引き直しを確かめる。

北海道町村会は版番号を URL に持つので、新版が出ると旧 URL ごと 404 になる。
引き直しの手順と、実行時の上書きが TSV の修正で自然に外れることを見る。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

import reiki_targets
import source_url_recovery


ENTRY_HTML = """
<ul>
  <li><a href="/~reikidb/?choson_no=54">
      <span><div class="rubi">かみのくにちょう</div><div>上ノ国町</div></span></a></li>
  <li><a href="/~reikidb/?choson_no=122">
      <span><div class="rubi">てしかがちょう</div><div>弟子屈町</div></span></a></li>
  <li><a href="https://www.city.wakkanai.hokkaido.jp/data/reiki/reiki.html">
      <span><div class="rubi">わっかないし</div><div>稚内市</div></span></a></li>
</ul>
"""

TOWN_PAGE_HTML = """
<div id="choson_name">上ノ国町</div>
<a href="/~reikidb/data/54/54/reiki.html">例規集</a>
"""

# 入口へ戻されたときに拾ってしまう他自治体のリンク。
OTHER_TOWN_PAGE_HTML = """
<a href="/~reikidb/data/122/49/reiki.html">例規集</a>
"""


class _Response:
    def __init__(self, text: str, url: str, status_code: int = 200) -> None:
        self.text = text
        self.url = url
        self.status_code = status_code
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    """要求された URL に応じて固定の応答を返す最小の session。"""

    def __init__(self, pages: dict[str, _Response]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, **_kwargs) -> _Response:
        self.requested.append(url)
        if url in self.pages:
            return self.pages[url]
        return _Response("", url, status_code=404)


class ChosonNumberTest(unittest.TestCase):
    def test_reads_kanji_names(self) -> None:
        numbers = source_url_recovery.h_chosonkai_choson_numbers(ENTRY_HTML)
        self.assertEqual(numbers.get("上ノ国町"), 54)
        self.assertEqual(numbers.get("弟子屈町"), 122)

    def test_ignores_towns_hosted_elsewhere(self) -> None:
        # 自前サイトへ出ている自治体は choson_no を持たない。
        numbers = source_url_recovery.h_chosonkai_choson_numbers(ENTRY_HTML)
        self.assertNotIn("稚内市", numbers)


class ResolveTest(unittest.TestCase):
    def test_resolves_current_edition(self) -> None:
        session = _Session(
            {
                source_url_recovery.H_CHOSONKAI_ENTRY: _Response(
                    ENTRY_HTML, source_url_recovery.H_CHOSONKAI_ENTRY
                ),
                source_url_recovery.H_CHOSONKAI_ENTRY
                + "?choson_no=54": _Response(
                    TOWN_PAGE_HTML,
                    source_url_recovery.H_CHOSONKAI_ENTRY + "?choson_no=54",
                ),
            }
        )
        resolved = source_url_recovery.resolve_h_chosonkai_source_url("上ノ国町", session=session)
        self.assertEqual(
            resolved,
            "https://houmu.h-chosonkai.gr.jp/~reikidb/data/54/54/reiki.html",
        )

    def test_refuses_another_towns_link(self) -> None:
        # セッションが切れて入口へ戻されると別の自治体のリンクが載る。
        # 自治体番号が合わないリンクは採用しない。
        session = _Session(
            {
                source_url_recovery.H_CHOSONKAI_ENTRY: _Response(
                    ENTRY_HTML, source_url_recovery.H_CHOSONKAI_ENTRY
                ),
                source_url_recovery.H_CHOSONKAI_ENTRY
                + "?choson_no=54": _Response(
                    OTHER_TOWN_PAGE_HTML,
                    source_url_recovery.H_CHOSONKAI_ENTRY + "?choson_no=54",
                ),
            }
        )
        self.assertEqual(
            source_url_recovery.resolve_h_chosonkai_source_url("上ノ国町", session=session),
            "",
        )

    def test_unknown_host_has_no_procedure(self) -> None:
        self.assertFalse(
            source_url_recovery.is_recoverable_source_url(
                "https://en3-jg.d1-law.com/kakegawa/d1w_reiki/reiki.html"
            )
        )
        self.assertTrue(
            source_url_recovery.is_recoverable_source_url(
                "http://houmu.h-chosonkai.gr.jp/~reikidb/data/54/52/reiki.html"
            )
        )


class DeadDetectionTest(unittest.TestCase):
    def test_only_404_counts_as_gone(self) -> None:
        session = _Session({"https://example.invalid/alive": _Response("", "x", 200)})
        self.assertTrue(source_url_recovery.source_url_is_dead("https://example.invalid/gone", session))
        self.assertFalse(source_url_recovery.source_url_is_dead("https://example.invalid/alive", session))

    def test_connection_failure_is_not_gone(self) -> None:
        class _Broken:
            def get(self, *_args, **_kwargs):
                raise RuntimeError("connection reset")

        # 一時的な不通で URL を引き直すと、生きている登録簿を壊す。
        self.assertFalse(source_url_recovery.source_url_is_dead("https://example.invalid/x", _Broken()))


class OverrideTest(unittest.TestCase):
    def test_override_applies_and_expires(self) -> None:
        overrides = {
            "01362": {
                "url": "https://houmu.h-chosonkai.gr.jp/~reikidb/data/54/54/reiki.html",
                "replaces": "http://houmu.h-chosonkai.gr.jp/~reikidb/data/54/52/reiki.html",
            }
        }
        stale = "http://houmu.h-chosonkai.gr.jp/~reikidb/data/54/52/reiki.html"
        self.assertEqual(
            reiki_targets.apply_source_url_override("01362", stale, overrides),
            "https://houmu.h-chosonkai.gr.jp/~reikidb/data/54/54/reiki.html",
        )
        # TSV が人手で直ったら、古い上書きは使わない。
        fixed = "https://houmu.h-chosonkai.gr.jp/~reikidb/data/54/60/reiki.html"
        self.assertEqual(
            reiki_targets.apply_source_url_override("01362", fixed, overrides),
            fixed,
        )

    def test_missing_and_broken_override_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source_url_overrides.json"
            original = reiki_targets.source_url_overrides_path
            reiki_targets.source_url_overrides_path = lambda: path  # type: ignore[assignment]
            try:
                self.assertEqual(reiki_targets.load_source_url_overrides(), {})
                path.write_text("{ broken", encoding="utf-8")
                self.assertEqual(reiki_targets.load_source_url_overrides(), {})
                path.write_text(
                    json.dumps({"01362": {"url": "https://example.test/x", "replaces": ""}}),
                    encoding="utf-8",
                )
                self.assertEqual(
                    reiki_targets.load_source_url_overrides()["01362"]["url"],
                    "https://example.test/x",
                )
            finally:
                reiki_targets.source_url_overrides_path = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
