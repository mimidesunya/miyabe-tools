"""お知らせのリンク先を読んでためる。取得元には当てず、応答を作って渡す。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.document_crawl import JST  # noqa: E402
from tools.news import fetch_news_bodies as bodies  # noqa: E402
from tools.news.fetch_news_feeds import HostPacer  # noqa: E402

SLUG = "99999-example-shi"
FILLER = "市民のみなさまへのお知らせです。" * 8


def page(text: str) -> bytes:
    return f"<html><body><article><p>{text}</p><a href='/x.pdf'>資料</a></article></body></html>".encode("utf-8")


def make_pdf(*texts: str) -> bytes:
    """頁ごとに本文を持つ小さな PDF。pypdf で抜き出せる形にする。"""
    count = len(texts)
    page_numbers = [3 + 2 * n for n in range(count)]
    font_number = 3 + 2 * count
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
    ]
    for number, text in zip(page_numbers, texts):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {number + 1} 0 R "
                       f"/Resources << /Font << /F1 {font_number} 0 R >> >> >>".encode())
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", content_type: str = "text/html; charset=utf-8",
                 url: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.body = body
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.url = url

    def iter_content(self, chunk_size: int = 0):
        yield self.body

    def close(self) -> None:
        pass


class FakeSession:
    """URL ごとに応答を決める。例外を入れればつながらない扱い。"""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        response.url = response.url or url
        return response


def item(item_id: str, revision: int = 0, seen: str = "2026-11-14 10:00:00", date: str = "2026-11-14") -> dict:
    return {"id": item_id, "jis_code": "99999", "url": f"https://www.city.example.jp/{item_id}.html",
            "title": item_id, "date": date, "first_seen_at": seen, "revision": revision}


class BodiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "data"
        self.work_root = root / "work"
        self.items_path = self.data_root / SLUG / "items" / "2026.jsonl"
        self.items_path.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_items(self, *records: dict, partial: str = "") -> None:
        with self.items_path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.write(partial)

    def run_once(self, responses: dict[str, object], per_run: int = 100) -> dict:
        session = FakeSession(responses)
        summary = bodies.run(data_root=self.data_root, work_root=self.work_root, session=session,
                             pacer=HostPacer(interval=0), per_run=per_run, workers=1, quiet=True)
        summary["calls"] = session.calls
        return summary

    def lines(self) -> list[dict]:
        path = self.data_root / SLUG / "bodies" / f"{datetime.now(JST).year}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def state(self) -> dict:
        return json.loads((self.data_root / SLUG / "bodies" / "state.json").read_text(encoding="utf-8"))

    def url(self, item_id: str) -> str:
        return f"https://www.city.example.jp/{item_id}.html"

    def test_new_items_are_read_once(self) -> None:
        self.add_items(item("a"), partial='{"id": "b", "url": "https://www.city.ex')
        summary = self.run_once({self.url("a"): FakeResponse(200, page(FILLER))})
        self.assertEqual((summary["queued"], summary["fetched"], summary["waiting_after"]), (1, 1, 0))
        line = self.lines()[0]
        self.assertEqual((line["id"], line["kind"], line["status"]), ("a", "html", 200))
        self.assertTrue(line["text"].startswith("市民のみなさま"))
        self.assertEqual(line["attachments"], [{"url": "https://www.city.example.jp/x.pdf", "text": "資料"}])
        # 書きかけの行は次の回に読む。
        self.assertEqual(self.state()["cursor"]["2026.jsonl"], self.items_path.stat().st_size - len(
            '{"id": "b", "url": "https://www.city.ex'.encode("utf-8")))
        summary = self.run_once({})
        self.assertEqual((summary["queued"], summary["calls"]), (0, []))

    def test_a_new_revision_is_read_again_but_same_text_adds_no_line(self) -> None:
        self.add_items(item("a"))
        self.run_once({self.url("a"): FakeResponse(200, page(FILLER))})
        self.add_items(item("a", revision=1, date="2026-11-15"))
        summary = self.run_once({self.url("a"): FakeResponse(200, page(FILLER))})
        self.assertEqual((summary["fetched"], summary["unchanged"]), (0, 1))
        self.assertEqual(len(self.lines()), 1)
        self.add_items(item("a", revision=2, date="2026-12-01"))
        summary = self.run_once({self.url("a"): FakeResponse(200, page("12 月の人口です。" + FILLER))})
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual([line["revision"] for line in self.lines()], [0, 2])

    def test_gone_pages_leave_a_line(self) -> None:
        self.add_items(item("a"))
        summary = self.run_once({self.url("a"): FakeResponse(404)})
        self.assertEqual(summary["gone"], 1)
        self.assertEqual((self.lines()[0]["kind"], self.lines()[0]["status"]), ("gone", 404))
        self.assertEqual(self.state()["done"]["a"], [0, "gone"])

    def test_failures_are_retried_then_given_up(self) -> None:
        self.add_items(item("a"))
        for attempt in range(1, bodies.MAX_TRIES):
            summary = self.run_once({self.url("a"): requests.ConnectionError("refused")})
            self.assertEqual((summary["retry"], summary["waiting_after"]), (1, 1))
            self.assertEqual(self.state()["pending"]["a"]["tries"], attempt)
        summary = self.run_once({self.url("a"): FakeResponse(503)})
        self.assertEqual((summary["failed"], summary["waiting_after"]), (1, 0))
        self.assertEqual((self.lines()[0]["kind"], self.lines()[0]["error"]), ("failed", "HTTP 503"))

    def test_too_large_is_not_retried(self) -> None:
        self.add_items(item("a"))
        huge = FakeResponse(200, b"", headers={"Content-Length": str(bodies.MAX_BYTES + 1)})
        summary = self.run_once({self.url("a"): huge})
        self.assertEqual((summary["failed"], summary["waiting_after"]), (1, 0))
        self.assertEqual((self.lines()[0]["kind"], self.lines()[0]["size"]), ("too_large", bodies.MAX_BYTES + 1))

    def test_http_that_does_not_answer_is_read_over_https(self) -> None:
        plain = [dict(item(name), url=f"http://www.city.example.jp/{name}.html") for name in ("a", "b")]
        self.add_items(*plain)
        summary = self.run_once({
            "http://www.city.example.jp/a.html": requests.ConnectTimeout("no answer"),
            "https://www.city.example.jp/a.html": FakeResponse(200, page(FILLER)),
            "https://www.city.example.jp/b.html": FakeResponse(200, page(FILLER)),
        })
        self.assertEqual(summary["fetched"], 2)
        # 2 件目からは http を試さない。
        self.assertNotIn("http://www.city.example.jp/b.html", summary["calls"])
        self.assertEqual(self.lines()[0]["final_url"], "https://www.city.example.jp/a.html")

    def test_pdf_and_other_kinds(self) -> None:
        self.add_items(item("p"), item("i"))
        summary = self.run_once({
            self.url("p"): FakeResponse(200, make_pdf("Notice of road works"), "application/pdf"),
            self.url("i"): FakeResponse(200, b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "image/png"),
        })
        self.assertEqual(summary["fetched"], 2)
        by_id = {line["id"]: line for line in self.lines()}
        self.assertEqual((by_id["p"]["kind"], by_id["p"]["pages"]), ("pdf", 1))
        self.assertIn("Notice of road works", by_id["p"]["text"])
        self.assertEqual((by_id["i"]["kind"], by_id["i"]["media_type"]), ("other", "image/png"))
        self.assertNotIn("text", by_id["i"])

    def test_newest_first_within_the_limit(self) -> None:
        self.add_items(*[item(f"old{n}", seen="2026-11-13 13:00:00", date=f"2026-11-0{n}") for n in range(1, 8)])
        self.add_items(item("new", seen="2026-11-14 16:00:05"))
        responses = {self.url(f"old{n}"): FakeResponse(200, page(FILLER)) for n in range(1, 8)}
        responses[self.url("new")] = FakeResponse(200, page(FILLER))
        summary = self.run_once(responses, per_run=1)
        self.assertEqual(summary["per_municipality"], bodies.MIN_PER_MUNICIPALITY)
        self.assertEqual(summary["calls"], [self.url("new")] + [self.url(f"old{n}") for n in (7, 6, 5, 4)])
        self.assertEqual(summary["waiting_after"], 3)
        self.assertTrue((self.work_root / "bodies_last_run.json").exists())

    def test_limit_per_municipality(self) -> None:
        self.assertEqual(bodies.per_municipality_limit(4000, 1391), bodies.MIN_PER_MUNICIPALITY)
        self.assertEqual(bodies.per_municipality_limit(4000, 400), 10)
        self.assertEqual(bodies.per_municipality_limit(4000, 1), bodies.MAX_PER_MUNICIPALITY)


if __name__ == "__main__":
    unittest.main()
