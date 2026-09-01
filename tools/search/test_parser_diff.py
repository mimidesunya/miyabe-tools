"""パーサ差分ハーネス。

合成した入力だけでは、打ち切りや除外の規則が本物を巻き込んでいないと言えない。
このラウンドで二度、実データの差分だけが改悪を捕まえた。ハーネス自体が壊れて
いれば気づけないので、部品に試験を置く。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parser_diff  # noqa: E402


class BaselinePathTest(unittest.TestCase):
    def test_accepts_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minutes_kind.py"
            path.write_text("x = 1\n", encoding="utf-8")
            self.assertEqual(
                parser_diff.baseline_path("tools/gijiroku/minutes_kind.py", path), path
            )

    def test_accepts_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minutes_kind.py"
            path.write_text("x = 1\n", encoding="utf-8")
            self.assertEqual(
                parser_diff.baseline_path("tools/gijiroku/minutes_kind.py", Path(tmp)), path
            )

    def test_a_missing_baseline_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as caught:
                parser_diff.baseline_path("tools/gijiroku/minutes_kind.py", Path(tmp))
            self.assertIn("minutes_kind.py", str(caught.exception))


class LoadModuleTest(unittest.TestCase):
    def test_registers_the_module_so_dataclasses_resolve(self):
        """dataclass は自分のモジュールを `sys.modules` から引く。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample_module.py"
            path.write_text(
                "from dataclasses import dataclass\n\n\n@dataclass\nclass Thing:\n    value: int\n",
                encoding="utf-8",
            )
            module = parser_diff.load_module("sample_module_probe", path)
            self.assertEqual(module.Thing(value=3).value, 3)
            self.assertIn("sample_module_probe", sys.modules)
            sys.modules.pop("sample_module_probe", None)

    def test_a_broken_module_does_not_stay_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken_module.py"
            path.write_text("raise ValueError('boom')\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parser_diff.load_module("broken_module_probe", path)
            self.assertNotIn("broken_module_probe", sys.modules)


class CommandLineTest(unittest.TestCase):
    def test_it_runs(self):
        """引数の組み立てが壊れていないことだけ見る。データは触らない。"""
        out = subprocess.run(
            [sys.executable, str(Path(parser_diff.__file__).resolve()), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--baseline", out.stdout or "")


class ReaderFieldsTest(unittest.TestCase):
    """比べるのは日付だけではない。

    codex と grok の両方が「文書の増減・題名・本文・原典 URL の回帰は
    止まらない」と指摘した。公開されるフィールドは全部比べる。
    """

    def _paths(self):
        from pathlib import Path as _Path

        here = _Path(parser_diff.__file__).resolve().parent
        for path in (str(here), str(here.parent / "gijiroku")):
            if path not in sys.path:
                sys.path.insert(0, path)
        return here

    def test_minutes_reader_returns_every_published_field(self):
        from pathlib import Path as _Path

        self._paths()
        import scraped_source_records as records
        import minutes_kind

        with tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp)
            target = root / "令和5年" / "会議録.txt"
            target.parent.mkdir(parents=True)
            target.write_text(
                "令和5年\n会議録\n出典: https://example.jp/x.pdf\n"
                "令和5年3月9日（木曜日） 午前10時00分開議\n○議長 開会します。\n",
                encoding="utf-8",
            )
            read = parser_diff.minutes_reader(minutes_kind, records)
            got = read(target, root)
        self.assertEqual(
            sorted(got), ["body_length", "dropped", "held_on", "source_url", "title"]
        )
        self.assertEqual(got["source_url"], "https://example.jp/x.pdf")
        self.assertEqual(got["held_on"], "2023-03-09")

    def test_reiki_reader_returns_every_published_field(self):
        from pathlib import Path as _Path

        self._paths()
        import scraped_source_records as records

        with tempfile.TemporaryDirectory() as tmp:
            target = _Path(tmp) / "x.html"
            target.write_text(
                '<div class="law-title">○○条例</div>'
                '<div class="law-number">平成24年条例第1号</div>'
                '<div class="law-date">平成24年3月31日 (2012-03-31)</div>',
                encoding="utf-8",
            )
            read = parser_diff.reiki_reader(records, records)
            got = read(target, _Path(tmp))
        self.assertEqual(sorted(got), ["body_length", "number", "promulgated_on", "title"])
        self.assertEqual(got["promulgated_on"], "2012-03-31")
