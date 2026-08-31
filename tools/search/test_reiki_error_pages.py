"""取得元のエラーページを例規として公開しない。

上里町は 17 件のうち 13 件の本文が「ご指定のページは見つかりませんでした」
だけだった。題名がちょうど「エラー」で本文が「その他 エラー」の文書も
全国で 17 件ある。条文は無いのに「例規が 17 件ある」ように見える。

**落としすぎない。**「市の花」のような短い本物や、本文が長い条例で
たまたま文言を含むものは残す。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_opensearch_index import looks_like_error_page  # noqa: E402


class ErrorPageTest(unittest.TestCase):
    def test_error_title_with_a_stub_body(self) -> None:
        self.assertTrue(looks_like_error_page("エラー", "その他 エラー"))

    def test_not_found_body(self) -> None:
        self.assertTrue(
            looks_like_error_page("上里町介護保険条例", "ご指定のページは見つかりませんでした")
        )

    def test_a_short_real_ordinance_is_kept(self) -> None:
        self.assertFalse(looks_like_error_page("町の花", "町の花はさくらとする。"))

    def test_a_long_body_is_kept_even_if_it_mentions_404(self) -> None:
        body = "第1条 この条例は…" + "あ" * 300 + "404"
        self.assertFalse(looks_like_error_page("個人情報保護条例", body))

    def test_an_ordinance_citing_article_404_is_kept(self) -> None:
        # `404` を単独のマーカーにすると、「地方税法（…）第404条」を引く
        # 固定資産評価員設置条例のような短い本物が落ちる。公開に 17 件あった。
        body = (
            "地方税法（昭和25年法律第226号）第404条第2項の規定により、"
            "固定資産評価員を置く。"
        )
        self.assertFalse(looks_like_error_page("八街市固定資産評価員設置条例", body))
        self.assertFalse(looks_like_error_page("勅令第404号の取扱いについて", body))

    def test_error_title_with_a_real_body_is_kept(self) -> None:
        # 題名がエラーでも、条文があるなら落とさない。
        body = "第1条 この条例は、" + "い" * 300
        self.assertFalse(looks_like_error_page("エラー", body))


if __name__ == "__main__":
    unittest.main()


class SakaiminatoErrorPageTest(unittest.TestCase):
    """境港市は「見つかりません」ではなく「存在しません」と書く。

    公開に 2 件、題名も本文も
    `お探しのページは存在しません - さかなと鬼太郎のまち境港市` だけの文書が
    例規として並んでいた。
    """

    def test_the_page_is_dropped(self):
        from build_opensearch_index import looks_like_error_page

        title = "お探しのページは存在しません - さかなと鬼太郎のまち境港市 Sakaiminato City Official Web Site"
        body = "その他 " + title
        self.assertTrue(looks_like_error_page(title, body))

    def test_a_real_ordinance_saying_the_words_is_kept(self):
        """長い本文なら、たまたま文言を含む条例とみなして落とさない。"""
        from build_opensearch_index import looks_like_error_page

        body = "第1条 " + "この条例は、お探しのページは存在しませんという文言を引用する。" * 10
        self.assertFalse(looks_like_error_page("○○条例", body))
