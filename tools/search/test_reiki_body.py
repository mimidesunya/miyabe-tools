"""例規の本文に AI 評価を混ぜない。混ぜると自治体の見解と読み違える。

2026-08-31 に、評価を `evaluation_text` へ分ける修正が変数の代入を欠いたまま
入り、`iter_reiki_documents()` が最初の 1 件で NameError を出していた。
例規の索引更新は本番で全自治体・全実行が失敗していた（celery ログで確認）。
単体テストは 29 件とも通っていたが、この繰り返し子を実データ 1 件で通す
テストが無かったので誰も気づけなかった。ここでそれを通す。
"""

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reiki"))

import build_opensearch_index as indexer  # noqa: E402
import reiki_targets  # noqa: E402


ORIGINAL_BODY = "市制記念日は、7月1日とする。"
AI_REASON = "小さな政府の観点からも、実害のない形式的な規定として維持を容認できる。"
AI_COMBINED_REASON = "象徴的・儀礼的な制定であり、行政コストの増大を伴わない。"
AI_STANCE = "適合"


class ReikiBodyIsSourceOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for name in ("html", "markdown", "classification"):
            (root / name).mkdir()
        (root / "html" / "1001.html").write_text(
            '<html><body><div class="law-date">昭和12年6月25日</div>'
            f"<h1>市制記念日</h1><p>{ORIGINAL_BODY}</p></body></html>",
            encoding="utf-8",
        )
        (root / "classification" / "1001.json").write_text(
            json.dumps(
                {
                    "title": "市制記念日",
                    "number": "昭和12年6月25日告示第163号",
                    "documentType": "告示",
                    "responsibleDepartment": "総務課",
                    "reason": AI_REASON,
                    "necessityScore": -1,
                    "primaryClass": "G",
                    "lensEvaluation": {"combined": {"stance": AI_STANCE, "reason": AI_COMBINED_REASON}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with gzip.open(root / "source_manifest.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(
                [{"key": "1001.html", "title": "市制記念日", "source_url": "https://example.invalid/1001.html"}],
                handle,
                ensure_ascii=False,
            )
        self._target = {
            "slug": "14130-kawasaki-shi",
            "code": "14130",
            "name": "川崎市",
            "system_type": "example",
            "work_root": str(root),
            "source_dir": str(root / "html"),
            "html_dir": str(root / "html"),
            "markdown_dir": str(root / "markdown"),
            "classification_dir": str(root / "classification"),
        }
        self._saved = reiki_targets.iter_reiki_targets
        reiki_targets.iter_reiki_targets = lambda *a, **kw: iter([self._target])

    def tearDown(self) -> None:
        reiki_targets.iter_reiki_targets = self._saved
        self._tmp.cleanup()

    def _only_document(self) -> dict:
        documents = list(indexer.iter_reiki_documents(strict=True))
        self.assertEqual(len(documents), 1, "実データ 1 件を通せていない")
        return documents[0][1]

    def test_iterator_runs(self) -> None:
        # NameError で最初の 1 件が落ちる回帰は、ここで必ず止まる。
        self._only_document()

    def test_body_has_no_ai_evaluation(self) -> None:
        body = self._only_document().get("body") or ""
        self.assertIn(ORIGINAL_BODY, body)
        for leaked in (AI_REASON, AI_COMBINED_REASON, AI_STANCE, "総務課"):
            self.assertNotIn(leaked, body, f"AI 評価・所管課が本文に混ざっている: {leaked}")

    def test_evaluation_text_keeps_the_evaluation(self) -> None:
        evaluation = self._only_document().get("evaluation_text") or ""
        for kept in (AI_STANCE, AI_COMBINED_REASON, AI_REASON):
            self.assertIn(kept, evaluation)

    def test_ordinary_abbreviations_survive(self) -> None:
        # CamelCase を一律に消したところ、評価文から `DX` `IoT` `RPA` `WebAPI` まで
        # 消え、「DX推進の基盤となる規則」が「 推進の基盤となる規則」になった。
        # 落とすのは AI 評価が使う項目名だけにする。
        import build_opensearch_index as indexer

        text = "DX推進等による効率化。IoT推進とRPA導入。WebAPIを公開する。"
        self.assertEqual(indexer.drop_internal_identifiers(text), text)

    def test_identifiers_glued_to_japanese_are_dropped(self) -> None:
        # 評価文は `necessityScoreを-1とし、Class Gに分類する` のように、
        # 識別子と日本語が地続きで書かれる。空白で切ってトークンごとに見ると
        # 日本語がくっついた分が残り、公開検索に 13 件戻っていた。
        import build_opensearch_index as indexer

        cleaned = indexer.drop_internal_identifiers(
            "necessityScoreを-1とし、Class Gに分類する。小さな政府の観点から維持できる。"
        )
        self.assertNotIn("necessityScore", cleaned)
        self.assertNotIn("Class G", cleaned)
        self.assertIn("小さな政府", cleaned)

    def test_internal_identifiers_are_not_search_terms(self) -> None:
        # `necessityScore` は AI 評価の内部の項目名で、利用者が探す語ではない。
        # 検索語に残していたので、本文にその語が無い条例が 13 件ヒットしていた。
        terms = self._only_document().get("body_terms") or ""
        for identifier in ("necessityScore", "fiscalImpactScore", "primaryClass"):
            self.assertNotIn(identifier, terms)

    def test_evaluation_stays_searchable(self) -> None:
        # 本文から外しても検索語としては残す。従来の検索が効かなくならないように。
        terms = self._only_document().get("body_terms") or ""
        self.assertTrue(terms.strip(), "body_terms が空になっている")


if __name__ == "__main__":
    unittest.main()
