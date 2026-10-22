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
