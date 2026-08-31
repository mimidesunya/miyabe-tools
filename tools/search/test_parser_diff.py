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
