"""会期名の年で照合して、取得済み会議録の原典 URL と開催日を落とさない。

本文冒頭の年は「会期名の年」であって開催年ではない。令和3年2月の会議に
「令和2年第2回定例会」と書かれていることがある。そこだけを照合キーにすると
一覧行に当たらず、`source_url` も `held_on` も空になる。取得は成功している
ので件数では検知できない。本番の北海道・札幌市で 917 件がこの形だった。

保存先のディレクトリ名は一覧行の年ラベルから作るが、改行や空白が `_` に
置き換わる（`平成31年・令和元年` → `平成31年・_令和元年`）。ここも合わせる。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scraped_source_records import (  # noqa: E402
    build_minutes_record,
    canonical_year_label,
    parse_minutes_source_meta,
)


URL = "https://www.gikai.example.jp/cgi-bin/index.cgi?YEAR=2021&FINO=8619"


class MinutesSourceUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.downloads = self.root / "downloads"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_index(self, rows: list[dict]) -> dict:
        path = self.root / "meetings_index.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return parse_minutes_source_meta(path)

    def _record(self, year_dir: str, meeting_dir: str, name: str, body: str):
        target = self.downloads / year_dir / meeting_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return build_minutes_record(target, self.downloads, self._meta, "2026-08-31T00:00:00Z")

    def test_session_year_differs_from_held_year(self) -> None:
        # 保存先は令和3年。本文冒頭は「令和2年第2回定例会」。
        self._meta = self._write_index(
            [
                {
                    "title": "02月03日-01号",
                    "year_label": "令和3年",
                    "url": URL,
                    "meeting_name": "少子・高齢社会対策特別委員会会議録",
                    "source_year": 2021,
                }
            ]
        )
        record = self._record(
            "令和3年",
            "少子・高齢社会",
            "02月03日-01号.txt",
            "令和2年第2回定例会\n少子・高齢社会対策特別委員会会議録\n令和3年（2021年）2月3日（水曜日）\n開会\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, URL)
        self.assertEqual(record.held_on, "2021-02-03")

    def test_year_dir_sanitised_from_index_label(self) -> None:
        # 一覧行は `平成31年・\n令和元年`、保存先は `平成31年・_令和元年`。
        self._meta = self._write_index(
            [
                {
                    "title": "05月21日-01号",
                    "year_label": "平成31年・\n令和元年",
                    "url": URL,
                    "meeting_name": "少子・高齢社会対策特別委員会会議録",
                    "source_year": 2019,
                }
            ]
        )
        record = self._record(
            "平成31年・_令和元年",
            "少子・高齢社会",
            "05月21日-01号.txt",
            "第31回 令和元年第1回少子・高齢社会対策特別委員会会議録\n令和元年（2019年）5月21日（火曜日）\n開会\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, URL)

    def test_collision_suffix_is_not_part_of_the_title(self) -> None:
        # 同じ保存名がぶつかると保存側が SHA1 先頭 8 桁を足す。それは保存先を
        # 分けるための印で、会議の名前ではない。付いたまま照合すると当たらず、
        # 原典 URL も開催日も落ちる。本番で 10,696 件がこの形だった。
        self._meta = self._write_index(
            [
                {
                    "title": "03月11日-01号",
                    "year_label": "令和4年",
                    "url": URL,
                    "meeting_name": "予算特別委員会会議録",
                    "source_year": 2022,
                }
            ]
        )
        record = self._record(
            "令和4年",
            "予算決算,分科会",
            "03月11日-01号-2cbd47d6.txt",
            "令和４年（2022年）３月11日（金曜日）\n予算特別委員会会議録\n開議\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.title, "03月11日-01号")
        self.assertEqual(record.source_url, URL)

    def test_parenthesised_western_year_is_readable(self) -> None:
        # 取得元は「令和４年（2022年）３月11日」と併記することがある。
        # 「年」の直後に月を求めると、その括弧で切れて開催日が読めない。
        self._meta = self._write_index([])
        record = self._record(
            "令和4年",
            "予算特別",
            "03月11日-01号.txt",
            "予算特別委員会会議録\n令和４年（2022年）３月11日（金曜日）\n開議\n出席議員\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.held_on, "2022-03-11")

    def test_same_title_twice_does_not_share_one_url(self) -> None:
        # 同じ年・同じ題名の一覧行が複数あると、会議名なしの鍵は最初の 1 件へ
        # 固定される。飯塚市では 75 文書が同じ PDF を原典として指していた。
        # 本文から会議名が読めなくても、保存先の会議ディレクトリで選び直せる。
        other = "https://example.invalid/other.pdf"
        self._meta = self._write_index(
            [
                {
                    "title": "報告事項1",
                    "year_label": "令和8年",
                    "url": URL,
                    "meeting_name": "総務委員会",
                },
                {
                    "title": "報告事項1",
                    "year_label": "令和8年",
                    "url": other,
                    "meeting_name": "厚生委員会",
                },
            ]
        )
        record = self._record(
            "令和8年",
            "厚生委員会",
            "報告事項1.txt",
            "コミュニティ交通運行計画について" + "\n" + "資料" + "\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, other)

    def test_a_single_row_still_resolves_without_a_meeting_name(self) -> None:
        # 候補が一つしかないときは、従来どおり会議名なしで引ける。
        self._meta = self._write_index(
            [{"title": "報告事項1", "year_label": "令和8年", "url": URL, "meeting_name": "総務委員会"}]
        )
        record = self._record("令和8年", "別の名前", "報告事項1.txt", "資料" + "\n")
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, URL)

    def test_the_body_header_beats_the_catalog_row(self) -> None:
        # 一覧行との照合は題名と会議名の当てもので、同じ日の分科会や委員会が
        # 同じ題名だと 1 件の URL を全部へ配る（北海道の FINO=9077 が 5 会議、
        # 滋賀県の FINO=3434 が 6 会議）。本文の `出典:` は、その文書を落とした
        # ときに書いた 1 件ぶんの URL なので、そちらを正とする。
        own = "https://www.gikai.example.jp/cgi-bin/index.cgi?YEAR=2022&FINO=3436"
        self._meta = self._write_index(
            [
                {
                    "title": "04月28日-01号",
                    "year_label": "令和8年",
                    "url": URL,
                    "meeting_name": "4月招集会議会議録",
                    "source_year": 2021,
                }
            ]
        )
        record = self._record(
            "令和8年",
            "常任",
            "04月28日-01号-e8c4335d.txt",
            "観光文スポ・県土・交通常任委員会" + "\n" + "出典: " + own + "\n" + "開議" + "\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, own)
        # 一覧行から引き継いだ番号は別の会議のものなので、URL から読み直す。
        self.assertEqual(record.source_year, 2022)

    def test_the_catalog_row_is_used_when_the_body_has_no_header(self) -> None:
        self._meta = self._write_index(
            [{"title": "04月28日-01号", "year_label": "令和8年", "url": URL, "meeting_name": "4月招集会議会議録"}]
        )
        record = self._record("令和8年", "本会議", "04月28日-01号.txt", "4月招集会議会議録" + "\n" + "開議" + "\n")
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, URL)

    def test_comma_joined_meeting_directory_still_matches(self) -> None:
        # 同じ日に開かれた会議をまとめて「建設常任,建設協議,建設企業」という
        # 名前の保存先になることがある。そのままでは前方一致が 1 件も当たらず、
        # 八戸市では最大 9 文書が同じ原典 URL を指していた。
        other = "https://example.invalid/kensetsu.html"
        self._meta = self._write_index(
            [
                {"title": "09月10日-01号", "year_label": "令和8年", "url": URL, "meeting_name": "総務協議会"},
                {"title": "09月10日-01号", "year_label": "令和8年", "url": other, "meeting_name": "建設協議会"},
            ]
        )
        record = self._record(
            "令和8年",
            "建設常任,建設協議,建設企業",
            "09月10日-01号.txt",
            "所管事務調査について" + "\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.source_url, other)

    def test_a_date_far_from_the_year_label_is_not_the_meeting_date(self) -> None:
        # 本文が引用している別の年の日付を開催日にしていた。本番で
        # 「平成16年」の委員会記録に 1932-10-01 が入るなど 10 件あった。
        from scraped_source_records import extract_held_on

        body = (
            "宇都宮市議会厚生常任委員会" + "\n"
            + "昭和7年10月1日に制定された条例について" + "\n"
            + "平成16年3月16日（火曜日）" + "\n" + "開議" + "\n"
        )
        held_on, _, _, _ = extract_held_on(
            body, "厚生常任委員会", None, source_hint="x", year_label="平成16年"
        )
        self.assertEqual(held_on, "2004-03-16")

    def test_a_possible_year_label_is_not_collapsed(self) -> None:
        # `平成11年` を `平成1年` に畳んではいけない。元号の年は 2 桁までありうる。
        from scraped_source_records import collapse_repeated_year_label

        for label in ("平成11年", "平成22年", "令和33年", "昭和64年"):
            with self.subTest(label=label):
                self.assertEqual(collapse_repeated_year_label(label), label)
        # 実在しない桁数のときだけ畳む。
        self.assertEqual(collapse_repeated_year_label("平成28282828年"), "平成28年")

    def test_the_source_url_is_a_date_hint(self) -> None:
        # 原典 URL にも開催日が入っている（`fileName=R070220A` は 2025-02-20）。
        # 保存パスだけを渡していたので、kensakusystem の 16,312 件が
        # 開催日を持てなかった。取れているのに使っていなかった。
        url = "https://ssp.kaigiroku.net/tenant/izumo/View.html?fileName=R070220A"
        self._meta = self._write_index(
            [
                {
                    "title": "令和 6年度第6回定例会（第2号 2月20日）",
                    "year_label": "令和6年",
                    "url": url,
                    "meeting_name": "定例会",
                }
            ]
        )
        record = self._record(
            "令和6年",
            "定例会",
            "令和 6年度第6回定例会（第2号 2月20日）.txt",
            "出雲市議会定例会" + "\n" + "開議" + "\n",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.held_on, "2025-02-20")

    def test_canonical_year_label_drops_separators(self) -> None:
        self.assertEqual(canonical_year_label("平成31年・_令和元年"), "平成31年・令和元年")
        self.assertEqual(canonical_year_label("平成31年・\n令和元年"), "平成31年・令和元年")


if __name__ == "__main__":
    unittest.main()
