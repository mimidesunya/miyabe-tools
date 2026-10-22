"""取得元がセッションを捨てたあとの、入り直しと続きからの取得。

白浜町・平戸市・大阪市（2026-10）は、本文 1 件を開いたところで取得元が
「Reiki-Base Error 処理エラーが発生しました」を返し、そのあとの検索が
軒並み時間切れになって取り切れなかった。取得元は処理エラーのときに
セッションを捨てて新しい JSESSIONID を渡す。メイン画面に残った検索画面は
古いセッションのもので、検索や「次へ」の要求は http:// の入口へ転送され、
mixed content として止められる（白浜町で確かめた）。

後半は Playwright を起動せず、取得元を真似た偽の画面で run() を通す。
"""

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "reiki" / "scrapers"), str(ROOT / "tools" / "reiki")]

import legal_square  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402

# 本番の WARN に出ていた処理エラー画面の文面。
ERROR_PAGE_TEXT = (
    "Reiki-Base Error 処理エラーが発生しました お手数ですが、再度アクセスし直してください。 "
    "ご迷惑をお掛けし、申し訳ございませんがよろしくお願いいたします。 閉じる"
)
HOST = "https://krr324.legal-square.com/HAS-Shohin/page/"


class SourceErrorPageTest(unittest.TestCase):
    def test_error_page_body_id(self) -> None:
        self.assertTrue(legal_square.is_source_error_page("ERROR", ""))

    def test_error_page_text(self) -> None:
        self.assertTrue(legal_square.is_source_error_page("", ERROR_PAGE_TEXT))

    def test_viewer_is_not_an_error(self) -> None:
        self.assertFalse(
            legal_square.is_source_error_page("", "白浜町立保育園条例 第1条 この条例は、…")
        )
        self.assertFalse(legal_square.is_source_error_page("", ""))


class SessionLostRequestTest(unittest.TestCase):
    def test_search_redirected_to_the_login_page(self) -> None:
        # セッションが切れた画面で検索すると、入口へ http:// で転送される。
        url = "http://krr324.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf"
        self.assertTrue(legal_square.is_session_lost_request("xhr", url))
        self.assertTrue(legal_square.is_session_lost_request("fetch", url + "?x=1"))

    def test_opening_the_login_page_is_not_a_loss(self) -> None:
        # 入口を開くのと、入口からの自動遷移は画面遷移（document）。
        self.assertFalse(legal_square.is_session_lost_request("document", HOST + "SJSrbLogin.jsf"))

    def test_ordinary_search_requests(self) -> None:
        self.assertFalse(legal_square.is_session_lost_request("xhr", HOST + "SJReikiList.jsf"))
        self.assertFalse(
            legal_square.is_session_lost_request(
                "xhr", "https://krr324.legal-square.com/HAS-Shohin/ers/js/?files=SJSrbLogin.jsf"
            )
        )


class BrokenAtSourceTest(unittest.TestCase):
    def test_the_first_error_is_never_the_documents_fault(self) -> None:
        # 白浜町立保育園条例は本番で処理エラーになったが、新しいセッションでは開けた。
        self.assertFalse(legal_square.is_broken_at_source(0, False))
        self.assertFalse(legal_square.is_broken_at_source(0, True))

    def test_second_error_after_other_bodies_is_not_judged(self) -> None:
        self.assertFalse(legal_square.is_broken_at_source(1, False))

    def test_second_error_right_after_logging_in_again(self) -> None:
        self.assertTrue(legal_square.is_broken_at_source(1, True))


class MissingAtSourceShareTest(unittest.TestCase):
    def test_none(self) -> None:
        self.assertFalse(legal_square.missing_at_source_exceeded(0, 0))

    def test_a_few_in_a_large_source(self) -> None:
        self.assertFalse(legal_square.missing_at_source_exceeded(1, 1350))
        limit = legal_square.MAX_MISSING_AT_SOURCE
        self.assertFalse(legal_square.missing_at_source_exceeded(limit, 10000))
        self.assertTrue(legal_square.missing_at_source_exceeded(limit + 1, 10000))

    def test_a_large_share_of_a_small_source(self) -> None:
        self.assertTrue(legal_square.missing_at_source_exceeded(2, 5))


# ---- 取得元を真似た偽の画面 ----

PAGE_SIZE = 10
# 1 回の検索で返す件数の上限（CAP_HINTS にある値）。
SEARCH_CAP = 100
LOGIN_URL = HOST + "SJSrbLogin.jsf"
ERA_BASE = {"明治": 1868, "大正": 1912, "昭和": 1926, "平成": 1989, "令和": 2019}


class FakeRequest:
    def __init__(self, resource_type: str, url: str) -> None:
        self.resource_type = resource_type
        self.url = url


def seireki(era_date) -> tuple[int, int, int]:
    era, year, month, day = era_date
    return ERA_BASE[era] + int(year) - 1, int(month), int(day)


class FakeSource:
    """取得元。always_broken の本文は毎回、break_once の本文は最初の 1 回だけ
    処理エラーを返し、そのセッションを捨てる。expire_on_next は、その番号の
    ページ（1 始まり）で「次へ」を押したときに、expire_on_search は、入ってから
    数えてその回の検索で、1 度だけセッションを切らす。

    spread_dates なら制定年月日を平成2〜30年にばらし、件数が上限を超えると
    期間で割って取る形になる。そうでなければ全件が同じ日。
    """

    def __init__(
        self,
        count: int,
        *,
        always_broken=(),
        break_once=(),
        expire_on_next=0,
        expire_on_search=0,
        spread_dates=False,
    ) -> None:
        self.rows = []
        for n in range(1, count + 1):
            if spread_dates:
                date = (1990 + n % 29, 1 + n % 12, 1 + n % 28)
            else:
                date = (2025, 2, 26)
            wareki = (
                f"令和{date[0] - 2018}年{date[1]}月{date[2]}日"
                if date[0] >= 2020
                else f"平成{date[0] - 1988}年{date[1]}月{date[2]}日"
            )
            self.rows.append(
                {
                    "title": f"テスト条例{n}",
                    "date": date,
                    "cells": [str(n), f"テスト条例{n}", wareki, f"条例第{n}号", ""],
                }
            )
        self.always_broken = set(always_broken)
        self.break_once = set(break_once)
        self.expire_on_next = expire_on_next
        self.expire_on_search = expire_on_search
        self.searches = 0
        self.logins = 0
        self.opened: list[str] = []

    def search(self, payload) -> list[dict]:
        rows = self.rows
        if payload and payload.get("from") and payload.get("to"):
            low, high = seireki(payload["from"]), seireki(payload["to"])
            rows = [row for row in rows if low <= row["date"] <= high]
        for word, negate in (payload or {}).get("words") or []:
            rows = [row for row in rows if (word in row["title"]) != bool(negate)]
        return rows


class FakePopup:
    def __init__(self, title: str, error: bool) -> None:
        self.title = title
        self.error = error
        self.url = HOST + ("OpenEditor.jsf?appl-id=SJ0010" if error else "../jsp/SVDocumentView")

    def wait_for_load_state(self, *args, **kwargs) -> None:
        pass

    def wait_for_selector(self, *args, **kwargs) -> None:
        pass

    def wait_for_timeout(self, *args, **kwargs) -> None:
        pass

    def eval_on_selector_all(self, selector, script):
        return [] if self.error else [f"<p>{self.title}の本文</p>"]

    def query_selector(self, selector):
        return object() if self.error and selector == "body#ERROR" else None

    def evaluate(self, script, arg=None):
        if self.error:
            return {"id": "ERROR", "text": ERROR_PAGE_TEXT}
        return {"id": "", "text": self.title}

    def inner_text(self, selector) -> str:
        return ERROR_PAGE_TEXT if self.error else self.title

    def close(self) -> None:
        pass


class FakeAnchor:
    def __init__(self, page, row: dict) -> None:
        self.page = page
        self.row = row

    def click(self) -> None:
        source = self.page.source
        title = self.row["title"]
        source.opened.append(title)
        error = not self.page.alive
        if title in source.always_broken or title in source.break_once:
            source.break_once.discard(title)
            error = True
        if error:
            # 取得元はこの時点でセッションを捨てる。
            self.page.alive = False
        self.page.popup = FakePopup(title, error)


class FakeNext:
    def __init__(self, page) -> None:
        self.page = page

    def get_attribute(self, name):
        return ""

    def click(self) -> None:
        page = self.page
        if page.source.expire_on_next == page.index + 1:
            page.source.expire_on_next = 0
            page.alive = False
        if not page.alive:
            page.redirect_to_login()
            return
        page.index += 1


class FakePopupInfo:
    def __init__(self, page) -> None:
        self.page = page

    @property
    def value(self):
        return self.page.popup


class FakePage:
    def __init__(self, source: FakeSource) -> None:
        self.source = source
        self.alive = False
        self.index = 0
        self.handlers = []
        self.popup = None
        # いま表示中の検索結果（上限で切る前）と、次の検索の条件。
        self.results = list(source.rows)
        self.pending = None
        self.stamped = False

    # 取得元の振る舞い
    def redirect_to_login(self) -> None:
        # 古いセッションの要求は、入口へ http:// で転送されて止められる。
        for handler in self.handlers:
            handler(FakeRequest("xhr", LOGIN_URL.replace("https://", "http://")))

    def shown(self) -> list[dict]:
        return self.results[:SEARCH_CAP]

    def visible_rows(self) -> list[dict]:
        start = self.index * PAGE_SIZE
        return self.shown()[start:start + PAGE_SIZE]

    def pager_text(self) -> str:
        total = len(self.shown())
        if total == 0:
            return "0件"
        start = self.index * PAGE_SIZE
        return f"{start + 1}～{min(total, start + PAGE_SIZE)}件目/{total}件"

    # Playwright の Page と同じ名前の操作
    def set_default_timeout(self, ms) -> None:
        pass

    def on(self, event, handler) -> None:
        if event == "request":
            self.handlers.append(handler)

    def goto(self, url, **kwargs) -> None:
        self.source.logins += 1
        self.alive = True
        self.index = 0
        self.pending = None

    def click(self, selector, **kwargs) -> None:
        source = self.source
        if selector == "#searchDetail" and self.alive:
            source.searches += 1
            if source.expire_on_search and source.searches == source.expire_on_search:
                self.alive = False
        if not self.alive:
            self.redirect_to_login()
            return
        if selector == "#searchDetail":
            self.results = source.search(self.pending)
            self.index = 0
            self.stamped = False

    def wait_for_timeout(self, ms) -> None:
        pass

    def wait_for_selector(self, selector, **kwargs) -> None:
        pass

    def wait_for_function(self, script, arg=None, timeout=None) -> None:
        if script == legal_square.FRESH_EVAL:
            if self.stamped:
                raise PlaywrightTimeoutError("検索結果が差し替わらない")
            return
        if arg is not None and self.pager_text() == arg:
            raise PlaywrightTimeoutError("次のページが出ない")

    def query_selector(self, selector):
        if "次へ" in selector:
            return FakeNext(self)
        return None

    def query_selector_all(self, selector):
        return [FakeAnchor(self, row) for row in self.visible_rows()]

    def evaluate(self, script, arg=None):
        if script == legal_square.ROW_EVAL:
            return [{"title": row["title"], "cells": list(row["cells"])} for row in self.visible_rows()]
        if script == legal_square.TOTAL_EVAL:
            return self.pager_text()
        if script == legal_square.KIND_EVAL:
            return []
        if script == legal_square.STAMP_EVAL:
            self.stamped = True
            return True
        if script == legal_square.FILTER_EVAL:
            self.pending = arg
            return None
        if script == legal_square.DATE_ERROR_EVAL:
            return False
        return None

    def expect_popup(self, **kwargs):
        return contextlib.nullcontext(FakePopupInfo(self))


class FakeContext:
    def __init__(self, source: FakeSource) -> None:
        self.source = source

    def set_default_timeout(self, ms) -> None:
        pass

    def new_page(self) -> FakePage:
        return FakePage(self.source)

    def close(self) -> None:
        pass


class FakeBrowser:
    def __init__(self, source: FakeSource) -> None:
        self.source = source

    def new_context(self, **kwargs) -> FakeContext:
        return FakeContext(self.source)

    def close(self) -> None:
        pass


class FakePlaywright:
    def __init__(self, source: FakeSource) -> None:
        self.chromium = mock.Mock()
        self.chromium.launch.return_value = FakeBrowser(source)


def run_fake(source: FakeSource, work: Path, *, check_updates: bool = False) -> dict:
    target = {
        "name": "テスト町",
        "slug": "99999-test",
        "system_type": "legal-square",
        "source_url": LOGIN_URL,
        "source_dir": str(work / "source"),
        "html_dir": str(work / "html"),
        "markdown_dir": str(work / "markdown"),
        "work_root": str(work),
    }
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(
                legal_square, "sync_playwright", lambda: contextlib.nullcontext(FakePlaywright(source))
            )
        )
        stack.enter_context(
            mock.patch.object(legal_square.reiki_targets, "load_reiki_target", lambda *a, **k: target)
        )
        stack.enter_context(mock.patch.object(legal_square, "SESSION_RENEW_WAIT_SECONDS", 0))
        stack.enter_context(mock.patch.object(legal_square.time, "sleep", lambda seconds: None))
        log = stack.enter_context(open(work / "log.txt", "a", encoding="utf-8"))
        stack.enter_context(contextlib.redirect_stdout(log))
        legal_square.run(
            "99999-test",
            "legal-square",
            force=False,
            check_updates=check_updates,
            limit=0,
            headful=False,
            timeout_ms=1000,
        )
    coverage = legal_square.reiki_io.load_source_coverage(work)
    manifest_path = legal_square.reiki_io.existing_path(work / "source_manifest.json.gz")
    manifest = json.loads(legal_square.reiki_io.read_text_auto(manifest_path))
    return {"coverage": coverage, "manifest": manifest}


class SessionRenewalRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_one_error_is_recovered_by_logging_in_again(self) -> None:
        # 本番の白浜町と同じ形。本文 1 件で処理エラー、その本文も入り直せば開ける。
        source = FakeSource(25, break_once={"テスト条例12"})
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["failed"], 0)
        self.assertEqual(coverage["missing_at_source"], 0)
        self.assertEqual(coverage["session_renewals"], 1)
        self.assertEqual(len(result["manifest"]), 25)
        self.assertEqual(source.logins, 2)
        # 取り込み済みの本文は開き直さない。処理エラーの本文だけ 2 回開く。
        self.assertEqual(source.opened.count("テスト条例12"), 2)
        self.assertEqual(len(source.opened), 26)

    def test_a_body_that_never_opens_is_missing_at_source(self) -> None:
        source = FakeSource(25, always_broken={"テスト条例12"})
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["failed"], 0)
        self.assertEqual(coverage["missing_at_source"], 1)
        self.assertEqual(coverage["missing_at_source_examples"], ["テスト条例12"])
        self.assertFalse(coverage["missing_share_exceeded"])
        self.assertEqual(coverage["session_renewals"], 2)
        self.assertEqual(len(result["manifest"]), 24)
        self.assertEqual(source.opened.count("テスト条例12"), 2)
        # そのあとの本文も取れている。
        self.assertIn("テスト条例25", [row["title"] for row in result["manifest"]])

    def test_a_body_that_never_opens_keeps_the_previous_copy(self) -> None:
        source = FakeSource(25, always_broken={"テスト条例12"})
        first = run_fake(FakeSource(25), self.work)
        self.assertEqual(len(first["manifest"]), 25)
        # 毎日の巡回は --check-updates で本文を取り直す。開けなくても前回の本文は残す。
        result = run_fake(source, self.work, check_updates=True)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["missing_at_source"], 1)
        self.assertEqual(len(result["manifest"]), 25)

    def test_session_lost_on_next_page_is_resumed(self) -> None:
        # 「次へ」の要求が入口へ戻されたら、入り直して続きのページを歩く。
        source = FakeSource(25, expire_on_next=2)
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["session_renewals"], 1)
        self.assertEqual(coverage["unresolved"], [])
        self.assertEqual(len(result["manifest"]), 25)
        self.assertEqual(len(source.opened), 25)

    def test_session_lost_on_a_search_is_resumed(self) -> None:
        # 本番の大阪市と同じ形。上限に張り付いて期間で割る途中で、検索の要求が
        # 入口へ戻される。以前は同じ画面で聞き直し続け、差し替わらないまま
        # 20 回数えて走査ごと中断していた。
        source = FakeSource(150, spread_dates=True, expire_on_search=3)
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertEqual(coverage["cap_value"], SEARCH_CAP)
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["unresolved"], [])
        self.assertEqual(coverage["session_renewals"], 1)
        self.assertEqual(len(result["manifest"]), 150)
        self.assertEqual(len(source.opened), 150)

    def test_a_split_walk_without_errors_does_not_log_in_again(self) -> None:
        # 取得元が健全なら、入り直しは起きない（ほかの 108 自治体の走り方を変えない）。
        source = FakeSource(150, spread_dates=True)
        result = run_fake(source, self.work)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["session_renewals"], 0)
        self.assertEqual(source.logins, 1)
        self.assertEqual(len(result["manifest"]), 150)

    def test_many_broken_bodies_do_not_complete_the_walk(self) -> None:
        # まとまって開けないなら、セッションの壊れ方を見誤っている。完了にしない。
        broken = {"テスト条例3", "テスト条例4"}
        source = FakeSource(8, always_broken=broken)
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertEqual(coverage["missing_at_source"], 2)
        self.assertEqual(coverage["unresolved"], [])
        self.assertTrue(coverage["missing_share_exceeded"])
        self.assertFalse(coverage["complete"])


if __name__ == "__main__":
    unittest.main()
