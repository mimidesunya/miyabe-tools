"""同じ自治体を積み直さない控え。

掃き取りは大きい自治体から積む。北海道の会議録 10,066 件は 1 回の掃き取りの
間隔では終わらないので、控えが無いと毎時同じものが積まれ、キューだけが伸びる。
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.tasks import generation_sweep_state as state  # noqa: E402
from tools.tasks import status as batch_status  # noqa: E402


class GenerationSweepStateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.original = batch_status.status_root
        batch_status.status_root = lambda: Path(self.temporary.name)
        self.addCleanup(lambda: setattr(batch_status, "status_root", self.original))

    def test_nothing_queued_yet_passes_everything(self):
        self.assertEqual(
            state.filter_recently_queued("minutes", ["a", "b"], now=1000.0),
            ["a", "b"],
        )

    def test_recently_queued_is_held_back(self):
        state.mark_queued("minutes", ["01000-hokkaido"], now=1000.0)
        self.assertEqual(
            state.filter_recently_queued(
                "minutes", ["01000-hokkaido", "b"], cooldown_seconds=3600, now=2000.0
            ),
            ["b"],
        )

    def test_the_cooldown_ends(self):
        state.mark_queued("minutes", ["01000-hokkaido"], now=1000.0)
        self.assertEqual(
            state.filter_recently_queued(
                "minutes", ["01000-hokkaido"], cooldown_seconds=3600, now=1000.0 + 3600
            ),
            ["01000-hokkaido"],
        )

    def test_old_records_are_dropped(self):
        """控えを際限なく太らせない。"""
        state.mark_queued("minutes", ["old"], now=0.0)
        state.mark_queued("minutes", ["new"], now=state.DEFAULT_COOLDOWN_SECONDS * 5)
        written = state.state_path("minutes").read_text(encoding="utf-8")
        self.assertIn("new", written)
        self.assertNotIn("old", written)

    def test_a_broken_file_does_not_stop_the_sweep(self):
        state.state_path("minutes").write_text("{ broken", encoding="utf-8")
        self.assertEqual(state.filter_recently_queued("minutes", ["a"]), ["a"])
