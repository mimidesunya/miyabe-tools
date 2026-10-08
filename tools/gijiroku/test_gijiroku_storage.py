import gzip
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from tools.gijiroku import gijiroku_planning, gijiroku_storage


class ExistingMinutesArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.slug_dir = root / "work" / "gijiroku" / "99999-test-shi"
        self.download_dir = self.slug_dir / "downloads" / "令和6年"
        self.download_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def archived(self, filename: str) -> list[Path]:
        archive = self.slug_dir / "_archive"
        return list(archive.rglob(filename)) if archive.exists() else []

    def attach_text(self, stem: str) -> dict[str, object]:
        return gijiroku_planning.attach_text_output(
            {"meeting_download_dir": self.download_dir, "stem": stem}
        )

    def test_zero_byte_gzip_is_archived_and_planned_again(self) -> None:
        broken = self.download_dir / "zero.txt.gz"
        broken.write_bytes(b"")

        plan = self.attach_text("zero")

        self.assertTrue(plan["needs_work"])
        self.assertIsNone(plan["existing_output"])
        self.assertFalse(broken.exists())
        archived = self.archived(broken.name)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), b"")

    def test_truncated_gzip_is_archived_and_planned_again(self) -> None:
        broken = self.download_dir / "truncated.txt.gz"
        broken.write_bytes(gzip.compress("会議録本文".encode("utf-8"))[:-4])

        plan = self.attach_text("truncated")

        self.assertTrue(plan["needs_work"])
        self.assertFalse(broken.exists())
        self.assertEqual(len(self.archived(broken.name)), 1)

    def test_whitespace_only_text_is_not_a_completed_artifact(self) -> None:
        broken = self.download_dir / "blank.txt.gz"
        broken.write_bytes(gzip.compress(b" \r\n\t"))

        plan = self.attach_text("blank")

        self.assertTrue(plan["needs_work"])
        self.assertFalse(broken.exists())
        self.assertEqual(len(self.archived(broken.name)), 1)

    def test_broken_gzip_does_not_hide_valid_plain_text(self) -> None:
        broken = self.download_dir / "fallback.txt.gz"
        valid = self.download_dir / "fallback.txt"
        broken.write_bytes(b"not-a-gzip-stream")
        valid.write_text("会議録本文\n", encoding="utf-8")

        plan = self.attach_text("fallback")

        self.assertFalse(plan["needs_work"])
        self.assertEqual(plan["existing_output"], valid)
        self.assertFalse(broken.exists())
        self.assertTrue(valid.exists())
        self.assertEqual(len(self.archived(broken.name)), 1)

    def test_named_outputs_archive_only_invalid_candidates(self) -> None:
        valid = self.download_dir / "mixed.txt"
        empty_html = self.download_dir / "mixed.html.gz"
        sidecar = self.download_dir / "mixed.csv"
        valid.write_text("会議録本文\n", encoding="utf-8")
        empty_html.write_bytes(gzip.compress(b"<html><body> </body></html>"))
        sidecar.write_text("status,error\n", encoding="utf-8")

        outputs = gijiroku_storage.existing_named_outputs(self.download_dir, "mixed")

        self.assertEqual(outputs, [valid])
        self.assertTrue(valid.exists())
        self.assertFalse(empty_html.exists())
        self.assertFalse(sidecar.exists())
        self.assertEqual(len(self.archived(empty_html.name)), 1)
        self.assertEqual(len(self.archived(sidecar.name)), 1)

    def test_broken_text_does_not_hide_valid_html_with_the_same_stem(self) -> None:
        broken_text = self.download_dir / "fallback.txt.gz"
        valid_html = self.download_dir / "fallback.html.gz"
        broken_text.write_bytes(b"broken gzip")
        valid_html.write_bytes(
            gzip.compress("<html><body>会議録本文</body></html>".encode("utf-8"))
        )

        outputs = gijiroku_storage.existing_named_outputs(self.download_dir, "fallback")

        self.assertEqual(outputs, [valid_html])
        self.assertFalse(broken_text.exists())
        self.assertTrue(valid_html.exists())
        self.assertEqual(len(self.archived(broken_text.name)), 1)
        self.assertEqual(self.archived(valid_html.name), [])

    def test_text_plan_keeps_raw_pdf_with_the_same_stem(self) -> None:
        raw_pdf = self.download_dir / "pdf-source.pdf"
        raw_pdf.write_bytes(b"%PDF-1.4\nraw source\n%%EOF\n")

        plan = self.attach_text("pdf-source")

        self.assertTrue(plan["needs_work"])
        self.assertTrue(raw_pdf.exists())
        self.assertEqual(self.archived(raw_pdf.name), [])

    def test_custom_output_archives_outside_downloads(self) -> None:
        custom_root = Path(self.temporary.name) / "custom-output"
        custom_download = custom_root / "downloads" / "令和6年"
        custom_download.mkdir(parents=True)
        broken = custom_download / "broken.txt.gz"
        broken.write_bytes(b"")

        plan = gijiroku_planning.attach_text_output(
            {"meeting_download_dir": custom_download, "stem": "broken"}
        )

        self.assertTrue(plan["needs_work"])
        self.assertFalse(broken.exists())
        self.assertEqual(len(list((custom_root / "_archive").rglob(broken.name))), 1)
        self.assertFalse((custom_root / "downloads" / "_archive").exists())


class AtomicMinutesWriteTest(unittest.TestCase):
    def test_writer_failure_preserves_previous_file_and_removes_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            destination = directory / "meeting.txt"
            destination.write_bytes(b"previous body")
            observed: list[Path] = []

            def fail_after_partial_write(temporary_path: Path) -> None:
                observed.append(temporary_path)
                temporary_path.write_bytes(b"partial body")
                raise RuntimeError("interrupted")

            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                gijiroku_storage.write_via_temporary_file(
                    destination,
                    fail_after_partial_write,
                )

            self.assertEqual(destination.read_bytes(), b"previous body")
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0].parent, directory)
            self.assertTrue(observed[0].name.startswith(".meeting.txt."))
            self.assertFalse(observed[0].exists())

    def test_gzip_payload_is_fsynced_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "meeting.txt"
            events: list[str] = []
            real_fsync = os.fsync
            real_replace = os.replace

            def recording_fsync(fd: int) -> None:
                events.append("fsync")
                real_fsync(fd)

            def recording_replace(
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                target: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            ) -> None:
                events.append("replace")
                real_replace(source, target)

            with (
                mock.patch.object(gijiroku_storage.os, "fsync", side_effect=recording_fsync),
                mock.patch.object(gijiroku_storage.os, "replace", side_effect=recording_replace),
            ):
                written = gijiroku_storage.write_bytes(
                    destination,
                    "会議録本文".encode("utf-8"),
                    compress=True,
                )

            self.assertEqual(events, ["fsync", "replace"])
            self.assertEqual(gzip.decompress(written.read_bytes()).decode("utf-8"), "会議録本文")


class EmptyDiscoveryTest(unittest.TestCase):
    """前は取れていたのに会議候補 0 件が続く入口を、探し直しの対象として覚える（美深町など）。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work_dir = Path(self.temporary.name)
        self.index = self.work_dir / "meetings_index.json"
        self.index.write_text('[{"url": "https://example.jp/a.pdf"}]', encoding="utf-8")

    def observe(self, when: str) -> dict:
        return gijiroku_storage.observe_empty_discovery(self.work_dir, now=datetime.fromisoformat(when))

    def test_empty_runs_over_a_week_mean_the_entry_is_dead(self) -> None:
        self.observe("2026-09-20 03:00:00")
        self.observe("2026-09-24 03:00:00")
        self.assertIsNone(gijiroku_storage.entry_looks_dead(self.work_dir))
        self.observe("2026-09-28 03:00:00")

        record = gijiroku_storage.entry_looks_dead(self.work_dir)
        self.assertIsNotNone(record)
        self.assertEqual(record["count"], 3)

    def test_calls_within_one_run_count_once(self) -> None:
        self.observe("2026-09-20 03:00:00")
        self.observe("2026-09-20 03:10:00")
        self.assertEqual(gijiroku_storage.load_empty_discovery(self.work_dir)["count"], 1)

    def test_many_empty_runs_in_two_days_are_not_enough(self) -> None:
        for when in ("2026-09-27 03:00:00", "2026-09-27 15:00:00", "2026-09-28 03:00:00", "2026-09-28 15:00:00"):
            self.observe(when)
        self.assertIsNone(gijiroku_storage.entry_looks_dead(self.work_dir))

    def test_saving_an_empty_list_is_observed_and_a_real_list_clears_it(self) -> None:
        gijiroku_storage.save_meetings_index(self.index, [])
        self.assertEqual(gijiroku_storage.load_empty_discovery(self.work_dir)["count"], 1)
        self.assertIn("a.pdf", self.index.read_text(encoding="utf-8"))

        gijiroku_storage.save_meetings_index(self.index, [{"url": "https://example.jp/a.pdf"}])
        self.assertEqual(gijiroku_storage.load_empty_discovery(self.work_dir), {})


if __name__ == "__main__":
    unittest.main()
