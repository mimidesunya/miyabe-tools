import unittest

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


if __name__ == "__main__":
    unittest.main()
