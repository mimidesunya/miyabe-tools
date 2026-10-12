from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT))
sys.path.append(str(WORKSPACE_ROOT / "tools"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "gijiroku"))

from tools.tasks import ocr_backfill  # noqa: E402


class PendingCountTest(unittest.TestCase):
    """OCR 待ちの数え方。

    通常の巡回は OCR 無しで走るので、OCR で本文にできた PDF も毎回
    「文字情報が無い」と数え直される。読めた分を引かないと件数が減らず、
    同じ自治体を 2 時間おきに取り直し続ける（三宅町・士幌町で実際に起きた）。
    """

    def _work_dir(self, attempts: dict, empty: int) -> Path:
        directory = Path(self.directory.name) / "01632-shihoro-cho"
        (directory / "").mkdir(parents=True, exist_ok=True)
        (directory / "ocr_attempts.json").write_text(
            json.dumps(attempts, ensure_ascii=False), encoding="utf-8"
        )
        (directory / "scrape_state.json").write_text(
            json.dumps({"validation": {"status_counts": {"empty_pdf_text": empty}}}),
            encoding="utf-8",
        )
        return directory

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_successful_ocr_is_not_pending_any_more(self) -> None:
        work_dir = self._work_dir({f"{i}.pdf": {"status": "ok", "attempts": 1} for i in range(3)}, 3)
        self.assertEqual(ocr_backfill.settled_count(work_dir), 3)

    def test_exhausted_attempts_are_settled_too(self) -> None:
        attempts = {
            "a.pdf": {"status": "failed", "attempts": ocr_backfill.pdf_ocr.MAX_ATTEMPTS},
            "b.pdf": {"status": "failed", "attempts": 1},
        }
        work_dir = self._work_dir(attempts, 2)
        # 試し尽くした 1 件だけを片付いた扱いにする。もう 1 件はまだ試せる。
        self.assertEqual(ocr_backfill.settled_count(work_dir), 1)

    def test_a_broken_record_does_not_count(self) -> None:
        work_dir = self._work_dir({"a.pdf": "壊れている"}, 1)
        self.assertEqual(ocr_backfill.settled_count(work_dir), 0)


class NotTargetTest(unittest.TestCase):
    """取得対象でない自治体が先頭に居座ると、掃き取りが毎回そこで終わる（猿払村・留寿都村）。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for slug, empty in (("01511-sarufutsu-mura", 73), ("01397-rusutsu-mura", 35), ("10443-katashina-mura", 30)):
            (root / slug).mkdir()
            (root / slug / "scrape_state.json").write_text(
                json.dumps({"validation": {"status_counts": {"empty_pdf_text": empty}}}), encoding="utf-8"
            )
        patches = [
            mock.patch.object(ocr_backfill, "WORK_ROOT", root),
            mock.patch.object(ocr_backfill, "scrapeable_slugs", return_value={"10443-katashina-mura"}),
            mock.patch.object(ocr_backfill.pdf_ocr, "tool_directory", return_value=root),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_only_scrapeable_municipalities_are_pending(self) -> None:
        self.assertEqual(ocr_backfill.pending_targets(), [("10443-katashina-mura", 30)])

    def test_a_target_that_fails_to_load_does_not_use_up_the_limit(self) -> None:
        # 台帳が掃き取りの途中で変わった形。読めない自治体は飛ばして次を走らせる。
        def run_one(slug: str) -> dict:
            if slug == "01511-sarufutsu-mura":
                raise ValueError(f"Municipality slug not found: {slug}")
            return {"slug": slug, "returncode": 0}

        with mock.patch.object(ocr_backfill, "scrapeable_slugs", return_value={"01511-sarufutsu-mura", "10443-katashina-mura"}), \
                mock.patch.object(ocr_backfill, "run_one", side_effect=run_one) as runner:
            result = ocr_backfill.run(limit=1)
        self.assertEqual([call.args[0] for call in runner.call_args_list], ["01511-sarufutsu-mura", "10443-katashina-mura"])
        self.assertEqual(result["targets"][-1]["returncode"], 0)

    def test_a_failed_run_still_uses_up_the_limit(self) -> None:
        # 走ってから落ちた（時間切れなど）分は数える。続けると 1 回の掃き取りが何時間も延びる。
        with mock.patch.object(ocr_backfill, "scrapeable_slugs", return_value={"01511-sarufutsu-mura", "10443-katashina-mura"}), \
                mock.patch.object(ocr_backfill, "run_one", side_effect=RuntimeError("timeout")) as runner:
            ocr_backfill.run(limit=1)
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
