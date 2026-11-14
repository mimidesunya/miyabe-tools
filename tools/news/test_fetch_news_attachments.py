"""お知らせの添付の PDF を読んでためる。取得元には当てず、応答を作って渡す。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.document_crawl import JST  # noqa: E402
from tools.news import fetch_news_attachments as attachments  # noqa: E402
from tools.news.fetch_news_feeds import HostPacer  # noqa: E402
from tools.news.pdf_text_worker import PdfTextWorker, PdfTimeout  # noqa: E402
from tools.news.test_fetch_news_bodies import FakeResponse, FakeSession, make_pdf  # noqa: E402

SLUG = "99999-example-shi"
SITE = "https://www.city.example.jp"


def body_line(item_id: str, *names: str, fetched_at: str = "2026-11-14 10:00:00") -> dict:
    return {"id": item_id, "jis_code": "99999", "url": f"{SITE}/{item_id}.html", "fetched_at": fetched_at,
            "kind": "html", "text": "お知らせ",
            "attachments": [{"url": f"{SITE}/files/{name}", "text": f"資料 {name}"} for name in names]}


def pdf_response(*texts: str) -> FakeResponse:
    return FakeResponse(200, make_pdf(*texts), "application/pdf")


class AttachmentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "data"
        self.work_root = root / "work"
        (self.data_root / SLUG / "items").mkdir(parents=True)
        self.bodies_path = self.data_root / SLUG / "bodies" / "2026.jsonl"
        self.bodies_path.parent.mkdir(parents=True)
        # 読み直しの待ちを確かめるときは、この時刻を進める。
        self.now = datetime.now(JST)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_bodies(self, *records: dict, partial: str = "") -> None:
        with self.bodies_path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.write(partial)

    def run_once(self, responses: dict[str, object], budget: float = 600, per_run: int = 1000) -> dict:
        session = FakeSession(responses)
        summary = attachments.run(data_root=self.data_root, work_root=self.work_root, session=session,
                                  pacer=HostPacer(interval=0), per_run=per_run, workers=1, budget_seconds=budget,
                                  quiet=True, clock=lambda: self.now)
        summary["calls"] = session.calls
        return summary

    def lines(self) -> list[dict]:
        path = self.data_root / SLUG / "attachments" / f"{self.now.year}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def state(self) -> dict:
        return json.loads((self.data_root / SLUG / "attachments" / "state.json").read_text(encoding="utf-8"))

    def test_pdfs_are_read_page_by_page_once(self) -> None:
        self.add_bodies(body_line("a", "notice.pdf", "list.xlsx"), partial='{"id": "b", "attachments": [')
        summary = self.run_once({f"{SITE}/files/notice.pdf": pdf_response("Road works", "Detour map")})
        self.assertEqual((summary["queued"], summary["fetched"], summary["waiting_after"]), (1, 1, 0))
        self.assertEqual(summary["calls"], [f"{SITE}/files/notice.pdf"])
        line = self.lines()[0]
        self.assertEqual((line["kind"], line["pages"], line["text_pages"], line["item"]), ("pdf", 2, 2, "a"))
        self.assertEqual(line["link_text"], "資料 notice.pdf")
        self.assertEqual(line["text"].split("\f"), ["Road works", "Detour map"])
        self.assertEqual(len(line["sha1"]), 40)
        # 書きかけの本文の行は次の回に読む。読んだ PDF は読み直さない。
        self.assertLess(self.state()["cursor"]["2026.jsonl"], self.bodies_path.stat().st_size)
        summary = self.run_once({})
        self.assertEqual((summary["queued"], summary["calls"]), (0, []))

    def test_same_url_once_and_twenty_per_item(self) -> None:
        self.add_bodies(body_line("a", "same.pdf"))
        self.run_once({f"{SITE}/files/same.pdf": pdf_response("Same")})
        many = [f"f{n:02d}.pdf" for n in range(25)]
        self.add_bodies(body_line("b", "same.pdf", *many))
        summary = self.run_once({f"{SITE}/files/{name}": pdf_response(name) for name in many})
        self.assertEqual(summary["queued"], attachments.MAX_PER_ITEM - 1)
        self.assertNotIn(f"{SITE}/files/same.pdf", summary["calls"])
        self.assertNotIn(f"{SITE}/files/f24.pdf", summary["calls"])

    def test_gone_too_large_and_not_a_pdf(self) -> None:
        self.add_bodies(body_line("a", "gone.pdf", "huge.pdf", "moved.pdf"))
        huge = FakeResponse(200, b"", "application/pdf",
                            headers={"Content-Length": str(attachments.MAX_BYTES + 1)})
        summary = self.run_once({
            f"{SITE}/files/gone.pdf": FakeResponse(404),
            f"{SITE}/files/huge.pdf": huge,
            f"{SITE}/files/moved.pdf": FakeResponse(200, b"<html><body>top</body></html>", url=f"{SITE}/"),
        })
        self.assertEqual((summary["gone"], summary["skipped"], summary["waiting_after"]), (1, 2, 0))
        by_url = {line["url"].rsplit("/", 1)[-1]: line for line in self.lines()}
        self.assertEqual(by_url["gone.pdf"]["kind"], "gone")
        self.assertEqual((by_url["huge.pdf"]["kind"], by_url["huge.pdf"]["size"]), ("too_large", attachments.MAX_BYTES + 1))
        self.assertEqual((by_url["moved.pdf"]["kind"], by_url["moved.pdf"]["final_url"]), ("other", f"{SITE}/"))

    def test_scanned_pdf_keeps_only_the_page_count(self) -> None:
        self.add_bodies(body_line("a", "scan.pdf"))
        summary = self.run_once({f"{SITE}/files/scan.pdf": pdf_response("", "")})
        self.assertEqual(summary["no_text"], 1)
        line = self.lines()[0]
        self.assertEqual((line["kind"], line["pages"]), ("pdf", 2))
        self.assertNotIn("text", line)

    def test_failures_are_retried_with_longer_waits_then_given_up(self) -> None:
        self.add_bodies(body_line("a", "slow.pdf"))
        url = f"{SITE}/files/slow.pdf"
        for attempt in range(1, attachments.MAX_TRIES):
            summary = self.run_once({url: requests.ConnectTimeout("no answer")})
            self.assertEqual((summary["retry"], summary["waiting_after"]), (1, 1))
            self.assertEqual(self.state()["pending"][url]["tries"], attempt)
            # 待ちが明ける前の回では読みに行かない。
            self.now += timedelta(hours=attachments.RETRY_WAIT_HOURS[attempt - 1]) - timedelta(minutes=1)
            self.assertEqual(self.run_once({})["calls"], [])
            self.now += timedelta(minutes=2)
        summary = self.run_once({url: FakeResponse(503)})
        self.assertEqual((summary["failed"], summary["waiting_after"]), (1, 0))
        self.assertEqual((self.lines()[0]["kind"], self.lines()[0]["error"]), ("failed", "HTTP 503"))

    def test_nothing_starts_after_the_budget(self) -> None:
        self.add_bodies(body_line("a", "one.pdf", "two.pdf"))
        summary = self.run_once({}, budget=0)
        self.assertEqual((summary["out_of_time"], summary["waiting_after"], summary["calls"]), (2, 2, []))

    def test_newest_bodies_first_then_page_order(self) -> None:
        self.add_bodies(body_line("old", "o1.pdf", fetched_at="2026-11-13 10:00:00"),
                        body_line("new", "n1.pdf", "n2.pdf", fetched_at="2026-11-14 10:00:05"))
        responses = {f"{SITE}/files/{name}": pdf_response(name) for name in ("o1.pdf", "n1.pdf", "n2.pdf")}
        summary = self.run_once(responses, per_run=1)
        self.assertEqual(summary["per_municipality"], attachments.MIN_PER_MUNICIPALITY)
        self.assertEqual(summary["calls"], [f"{SITE}/files/n1.pdf", f"{SITE}/files/n2.pdf", f"{SITE}/files/o1.pdf"])


class SleepyWorker(PdfTextWorker):
    """答えない子。"""

    def _ensure(self) -> subprocess.Popen:
        if self._process is None or self._process.poll() is not None:
            self._process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        return self._process


class PdfTextWorkerTest(unittest.TestCase):
    def test_reads_in_a_child_and_limits_pages(self) -> None:
        worker = PdfTextWorker(timeout=60)
        try:
            self.assertEqual(worker.read(make_pdf("One", "Two", "Three"), 2), (["One", "Two"], 3))
            self.assertEqual(worker.read(b"not a pdf", 10), ([], 0))
        finally:
            worker.close()

    def test_a_child_that_does_not_answer_is_stopped(self) -> None:
        worker = SleepyWorker(timeout=1)
        with self.assertRaises(PdfTimeout):
            worker.read(make_pdf("One"), 10)
        self.assertIsNone(worker._process)


if __name__ == "__main__":
    unittest.main()
