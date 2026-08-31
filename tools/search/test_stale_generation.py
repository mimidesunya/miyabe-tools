"""世代が古い自治体の拾い方。

索引側の解釈を直しても、再スクレイプまで公開検索は古いままだった。群馬県の
空公布日 710 件がその形で、現行コードなら読めるのに 30 日眠っていた。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stale_generation import stale_query, stale_slugs  # noqa: E402


class FakeClient:
    def __init__(self, buckets):
        self.buckets = buckets
        self.last_body = None

    def request(self, method, path, *, body=None, **kwargs):
        self.last_body = body
        return {"aggregations": {"slugs": {"buckets": self.buckets}}}


class StaleGenerationTest(unittest.TestCase):
    def test_missing_generation_counts_as_stale(self):
        """世代を持たない文書は、世代を付ける前に索引したものである。"""
        query = stale_query("reiki", 3)
        shoulds = query["bool"]["should"]
        self.assertIn({"bool": {"must_not": [{"exists": {"field": "parser_generation"}}]}}, shoulds)
        self.assertIn({"range": {"parser_generation": {"lt": 3}}}, shoulds)
        self.assertEqual(query["bool"]["filter"], [{"term": {"doc_type": "reiki"}}])

    def test_most_stale_first(self):
        client = FakeClient([
            {"key": "13101-chiyoda-ku", "doc_count": 12},
            {"key": "10000-gunma-ken", "doc_count": 710},
            {"key": "32203-izumo-shi", "doc_count": 40},
        ])
        self.assertEqual(
            stale_slugs(client, "miyabe-reiki-current", "reiki", 2, limit=2),
            [("10000-gunma-ken", 710), ("32203-izumo-shi", 40)],
        )

    def test_limit_zero_asks_nothing(self):
        """上限 0 なら照会もしない。掃き取りを止めるときに使う。"""
        client = FakeClient([{"key": "x", "doc_count": 1}])
        self.assertEqual(stale_slugs(client, "i", "reiki", 2, limit=0), [])
        self.assertIsNone(client.last_body)

    def test_below_minimum_is_left_alone(self):
        client = FakeClient([{"key": "a", "doc_count": 1}, {"key": "b", "doc_count": 5}])
        self.assertEqual(
            stale_slugs(client, "i", "reiki", 2, limit=10, min_docs=2),
            [("b", 5)],
        )

    def test_asks_wider_than_the_limit(self):
        """上位だけ見ると、件数の少ない自治体がいつまでも残る。"""
        client = FakeClient([])
        stale_slugs(client, "i", "reiki", 2, limit=3)
        self.assertGreaterEqual(client.last_body["aggs"]["slugs"]["terms"]["size"], 50)


class MappingGuardTest(unittest.TestCase):
    """mapping が `dynamic: false` なので、移行前に積むと永久ループになる。"""

    class MappingClient:
        def __init__(self, properties, raises=False):
            self.properties = properties
            self.raises = raises

        def request(self, method, path, **kwargs):
            if self.raises:
                raise RuntimeError("index missing")
            return {"miyabe-reiki-000001": {"mappings": {"properties": self.properties}}}

    def test_present(self):
        from stale_generation import generation_field_is_mapped

        client = self.MappingClient({"parser_generation": {"type": "integer"}})
        self.assertTrue(generation_field_is_mapped(client, "miyabe-reiki-current"))

    def test_absent(self):
        from stale_generation import generation_field_is_mapped

        self.assertFalse(
            generation_field_is_mapped(self.MappingClient({"title": {}}), "miyabe-reiki-current")
        )

    def test_unreadable_index_is_treated_as_unmigrated(self):
        from stale_generation import generation_field_is_mapped

        self.assertFalse(
            generation_field_is_mapped(self.MappingClient({}, raises=True), "x")
        )
