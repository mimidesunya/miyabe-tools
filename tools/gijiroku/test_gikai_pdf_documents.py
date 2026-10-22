"""汎用クロール（独自）で毎回「エラー停止」になっていた 3 自治体の形（2026-10-21）。

- 菊陽町: 会議結果を Word の RTF で配る。.rtf をページとして開いて HTML でないので
  57 件を「開けなかったページ」に数え、索引の更新も止まっていた。
- 小清水町: 入口 1 件が開けずに候補 0 件。なぜ開けなかったのかが記録に残っていなかった。
- 舟橋村: `charset=none` を返す目次 2 件が開けず、0 バイトの PDF を取得エラーにし、
  同じ PDF を http と https の 2 回取りに行っていた。
"""

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from tools.gijiroku import gijiroku_planning, gijiroku_storage, minutes_kind
from tools.gijiroku.scrapers import gikai_pdf, kami_city_pdf

MINUTES_BODY = (
    "令和６年第２回菊陽町議会定例会会議録\n"
    "出席議員（１８名）\n欠席議員（なし）\n"
    "午前10時0分開会\n"
    "○議長（山田太郎君）　これより会議を開きます。\n"
    "○町長（佐藤次郎君）　提案理由を説明いたします。\n"
)
MEETING_RESULT_BODY = (
    "令和４年第２回菊陽町議会臨時会\n"
    "１　開　　会　　　令和４年８月２３日\n"
    "２　閉　　会　　　令和４年８月２３日\n"
    "３　会　　期　　　１日間\n"
    "４　議決件数　　　５件\n"
    "５　議決の状況　　報告１件、原案可決４件\n"
    "議案番号\t件名\t議決年月日\t結果\n"
    "議案第38号\t工事請負契約の締結について\tＲ4.8.23\t原案可決\n"
)


def rtf_bytes(text: str) -> bytes:
    """本文を Word と同じく `\\'xx`（CP932）で書いた RTF にする。"""
    body = "\\par ".join(
        "".join(f"\\'{byte:02x}" for byte in line.encode("cp932")) for line in text.splitlines()
    )
    return ("{\\rtf1\\ansi\\ansicpg932\\uc2\\deff0{\\fonttbl{\\f0\\fcharset128 MS Mincho;}}" + body + "}").encode(
        "latin-1"
    )


def _http_error(status: int, headers: dict | None = None, body: bytes = b"") -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    response._content = body
    return requests.HTTPError(f"{status} Error", response=response)


class _Response:
    def __init__(self, content: bytes, headers: dict, status: int = 200, encoding: str = "", apparent: str = "") -> None:
        self.content = content
        self.headers = headers
        self.status_code = status
        self.encoding = encoding
        self.apparent_encoding = apparent

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _http_error(self.status_code)


class _Session:
    def __init__(self, responses: dict[str, _Response]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return self.responses[url]


def crawl(start: str, pages: dict[str, object]) -> tuple[list, dict, list[str]]:
    requested: list[str] = []

    def fake_request_text(_session, url, _timeout_ms, **_kwargs):
        requested.append(url)
        outcome = pages.get(url, _http_error(404))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    walk: dict = {}
    with mock.patch.object(gikai_pdf, "request_text", side_effect=fake_request_text):
        items = gikai_pdf.crawl_pdf_items(object(), start, timeout_ms=1000, max_pages=50, max_depth=3, walk=walk)
    return items, walk, requested


class KikuyoRtfCrawlTest(unittest.TestCase):
    START = "https://www.town.kikuyo.lg.jp/gikai/list00327.html"
    KIJI = "https://www.town.kikuyo.lg.jp/gikai/kiji0033487/index.html"

    def test_rtf_link_is_a_candidate_not_a_page(self) -> None:
        start_html = (
            "<html><head><title>会議録</title></head><body>"
            f'<a href="{self.KIJI}">令和6年第2回菊陽町議会6月定例会</a></body></html>'
        )
        kiji_html = (
            "<html><head><title>令和6年第2回菊陽町議会6月定例会</title></head><body>"
            '<a href="3_3487_1_up_a.pdf">菊陽町令和6年6月定例会（PDF：4.36メガバイト）</a>'
            '<a href="3_3487_2_up_b.rtf">令和6年第2回定例会会議録（ファイル：120キロバイト）</a>'
            '<a href="3_3487_9615_up_gsv5h51g.rtf">8月臨時会会議結果（ファイル：76.8キロバイト）</a>'
            "</body></html>"
        )
        items, walk, requested = crawl(self.START, {self.START: start_html, self.KIJI: kiji_html})

        self.assertEqual(
            sorted(item.url.rsplit("/", 1)[-1] for item in items),
            ["3_3487_1_up_a.pdf", "3_3487_2_up_b.rtf"],
        )
        # RTF をページとして開きに行かない。開けなかったページにも数えない。
        self.assertFalse(any(url.endswith(".rtf") for url in requested))
        self.assertEqual(walk["missed_pages"], 0)
        # 会議結果は会議の記録ではないので、取りに行く前に落とす。
        self.assertEqual(walk["dropped_non_minutes"], 1)
        self.assertTrue(walk["dropped_urls"][0].endswith("3_3487_9615_up_gsv5h51g.rtf"))

    def test_extensionless_rtf_endpoint_becomes_a_candidate(self) -> None:
        endpoint = "https://www.town.kikuyo.lg.jp/gikai/download?id=1"
        start_html = f'<html><body><a href="{endpoint}">令和6年第2回定例会会議録</a></body></html>'
        # gikai_pdf が読み込んだほうの型で投げる（パッケージ経由の名前とは別物になる）。
        rtf_reply = gikai_pdf.NonHtmlResponse(
            "HTML ではない応答", content_type="application/rtf", head=b"{\\rtf1\\a"
        )
        items, walk, _requested = crawl(self.START, {self.START: start_html, endpoint: rtf_reply})
        self.assertEqual([item.url for item in items], [endpoint])
        self.assertEqual(walk["missed_pages"], 0)


class RtfDocumentFetchTest(unittest.TestCase):
    URL = "https://www.town.kikuyo.lg.jp/gikai/kiji0034795/3_4795_2_up_b.rtf"

    def setUp(self) -> None:
        kami_city_pdf._consecutive_exhausted_fetches = 0
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def plan_for(self, title: str, url: str) -> dict:
        item = kami_city_pdf.PdfMeetingItem(
            title=title,
            url=url,
            year_label="令和6年",
            source_year=2024,
            source_fino=None,
            page_url="https://www.town.kikuyo.lg.jp/gikai/kiji0034795/index.html",
            page_title="令和6年第2回菊陽町議会6月定例会",
            meeting_group=None,
        )
        plan = gijiroku_planning.build_base_plans([item], self.root / "downloads", use_group_dir=False)[0]
        gijiroku_planning.attach_text_output(plan, key="text_base")
        plan["pdf_path"] = self.root / "pdfs" / plan["year_dir"] / f"1_{plan['stem']}.rtf"
        return plan

    def test_request_accepts_rtf(self) -> None:
        raw = rtf_bytes("会議録")
        session = _Session({self.URL: _Response(raw, {"Content-Type": "application/rtf"})})
        self.assertEqual(kami_city_pdf.request_pdf_bytes(session, self.URL, 10_000), raw)

    def test_rtf_minutes_are_saved_as_text(self) -> None:
        session = _Session({self.URL: _Response(rtf_bytes(MINUTES_BODY), {"Content-Type": "application/rtf"})})
        plan = self.plan_for("令和6年第2回定例会会議録", self.URL)
        result = kami_city_pdf.process_pdf_meeting_plan(session, plan, no_resume=False, timeout_ms=1000)

        self.assertEqual(result["status"], "saved_text")
        output = Path(result["output_path"])
        # 索引は downloads の .txt(.gz) しか読まない。PDF と同じ形で置く。
        self.assertTrue(output.name.endswith(".txt.gz"))
        self.assertTrue(output.is_relative_to(self.root / "downloads"))
        saved = gzip.decompress(output.read_bytes()).decode("utf-8")
        self.assertIn("○議長（山田太郎君）　これより会議を開きます。", saved)
        self.assertIn(f"出典: {self.URL}", saved)
        # 取った RTF の写しは work 側に残す（次の周回で使い回す）。
        self.assertTrue(plan["pdf_path"].read_bytes().startswith(b"{\\rtf1"))

        # 手元の写しがあれば取得元へ取りに行かずに読み直せる。
        output.unlink()
        again = self.plan_for("令和6年第2回定例会会議録", self.URL)
        result = kami_city_pdf.process_pdf_meeting_plan(_Session({}), again, no_resume=False, timeout_ms=1000)
        self.assertEqual(result["status"], "saved_text")

    def test_meeting_result_is_not_minutes(self) -> None:
        self.assertEqual(
            minutes_kind.non_minutes_reason("8月臨時会会議結果（ファイル：76.8キロバイト）"),
            "non_minutes_label",
        )
        # 題名が「会議結果」を名乗らなくても、本文が議決の一覧なら会議録にしない。
        adoption = minutes_kind.adopt_minutes_document("会議資料", MEETING_RESULT_BODY)
        self.assertFalse(adoption.accepted)
        self.assertEqual(adoption.reason, "agenda_only")
        # 会議録を名乗る題名は、これまでどおり落とさない。
        self.assertIsNone(minutes_kind.non_minutes_reason("令和6年第1回臨時会会議結果及び会議録"))

    def test_ocr_is_not_tried_for_rtf(self) -> None:
        path = self.root / "work" / "pdfs" / "令和6年" / "1_x.rtf"
        path.parent.mkdir(parents=True)
        path.write_bytes(rtf_bytes(""))
        with mock.patch.object(kami_city_pdf.pdf_ocr, "is_enabled", return_value=True), mock.patch.object(
            kami_city_pdf.pdf_ocr, "ocr_pdf_text"
        ) as ocr:
            self.assertEqual(kami_city_pdf.ocr_pdf_when_enabled(path), ("", ""))
        ocr.assert_not_called()


class FunahashiTest(unittest.TestCase):
    BASE = "https://www.vill.funahashi.toyama.jp/kaigiroku/"

    def setUp(self) -> None:
        kami_city_pdf._consecutive_exhausted_fetches = 0
        self.addCleanup(setattr, kami_city_pdf, "_consecutive_exhausted_fetches", 0)

    def test_charset_none_page_with_cp932_only_character(self) -> None:
        # 28-12cont/mokuji.html: `charset=none`、推定は SHIFT_JIS、本文に「﨑」（CP932 にだけある字）。
        html = '<html><title>目次から見る</title><a href="28-12PDF/1gou28_12.pdf">第１号</a> 川﨑和夫</html>'
        url = self.BASE + "28-12cont/mokuji.html"
        session = _Session(
            {
                url: _Response(
                    html.encode("cp932"),
                    {"Content-Type": "text/html; charset=none"},
                    encoding="none",
                    apparent="SHIFT_JIS",
                )
            }
        )
        text = kami_city_pdf.request_text(session, url, 10_000)
        self.assertIn("川﨑和夫", text)
        self.assertIn("第１号", text)

    def test_empty_pdf_is_a_source_side_gap(self) -> None:
        url = self.BASE + "17-6cont/17-6PDF/1gou.pdf"
        session = _Session({url: _Response(b"", {"Content-Type": "application/pdf", "Content-Length": "0"})})
        item = kami_city_pdf.PdfMeetingItem(
            title="第１号（６月１５日）",
            url=url,
            year_label="平成29年",
            source_year=2017,
            source_fino=None,
            page_url=self.BASE + "17-6cont/mokuji.html",
            page_title="目次から見る",
            meeting_group=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = kami_city_pdf.process_pdf_meeting_plan(
                session,
                {"item": item, "pdf_path": Path(directory) / "pdfs" / "1gou.pdf"},
                no_resume=False,
                timeout_ms=1000,
            )
        self.assertEqual(result["status"], "source_missing")
        self.assertIn("0 バイト", result["error"])
        # やり直しても同じなので、1 回で諦める。
        self.assertEqual(session.calls, [url])

    def test_http_and_https_are_one_page_and_one_candidate(self) -> None:
        start = self.BASE + "17-6cont/houhou.html"
        index = self.BASE + "index.html"
        http_toc = "http://www.vill.funahashi.toyama.jp/kaigiroku/28-6cont/mokuji.html"
        https_toc = self.BASE + "28-6cont/mokuji.html"
        pages = {
            start: f'<html><body><a href="{index}">「舟橋村議会会議録」に戻る</a></body></html>',
            index: (
                "<html><head><title>舟橋村議会会議録</title></head><body>"
                f'<a href="{http_toc}">平成28年6月定例会 会議録</a>'
                f'<a href="{https_toc}">平成28年6月定例会 会議録</a>'
                '<a href="http://www.vill.funahashi.toyama.jp/kaigiroku/28-6cont/28-6PDF/1gou28_6.pdf">第１号 会議録</a>'
                "</body></html>"
            ),
            http_toc: '<html><body><a href="28-6PDF/1gou28_6.pdf">第１号（６月１０日）</a></body></html>',
            https_toc: '<html><body><a href="28-6PDF/1gou28_6.pdf">第１号（６月１０日）</a></body></html>',
        }
        items, walk, requested = crawl(start, pages)

        self.assertEqual(len(items), 1)
        self.assertEqual(sum(1 for url in requested if url.endswith("28-6cont/mokuji.html")), 1)
        self.assertEqual(walk["missed_pages"], 0)

    def test_previous_http_candidate_is_not_lost(self) -> None:
        # 前回は http の URL で本文を取れていた。今回 https の 1 件にまとめても、見失ったことにしない。
        http_urls = [f"http://www.vill.funahashi.toyama.jp/kaigiroku/{i}.pdf" for i in range(10)]
        https_urls = [url.replace("http://", "https://") for url in http_urls]
        self.assertEqual(gijiroku_storage.document_key(http_urls[0]), https_urls[0])
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            index = work_dir / "meetings_index.json"
            index.write_text(
                json.dumps([{"url": url} for url in http_urls + https_urls]), encoding="utf-8"
            )
            lines = ["title,year,url,status,output,pdf,error"]
            lines += [f"t,y,{url},saved_text,,," for url in http_urls + https_urls]
            (work_dir / "run_result_20261019_030000.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            accepted = gijiroku_storage.previous_accepted_urls(work_dir, {"items": {}})
            current = [{"url": url} for url in https_urls]
            self.assertFalse(gijiroku_storage.meetings_index_would_shrink(index, current, accepted_urls=accepted))

            # 会議録でないと落とした分は、http の URL で渡しても外れる。
            narrowed = gijiroku_storage.previous_accepted_urls(
                work_dir, {"items": {}}, excluding=[http_urls[0]]
            )
            self.assertNotIn(https_urls[0], narrowed)
            self.assertEqual(len(narrowed), len(http_urls) - 1)


class KoshimizuMissedReasonTest(unittest.TestCase):
    START = "https://www.town.koshimizu.hokkaido.jp/gikai/detail/00009799.html"

    def test_waf_refusal_is_recorded(self) -> None:
        blocked = _http_error(403, {"X-CDN": "Imperva", "X-Iinfo": "51-1 2NYN"})
        _items, walk, _requested = crawl(self.START, {self.START: blocked})
        self.assertEqual(walk["missed_pages"], 1)
        self.assertEqual(walk["missed_reasons"], {"HTTP 403（Imperva 経由）": 1})
        self.assertEqual(walk["missed_details"], [f"HTTP 403（Imperva 経由）: {self.START}"])

    def test_reasons_are_told_apart(self) -> None:
        reason = kami_city_pdf.fetch_failure_reason
        self.assertEqual(reason(requests.exceptions.ConnectTimeout("t")), "接続の時間切れ")
        self.assertEqual(reason(requests.exceptions.ReadTimeout("t")), "応答の時間切れ")
        self.assertEqual(reason(requests.exceptions.SSLError("certificate verify failed")), "TLS の検証に失敗")
        self.assertEqual(reason(requests.ConnectionError("reset")), "接続できない・切られた")
        self.assertEqual(reason(_http_error(503)), "HTTP 503")
        self.assertEqual(
            reason(_http_error(403, body=b"<html>Request unsuccessful. Incapsula incident ID: 1</html>")),
            "HTTP 403（WAF に断られた）",
        )
        self.assertEqual(
            reason(kami_city_pdf.NonHtmlResponse("x", content_type="application/msword")),
            "HTML ではない応答（application/msword）",
        )


class EntryRetryTest(unittest.TestCase):
    """入口だけは長く間を置いてやり直す（舟橋村は 10/22 に本番から入口が時間切れ 3 回で候補 0 件）。"""

    START = "https://www.vill.funahashi.toyama.jp/kaigiroku/17-6cont/houhou.html"

    def setUp(self) -> None:
        kami_city_pdf._consecutive_exhausted_fetches = 0
        self.addCleanup(setattr, kami_city_pdf, "_consecutive_exhausted_fetches", 0)
        self.sleep = mock.patch.object(kami_city_pdf.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def fetch(self, outcomes: list, **kwargs) -> str:
        mock.patch.object(kami_city_pdf, "_request_text_once", side_effect=outcomes).start()
        return kami_city_pdf.request_text(object(), self.START, 10_000, **kwargs)

    def test_entry_waits_longer_between_attempts(self) -> None:
        timeout = requests.exceptions.ConnectTimeout("t")
        self.assertEqual(self.fetch([timeout, timeout, "<html></html>"], entry=True), "<html></html>")
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], list(kami_city_pdf.ENTRY_FETCH_RETRY_WAITS_SECONDS))

    def test_other_pages_keep_the_short_waits(self) -> None:
        timeout = requests.exceptions.ConnectTimeout("t")
        self.fetch([timeout, "<html></html>"])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [kami_city_pdf.FETCH_RETRY_WAITS_SECONDS[0]])

    def test_crawl_marks_only_the_start_page_as_entry(self) -> None:
        child = "https://www.vill.funahashi.toyama.jp/kaigiroku/17-6cont/r7.html"
        seen: list[tuple[str, bool]] = []
        pages = {self.START: f'<a href="{child}">令和7年</a>', child: "<html></html>"}

        def fake_request_text(_session, url, _timeout_ms, *, entry=False):
            seen.append((url, entry))
            return pages[url]

        with mock.patch.object(gikai_pdf, "request_text", side_effect=fake_request_text):
            gikai_pdf.crawl_pdf_items(object(), self.START, timeout_ms=1000, max_pages=10, max_depth=2)
        self.assertEqual(seen, [(self.START, True), (child, False)])


if __name__ == "__main__":
    unittest.main()
