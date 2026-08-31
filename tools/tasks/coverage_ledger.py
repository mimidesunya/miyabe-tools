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


def write_ledger(sections: list[dict[str, Any]]) -> Path:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": LEDGER_VERSION,
        "updated_at": batch_status.now_text(),
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
