"""周回の判定と取り直しの起動で、本番の作業者の違いに左右されない。

判定は maintenance キューで動き、会議録の作業領域を mount していない例規の
索引 worker も受ける。本文が空の自治体の取り直しは、件数が揃っていて優先度が
0 点でも、絞った自治体を実際に取る。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # celery はスクレイパのコンテナにしか入っていない。
    from deploy.scraper_runtime.celery import tasks as celery_tasks
except ModuleNotFoundError:  # pragma: no cover - 手元では読み飛ばす
    celery_tasks = None


@unittest.skipIf(celery_tasks is None, "celery が無い環境では確かめない")
class ForcedRefetchCommandTest(unittest.TestCase):
    def test_forced_run_skips_the_priority_wait(self) -> None:
        command = celery_tasks._gijiroku_scrape_command(name_filter="28585-mikata-kami-cho", force=True)
        self.assertIn("--no-resume", command)
        self.assertIn("--ignore-wait", command)
        self.assertEqual("28585-mikata-kami-cho", command[command.index("--filter") + 1])

    def test_regular_run_keeps_the_priority_wait(self) -> None:
        command = celery_tasks._gijiroku_scrape_command(name_filter="28585-mikata-kami-cho")
        self.assertNotIn("--no-resume", command)
        self.assertNotIn("--ignore-wait", command)

    def test_forced_reiki_run_refetches_and_skips_the_priority_wait(self) -> None:
        command = celery_tasks._reiki_scrape_command(name_filter="11000-saitama-ken", force=True)
        self.assertIn("--force", command)
        self.assertIn("--ignore-wait", command)
        self.assertEqual("11000-saitama-ken", command[command.index("--filter") + 1])

    def test_regular_reiki_run_keeps_the_priority_wait(self) -> None:
        command = celery_tasks._reiki_scrape_command(name_filter="11000-saitama-ken")
        self.assertNotIn("--force", command)
        self.assertNotIn("--ignore-wait", command)


@unittest.skipIf(celery_tasks is None, "celery が無い環境では確かめない")
class EmptyBodySweepTest(unittest.TestCase):
    MINUTES = "miyabe-minutes-current"
    REIKI = "miyabe-reiki-current"

    def _sweep(self) -> list[tuple[str, dict]]:
        totals = {
            self.MINUTES: {"g-peer-1": 40, "g-peer-2": 40, "g-peer-3": 40, "g-short": 40},
            self.REIKI: {"r-peer-1": 40, "r-peer-2": 40, "r-peer-3": 40, "r-short": 40, "r-empty": 40},
        }
        empty = {self.MINUTES: {}, self.REIKI: {"r-empty": 30}}
        medians = {
            self.MINUTES: {"g-peer-1": 40000, "g-peer-2": 40000, "g-peer-3": 40000, "g-short": 400},
            self.REIKI: {"r-peer-1": 1700, "r-peer-2": 1700, "r-peer-3": 1700, "r-short": 400, "r-empty": 0},
        }
        targets = [{"slug": slug, "system_type": "dbsr"} for slug in totals[self.MINUTES]]
        reiki = [{"slug": slug, "system_type": "taikei"} for slug in totals[self.REIKI]]
        send_task = mock.MagicMock()
        fake_client_module = mock.MagicMock()
        with mock.patch.dict(
            os.environ, {"MIYABE_MINUTES_ALIAS": self.MINUTES, "MIYABE_REIKI_ALIAS": self.REIKI}
        ), mock.patch.dict(sys.modules, {"opensearch_client": fake_client_module}), mock.patch.object(
            celery_tasks, "_slug_counts", side_effect=lambda _c, alias, query: (empty if query else totals)[alias]
        ), mock.patch.object(
            celery_tasks, "_slug_medians", side_effect=lambda _c, alias, _field: medians[alias]
        ), mock.patch.object(
            celery_tasks.gijiroku_targets, "iter_gijiroku_targets", return_value=targets
        ), mock.patch.object(
            celery_tasks.reiki_targets, "iter_reiki_targets", return_value=reiki
        ), mock.patch.object(
            celery_tasks.generation_sweep_state,
            "filter_recently_queued",
            side_effect=lambda _kind, slugs, **_kw: list(slugs),
        ), mock.patch.object(celery_tasks.generation_sweep_state, "mark_queued"), mock.patch.object(
            celery_tasks.app, "send_task", send_task
        ), mock.patch("builtins.print"):
            celery_tasks.sweep_empty_body()
        return [(call.args[0].rsplit(".", 1)[-1], call.kwargs["kwargs"]) for call in send_task.call_args_list]

    def test_both_kinds_are_queued_as_forced_refetches(self) -> None:
        sent = self._sweep()
        self.assertIn(("run_gijiroku_cycle", {"name_filter": "g-short", "force": True}), sent)
        self.assertIn(("run_reiki_cycle", {"name_filter": "r-empty", "force": True}), sent)

    def test_short_reiki_bodies_are_not_refetched(self) -> None:
        # 告示の多い県（埼玉県・青森県）は、全文そろっていても中央値が短い。
        sent = self._sweep()
        self.assertNotIn("r-short", [kwargs["name_filter"] for _task, kwargs in sent])


@unittest.skipIf(celery_tasks is None, "celery が無い環境では確かめない")
class RegistryAuditWorkDirTest(unittest.TestCase):
    def _audit_with_work_dir(self, work_dir: Path) -> mock.MagicMock:
        audit = mock.MagicMock(return_value=mock.Mock(selected_rows=0, enabled_targets_changed=False))
        with mock.patch.object(
            celery_tasks.audit_minutes_registry,
            "DEFAULT_POLICY_CACHE",
            work_dir / "registry_policy_cache.json",
        ), mock.patch.object(celery_tasks.audit_minutes_registry, "audit_registry", audit):
            self.assertFalse(celery_tasks._audit_gijiroku_registry_changes())
        return audit

    def test_worker_without_the_minutes_work_dir_does_not_audit(self) -> None:
        # 例規の索引 worker では作業領域が行き先の無いリンク。監査すると台帳を
        # 書いたあとキャッシュを書けずに落ちる。
        with tempfile.TemporaryDirectory() as tmpdir:
            link = Path(tmpdir) / "gijiroku"
            try:
                os.symlink(Path(tmpdir) / "not-mounted", link)
            except OSError:
                self.skipTest("symlink を作れない環境")
            self.assertFalse(self._audit_with_work_dir(link).called)

    def test_worker_with_the_minutes_work_dir_audits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(self._audit_with_work_dir(Path(tmpdir)).called)

    def test_work_dir_not_made_yet_is_still_audited(self) -> None:
        # 作られる前の新しい環境では、監査がキャッシュの置き場を作る。
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(self._audit_with_work_dir(Path(tmpdir) / "gijiroku").called)


if __name__ == "__main__":
    unittest.main()
