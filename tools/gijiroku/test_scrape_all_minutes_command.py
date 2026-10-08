"""一括実行が子スクレイパに渡す引数を、受け取れる系統にだけ渡す。

HTTP だけで取る系統（独自・kensakusystem など）は --headful を持たない。
渡すと argparse が止めて、その自治体の取得が毎回失敗する。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gijiroku import scrape_all_minutes  # noqa: E402


def command(system_type: str, *argv: str) -> list[str]:
    args = scrape_all_minutes.build_parser().parse_args(list(argv))
    target = {"system_type": system_type, "slug": "99999-test-shi"}
    return scrape_all_minutes.build_child_command(args, target)


class ChildCommandTest(unittest.TestCase):
    def test_headful_goes_only_to_browser_scrapers(self) -> None:
        for system_type in ("dbsr", "gijiroku.com", "kaigiroku.net"):
            self.assertIn("--headful", command(system_type, "--headful"), system_type)
        for system_type in ("独自", "kensakusystem", "static-kaigiroku-dir", "amivoice"):
            self.assertNotIn("--headful", command(system_type, "--headful"), system_type)

    def test_save_html_goes_only_to_scrapers_that_save_pages(self) -> None:
        self.assertIn("--save-html", command("kensakusystem", "--save-html"))
        self.assertNotIn("--save-html", command("独自", "--save-html"))

    def test_child_scrapers_accept_what_they_are_given(self) -> None:
        # 子スクレイパの引数の定義で、渡した引数がすべて通ること。
        import importlib.util

        for system_type in ("dbsr", "kensakusystem", "独自", "static-kaigiroku-dir", "site-gikai-pdf"):
            cmd = command(system_type, "--headful", "--save-html", "--no-resume")
            script = Path(cmd[cmd.index("--slug") - 1])
            if not script.is_absolute():
                script = Path(__file__).resolve().parents[2] / script
            spec = importlib.util.spec_from_file_location(f"child_{script.stem}", script)
            module = importlib.util.module_from_spec(spec)
            # dataclass は定義したモジュールを sys.modules から引く。
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            # site_gikai_pdf のように別のスクレイパの main を借りるものは、そちらの引数で見る。
            owner = module if hasattr(module, "build_parser") else sys.modules[module.main.__module__]
            owner.build_parser().parse_args(cmd[cmd.index("--slug"):])


if __name__ == "__main__":
    unittest.main()
