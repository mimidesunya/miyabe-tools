"""索引側パーサの世代が古い自治体を見つける。

保存済みのファイルはそのままに、解釈だけを直すことがある。公布日の見出し判定や
文字化けの修復がそれで、直しても再索引しなければ公開検索は古いままになる。
再スクレイプは 30 日周期なので、放っておくと最大 30 日ずれる。

各文書に `parser_generation` を持たせてあるので、それが今の世代より古い自治体を
数えれば、再索引すべき自治体がわかる。取得はやり直さない。
"""

from __future__ import annotations

from typing import Any

# 1 回の掃き取りで積み直す自治体の数。全国を一度に積むと index キューが
# 通常の更新を通さなくなる。少しずつ流して、何周かで追いつかせる。
DEFAULT_SWEEP_LIMIT = 20

# 1 自治体あたり、この件数より少ない古い世代は無視する。
# 削除待ちの残骸が 1 件だけ残っている状態で毎回積み直すのを避ける。
MIN_STALE_DOCS = 1


def stale_query(doc_type: str, generation: int) -> dict[str, Any]:
    """世代が古い、または世代を持たない文書を選ぶ。"""
    return {
        "bool": {
            "filter": [{"term": {"doc_type": doc_type}}],
            "should": [
                {"bool": {"must_not": [{"exists": {"field": "parser_generation"}}]}},
                {"range": {"parser_generation": {"lt": int(generation)}}},
            ],
            "minimum_should_match": 1,
        }
    }


def stale_slugs(
    client: Any,
    index: str,
    doc_type: str,
    generation: int,
    *,
    limit: int = DEFAULT_SWEEP_LIMIT,
    min_docs: int = MIN_STALE_DOCS,
) -> list[tuple[str, int]]:
    """古い世代の文書を持つ自治体を、多い順に返す。

    返すのは `(slug, 古い文書の件数)` の並び。件数は再索引の要否を人が読むためで、
    積む順の根拠でもある。多い自治体から直す。
    """
    if int(limit) <= 0:
        return []
    body = {
        "size": 0,
        "query": stale_query(doc_type, generation),
        "aggs": {
            "slugs": {
                "terms": {
                    "field": "slug",
                    # 上位だけを見ると、件数の少ない自治体がいつまでも残る。
                    # limit より広く取ってから足切りする。
                    "size": max(int(limit) * 5, 50),
                }
            }
        },
    }
    response = client.request("POST", f"/{index}/_search", body=body)
    buckets = (response.get("aggregations") or {}).get("slugs", {}).get("buckets") or []
    found: list[tuple[str, int]] = []
    for bucket in buckets:
        slug = str(bucket.get("key") or "").strip()
        count = int(bucket.get("doc_count") or 0)
        if not slug or count < int(min_docs):
            continue
        found.append((slug, count))
    found.sort(key=lambda item: (-item[1], item[0]))
    return found[: int(limit)]


def generation_field_is_mapped(client: Any, index: str) -> bool:
    """索引の mapping に `parser_generation` があるかを返す。

    mapping は `dynamic: false` である。移行前の索引へ書くと、この項目は黙って
    捨てられる。捨てられた文書は「世代が無い＝古い」と見えるので、掃き取りが
    同じ自治体を永久に積み直す。積む前にここで確かめる。
    """
    try:
        response = client.request("GET", f"/{index}/_mapping")
    except Exception:
        return False
    for body in (response or {}).values():
        properties = ((body or {}).get("mappings") or {}).get("properties") or {}
        if "parser_generation" in properties:
            return True
    return False
