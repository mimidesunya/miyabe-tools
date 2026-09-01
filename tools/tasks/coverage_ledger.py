"""取りこぼしの台帳。**公開に 1 件も出ていない自治体を数える。**

工程ごとの成功は既に記録している。取得が成功したか、索引が成功したか。
足りないのは端から端までの答えで、「この自治体は公開検索に出ているか」を
誰も見ていなかった。

そのせいで長く残った例:

- 能代市。取得元の作りが変わって例規 0 件になり、失敗として記録され続けて
  いた。**失敗は記録されていたが、誰も見ていなかった。**
- 鳥栖市。取得は成功、索引は走らないまま。どの経路にも引っかからない。
- 全国で 105 自治体が、会議録または例規を 1 件も公開していなかった。

台帳は原因まで分ける。原因ごとに打つ手が違う。

- `not_indexed`: 保存はある。索引に無い。再索引を積む
- `no_saved_files`: 保存が無い。取得側の問題。取得元を見る
- `excluded`: 台帳で対象外。何もしない
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from . import status as batch_status


LEDGER_VERSION = 1


def ledger_path() -> Path:
    return batch_status.status_root() / "coverage_ledger.json"


# 同じ系統の仲間と比べて、これを下回るなら少なすぎるとみなす。
# 0 件でなくても取りこぼしは起きる。富士市は 1,666 件あるところを 14 件しか
# 公開していなかったが、台帳では健全に見えていた。
THIN_RATIO = 0.1
# 仲間が少ないと中央値が当てにならない。この数を下回る系統は見ない。
THIN_MIN_PEERS = 8


def thin_slugs(
    counts_by_slug: dict[str, int],
    system_by_slug: dict[str, str],
    *,
    ratio: float = THIN_RATIO,
    min_peers: int = THIN_MIN_PEERS,
) -> list[dict[str, Any]]:
    """同じ系統の中央値と比べて極端に少ない自治体を返す。

    「0 件かどうか」だけでは取りこぼしを捕まえられない。取得元の作りが
    少し変われば、全部ではなく**一部だけ**取れなくなる。富士市は年度を
    144 件辿りながら直近の 14 件しか拾えていなかった。
    """
    import statistics

    by_system: dict[str, list[tuple[str, int]]] = {}
    for slug, count in counts_by_slug.items():
        if int(count) <= 0:
            continue
        by_system.setdefault(system_by_slug.get(slug, "?"), []).append((slug, int(count)))
    found: list[dict[str, Any]] = []
    for system, rows in by_system.items():
        if len(rows) < int(min_peers):
            continue
        median = statistics.median(sorted(count for _, count in rows))
        threshold = median * float(ratio)
        for slug, count in rows:
            if count < threshold:
                found.append(
                    {
                        "slug": slug,
                        "system_type": system,
                        "documents": count,
                        "peer_median": int(median),
                    }
                )
    found.sort(key=lambda row: (row["system_type"], row["documents"]))
    return found


# 取得元の申告母数に対して、これを下回るなら取り切れていない。
# 索引は会議録でないものを落とすので、申告どおりの数にはならない。
DECLARED_RATIO = 0.5


def declared_shortfall(
    declared_by_slug: dict[str, int],
    counts_by_slug: dict[str, int],
    *,
    ratio: float = DECLARED_RATIO,
) -> list[dict[str, Any]]:
    """取得元が申告した母数に対して、公開が明らかに足りない自治体を返す。

    **これが本来の指標である。**仲間の中央値と比べるのは、母数が読めない
    取得元のための最後の網でしかない。codex と grok が揃って指摘した。

    富士市は一覧が 1,761 件と申告しているのに 14 件しか公開していなかった。
    各務原市は 1,607 件に対して 23 件。どちらも取得元が数を出しているのに、
    こちらが比べていなかった。
    """
    found: list[dict[str, Any]] = []
    for slug, declared in declared_by_slug.items():
        declared = int(declared or 0)
        if declared <= 0:
            continue
        published = int(counts_by_slug.get(slug, 0))
        if published >= declared * float(ratio):
            continue
        found.append(
            {
                "slug": slug,
                "declared": declared,
                "published": published,
                "ratio": round(published / declared, 3) if declared else 0.0,
            }
        )
    found.sort(key=lambda row: (row["ratio"], -row["declared"]))
    return found


def classify(slug: str, *, published: bool, saved_files: int, excluded: bool) -> str:
    """その自治体が公開に出ていない理由を返す。出ているなら空。"""
    if published:
        return ""
    if excluded:
        return "excluded"
    if int(saved_files) > 0:
        return "not_indexed"
    return "no_saved_files"


def build_section(
    doc_type: str,
    targets: Iterable[dict[str, Any]],
    published_slugs: set[str],
    count_saved: Callable[[set[str]], dict[str, int]],
) -> dict[str, Any]:
    """1 系統ぶんの台帳を組み立てる。

    保存ファイルの数は、公開に出ていない自治体のぶんだけ数える。全国を毎回
    歩くと 100 万件超のファイルを触ることになる。
    """
    missing: list[dict[str, Any]] = []
    slugs: list[str] = []
    excluded_by_slug: dict[str, bool] = {}
    system_by_slug: dict[str, str] = {}
    name_by_slug: dict[str, str] = {}
    for target in targets:
        slug = str(target.get("slug") or "").strip()
        if not slug:
            continue
        slugs.append(slug)
        excluded_by_slug[slug] = str(target.get("crawl_status") or "").strip() not in ("", "enabled")
        system_by_slug[slug] = str(target.get("system_type") or "")
        name_by_slug[slug] = str(target.get("name") or slug)

    unpublished = {slug for slug in slugs if slug not in published_slugs}
    saved_counts = count_saved(unpublished) if unpublished else {}

    reasons: dict[str, int] = {}
    for slug in slugs:
        saved = int(saved_counts.get(slug, 0))
        reason = classify(
            slug,
            published=slug in published_slugs,
            saved_files=saved,
            excluded=excluded_by_slug.get(slug, False),
        )
        if reason == "":
            continue
        reasons[reason] = reasons.get(reason, 0) + 1
        missing.append(
            {
                "slug": slug,
                "name": name_by_slug.get(slug, slug),
                "system_type": system_by_slug.get(slug, ""),
                "reason": reason,
                "saved_files": saved,
            }
        )
    missing.sort(key=lambda row: (row["reason"], -int(row["saved_files"]), row["slug"]))
    return {
        "doc_type": doc_type,
        "targets": len(slugs),
        "published": len(slugs) - len(missing),
        "missing": len(missing),
        "reasons": reasons,
        "missing_rows": missing,
    }


# 台帳そのものの状態。**「異常 0 件」と「異常を数えられなかった」を
# 同じ値にしない。**これは今日直した不具合と同じ形が、監視の側へ移ったもので、
# codex と grok の両方が最優先の指摘として挙げた。
MEASUREMENT_COMPLETE = "complete"
MEASUREMENT_PARTIAL = "partial"
MEASUREMENT_FAILED = "failed"

# 台帳に必ず入るべき区分。欠けたまま書けば、欠けた区分は「異常 0」に見える。
REQUIRED_DOC_TYPES = ("minutes", "reiki")


def measurement_status(sections: list[dict[str, Any]]) -> str:
    """台帳をどこまで数えられたかを返す。"""
    present = {str(section.get("doc_type") or "") for section in sections}
    missing = [name for name in REQUIRED_DOC_TYPES if name not in present]
    if len(missing) == len(REQUIRED_DOC_TYPES):
        return MEASUREMENT_FAILED
    if missing:
        return MEASUREMENT_PARTIAL
    if any(section.get("errors") for section in sections):
        return MEASUREMENT_PARTIAL
    return MEASUREMENT_COMPLETE


def write_ledger(sections: list[dict[str, Any]]) -> Path:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    status = measurement_status(sections)
    payload = {
        "version": LEDGER_VERSION,
        "updated_at": batch_status.now_text(),
        # 数え切れなかったことを、異常 0 件と読ませない。
        "measurement_status": status,
        "required_doc_types": list(REQUIRED_DOC_TYPES),
        "measured_doc_types": [str(s.get("doc_type") or "") for s in sections],
        "errors": [error for section in sections for error in (section.get("errors") or [])],
        "sections": sections,
    }
    # 書いている途中で落ちても、読む側が壊れた JSON を読まないようにする。
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def read_ledger() -> dict[str, Any]:
    try:
        loaded = json.loads(ledger_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}
