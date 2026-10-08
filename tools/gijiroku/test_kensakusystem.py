import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from tools.gijiroku.scrapers import kensakusystem


# 伊丹市で 2026-10 に返っていた応答そのまま。HTTP 200 で、フレームの代わりにこれだけが返る。
SOURCE_FILE_MISSING_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<HTML>
<HEAD>
<META http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
</HEAD>
<body>
ERROR:ファイルの読込みに失敗しました。<BR>H240223B23
</body>
</html>
"""

ITEM_URL = (
    "https://www.kensakusystem.jp/itami/cgi-bin3/ResultFrame.exe"
    "?Code=4rrbkc8gx8cmrrscun&fileName=H240223B23&startPos=-1"
)


def meeting_item() -> kensakusystem.MeetingItem:
    return kensakusystem.MeetingItem(
        title="中心市街地活性化等対策特別委員会（ 2月23日）",
        url=ITEM_URL,
        year_label="平成24年",
    )


class FetchMeetingTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_request_text = kensakusystem.request_text

    def tearDown(self) -> None:
        kensakusystem.request_text = self.original_request_text

    def serve(self, pages: dict[str, str]) -> None:
        def request_text(_opener, url, _timeout_ms, **_kwargs):
            for key, body in pages.items():
                if key in url:
                    return body, url
            raise AssertionError(url)

        kensakusystem.request_text = request_text

    def test_vendor_file_error_is_source_missing(self) -> None:
        # 取得元が文書ファイルを失っている。何度取りに行っても同じなので、
        # 取得エラーではなく取得元の欠落として返す。
        self.serve({"ResultFrame.exe": SOURCE_FILE_MISSING_HTML})

        with self.assertRaises(kensakusystem.SourceDocumentMissing):
            kensakusystem.fetch_meeting_text(None, meeting_item(), 1_000)

    def test_unexpected_frame_page_is_still_an_error(self) -> None:
        # 取得元の欠落と言える応答でなければ、従来どおり取得エラーにする。
        self.serve({"ResultFrame.exe": "<html><body>メンテナンス中です</body></html>"})

        with self.assertRaises(RuntimeError) as caught:
            kensakusystem.fetch_meeting_text(None, meeting_item(), 1_000)
        self.assertNotIsInstance(caught.exception, kensakusystem.SourceDocumentMissing)

    def test_vendor_file_error_in_body_frame_is_not_saved_as_minutes(self) -> None:
        # 本文の段で同じ応答が返っても、エラー文を会議録として保存しない。
        self.serve(
            {
                "ResultFrame.exe": '<FRAMESET><FRAME SRC="/itami/cgi-bin3/r_TextFrame.exe?x/H240223B23/-1/0//10/1/2329//0/0/0" NAME="TEXTW"></FRAMESET>',
                "r_TextFrame.exe": '<FRAMESET><FRAME SRC="/itami/cgi-bin3/GetText3.exe?x/H240223B23/-1/0/0/0/0"></FRAMESET>',
                "GetText3.exe": SOURCE_FILE_MISSING_HTML,
            }
        )

        with self.assertRaises(kensakusystem.SourceDocumentMissing):
            kensakusystem.fetch_meeting_text(None, meeting_item(), 1_000)


class RequestRetryTest(unittest.TestCase):
    """時間切れ 1 回で木の枝を諦めない（目黒区・高槻市などがエラー停止のままだった）。"""

    def setUp(self) -> None:
        kensakusystem._consecutive_exhausted_fetches = 0
        self.sleep = mock.patch.object(kensakusystem.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(setattr, kensakusystem, "_consecutive_exhausted_fetches", 0)

    def serve(self, outcomes: list) -> list[int]:
        timeouts: list[int] = []

        def once(_opener, url, timeout_ms, **_kwargs):
            timeouts.append(timeout_ms)
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, url

        mock.patch.object(kensakusystem, "_request_text_once", once).start()
        return timeouts

    def test_timeout_is_retried_with_a_longer_wait(self) -> None:
        timeouts = self.serve([TimeoutError("The read operation timed out"), "<html>ok</html>"])

        body, _ = kensakusystem.request_text(None, "https://example.jp/See.exe", 10_000)

        self.assertEqual(body, "<html>ok</html>")
        self.assertEqual(timeouts, [10_000, kensakusystem.FETCH_RETRY_MIN_TIMEOUT_MS])

    def test_connection_error_wrapped_in_urlerror_is_retried(self) -> None:
        self.serve([URLError(TimeoutError("timed out")), URLError(ConnectionResetError()), "<html>ok</html>"])

        body, _ = kensakusystem.request_text(None, "https://example.jp/See.exe", 10_000)

        self.assertEqual(body, "<html>ok</html>")

    def test_server_error_is_retried_but_not_found_is_not(self) -> None:
        self.serve([HTTPError("u", 503, "busy", {}, None), "<html>ok</html>"])
        self.assertEqual(kensakusystem.request_text(None, "u", 10_000)[0], "<html>ok</html>")

        timeouts = self.serve([HTTPError("u", 404, "gone", {}, None)])
        with self.assertRaises(HTTPError):
            kensakusystem.request_text(None, "u", 10_000)
        self.assertEqual(len(timeouts), 1)

    def test_gives_up_after_retries(self) -> None:
        timeouts = self.serve([TimeoutError("t")] * 3)

        with self.assertRaises(TimeoutError):
            kensakusystem.request_text(None, "u", 10_000)
        self.assertEqual(len(timeouts), 1 + len(kensakusystem.FETCH_RETRY_WAITS_SECONDS))

    def test_stops_retrying_while_the_source_is_down(self) -> None:
        # 取得元が落ちているときに 1 件ごと 1 分以上待たない。
        attempts_per_call = 1 + len(kensakusystem.FETCH_RETRY_WAITS_SECONDS)
        give_up = kensakusystem.FETCH_RETRY_GIVE_UP_AFTER
        timeouts = self.serve([TimeoutError("t")] * (attempts_per_call * give_up + 1) + ["<html>ok</html>"])

        for _ in range(give_up + 1):
            with self.assertRaises(TimeoutError):
                kensakusystem.request_text(None, "u", 10_000)
        self.assertEqual(len(timeouts), attempts_per_call * give_up + 1)

        # 1 件でも通れば、次の失敗からまたやり直す。
        kensakusystem.request_text(None, "u", 10_000)
        self.assertEqual(kensakusystem._consecutive_exhausted_fetches, 0)


class TreeWalkRetryTest(unittest.TestCase):
    def test_slow_branch_is_not_counted_as_missed(self) -> None:
        context = kensakusystem.SeeContext(
            source_url="https://example.jp/",
            see_url="https://example.jp/cgi-bin3/See.exe?Code=abc",
            post_url="https://example.jp/cgi-bin3/r_ViewTree.exe",
            code="abc",
            root_html="<a class=\"js-tree-submit\" data-depth=\"平成29年 第5回定例会\"></a>",
        )
        outcomes: list = [TimeoutError("timed out"), "<html></html>"]

        def once(_opener, url, timeout_ms, **_kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, url

        walk: dict = {}
        with mock.patch.object(kensakusystem, "resolve_see_context", return_value=context), mock.patch.object(
            kensakusystem, "_request_text_once", once
        ), mock.patch.object(kensakusystem.time, "sleep"):
            kensakusystem._consecutive_exhausted_fetches = 0
            kensakusystem.discover_meeting_items(None, {"source_url": "https://example.jp/"}, 10_000, walk=walk)

        self.assertEqual(walk["missed_pages"], 0)


if __name__ == "__main__":
    unittest.main()
