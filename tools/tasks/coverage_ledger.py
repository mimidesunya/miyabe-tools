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


# 題名の月日と開催日が食い違う割合が、これを超えたら疑う。
# 同じ自治体でまとまって出るなら、解釈が古いか誤っている。
DATE_MISMATCH_RATIO = 0.1
# 題名に月日を持つ文書がこれより少ないと、割合が当てにならない。
DATE_MISMATCH_MIN_SAMPLES = 20


def date_mismatch_rows(
    samples_by_slug: dict[str, tuple[int, int]],
    *,
    ratio: float = DATE_MISMATCH_RATIO,
    min_samples: int = DATE_MISMATCH_MIN_SAMPLES,
) -> list[dict[str, Any]]:
    """題名の月日と開催日が食い違う自治体を返す。

    件数だけを見ていると、**空欄ではなく「もっともらしく誤る」**形が
    見えない。若桜町は題名 `6月17日` に対し開催日が 6月16日（招集日）で、
    公開件数は正しく、日付だけが 1 日ずれていた。

    `samples_by_slug` は `slug -> (題名に月日がある件数, 食い違う件数)`。
    """
    found: list[dict[str, Any]] = []
    for slug, (checked, mismatched) in samples_by_slug.items():
        if int(checked) < int(min_samples):
            continue
        if int(mismatched) < int(checked) * float(ratio):
            continue
        found.append(
            {
                "slug": slug,
                "checked": int(checked),
                "mismatched": int(mismatched),
                "ratio": round(mismatched / checked, 3) if checked else 0.0,
            }
        )
    found.sort(key=lambda row: -row["ratio"])
    return found


# 本文がこの長さ未満なら、中身がほぼ無い。
EMPTY_BODY_LENGTH = 60
# その自治体の文書のうち、これを超える割合が空同然なら疑う。
EMPTY_BODY_RATIO = 0.5
# 少数の自治体で偶然そうなることはあるので、下限を置く。
EMPTY_BODY_MIN_DOCS = 30


def empty_body_rows(
    counts_by_slug: dict[str, int],
    empty_by_slug: dict[str, int],
    *,
    ratio: float = EMPTY_BODY_RATIO,
    min_docs: int = EMPTY_BODY_MIN_DOCS,
) -> list[dict[str, Any]]:
    """本文がほとんど空の自治体を返す。

    **件数では出てこない不具合である。**公開はされていて、中身だけが無い。
    牛久市 1,001 件・福岡市 1,136 件は、題名と日付は読めるのに条文が
    1 件も入っていなかった。新しい Reiki-Base の本文構造を読めていなかった。
    """
    found: list[dict[str, Any]] = []
    for slug, total in counts_by_slug.items():
        total = int(total or 0)
        if total < int(min_docs):
            continue
        empty = int(empty_by_slug.get(slug, 0))
        if empty < total * float(ratio):
            continue
        found.append(
            {
                "slug": slug,
                "documents": total,
                "empty_body": empty,
                "ratio": round(empty / total, 3) if total else 0.0,
            }
        )
    found.sort(key=lambda row: (-row["ratio"], -row["documents"]))
    return found


# 本文の長さが同系統の中央値のこの割合を下回ったら疑う。
SHORT_BODY_RATIO = 0.35
# 標本が少ないと中央値が当てにならない。
SHORT_BODY_MIN_DOCS = 30
# 比べる相手の数。これを下回る系統は中央値を作らない。
SHORT_BODY_MIN_PEERS = 3
# 本文の中央値がこれを下回るものは、分割の細かさでは説明できない。
# 会議録 1 件が 1,000 字を切ることは、枠だけ・見出しだけを掴んでいる
# のでなければ起きない。
#
# 仲間比だけで見ると 145 自治体が挙がるが、その多くは `独自` の PDF
# 収集で、議案ごと・日ごとに細かく分かれているだけだった（松田町
# 「議案第58号」1,774 字の中身は議長の発言から始まる議事そのもの）。
# **短いことと欠けていることは違う。**取り直しを指示する相手は、
# この床を下回るものに限る。
SHORT_BODY_SEVERE_MEDIAN = 1000


def short_body_rows(
    median_by_slug: dict[str, int],
    system_by_slug: dict[str, str],
    counts_by_slug: dict[str, int],
    *,
    ratio: float = SHORT_BODY_RATIO,
    min_docs: int = SHORT_BODY_MIN_DOCS,
    min_peers: int = SHORT_BODY_MIN_PEERS,
) -> list[dict[str, Any]]:
    """本文が同系統の仲間より極端に短い自治体を返す。

    **空ではないので empty_body に出ず、件数は正しいので thin にも出ない。**
    高崎市は本文の中央値が 2,855 字で、同じ gijiroku.com の仲間は
    13,000〜35,000 字だった。取得していたのは開いている発言だけで、
    全発言ではなかった。件数も題名も日付も正しかった。
    """
    from statistics import median

    peers: dict[str, list[int]] = {}
    for slug, value in median_by_slug.items():
        if int(counts_by_slug.get(slug, 0)) < int(min_docs):
            continue
        system = str(system_by_slug.get(slug) or "").strip()
        if not system:
            continue
        peers.setdefault(system, []).append(int(value))

    found: list[dict[str, Any]] = []
    for slug, value in median_by_slug.items():
        total = int(counts_by_slug.get(slug, 0))
        if total < int(min_docs):
            continue
        system = str(system_by_slug.get(slug) or "").strip()
        values = peers.get(system) or []
        if len(values) < int(min_peers):
            continue
        peer_median = median(values)
        if peer_median <= 0:
            continue
        if int(value) >= peer_median * float(ratio):
            continue
        found.append(
            {
                "slug": slug,
                "system_type": system,
                "documents": total,
                "median_body": int(value),
                "peer_median": int(peer_median),
                "ratio": round(int(value) / peer_median, 3),
                # 分割の細かさでは説明できない短さかどうか。
                "severe": int(value) < SHORT_BODY_SEVERE_MEDIAN,
            }
        )
    found.sort(key=lambda row: (row["ratio"], -row["documents"]))
    return found


# 日付が読めない文書がこの割合を超えたら疑う。
EMPTY_DATE_RATIO = 0.5
# 少数の自治体で偶然そうなることはあるので、下限を置く。
EMPTY_DATE_MIN_DOCS = 30


def empty_date_rows(
    counts_by_slug: dict[str, int],
    dated_by_slug: dict[str, int],
    *,
    ratio: float = EMPTY_DATE_RATIO,
    min_docs: int = EMPTY_DATE_MIN_DOCS,
) -> list[dict[str, Any]]:
    """日付がほとんど読めていない自治体を返す。

    **件数でも本文でも出てこない。**板柳町は 503 件が公開され、本文も
    入っていて、そのうち 502 件に公布日が無かった。日付で絞れないので、
    利用者からは「無い」のと変わらない。
    """
    found: list[dict[str, Any]] = []
    for slug, total in counts_by_slug.items():
        total = int(total or 0)
        if total < int(min_docs):
            continue
        missing = total - int(dated_by_slug.get(slug, 0))
        if missing < total * float(ratio):
            continue
        found.append(
            {
                "slug": slug,
                "documents": total,
                "no_date": missing,
                "ratio": round(missing / total, 3) if total else 0.0,
            }
        )
    found.sort(key=lambda row: (-row["ratio"], -row["documents"]))
    return found


# 最新文書がこれより古い自治体は、取得が止まっている疑いがある。
STALE_DAYS = 730
# 議会が長期休止している小規模自治体もあるので、件数の下限は置かない。
# 代わりに、経過日数そのものを添えて人が判断できるようにする。


# 日付が読めている文書がこの割合を下回る自治体では、「最新の日付」は
# 取得の古さではなく日付の欠落を映している。板柳町は 503 件のうち
# 502 件に公布日が無く、残る 1 件の 1961 年が最新として出ていた。
STALE_MIN_DATED_RATIO = 0.5


def stale_rows(
    newest_by_slug: dict[str, str],
    *,
    today: str,
    days: int = STALE_DAYS,
    dated_by_slug: dict[str, int] | None = None,
    totals_by_slug: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """最新文書が古いまま止まっている自治体を返す。

    **件数でも空欄でも日付ずれでも出てこない形である。**仙台市は
    88 件が正しく公開され、日付も本文も正しかった。ただし最新が
    1991 年だった。取得元は 19,325 件を申告していて、ページ送りが
    18 ページ目で静かに終わっていた。既存の 6 つの軸はどれも
    これを指さなかった。

    `newest_by_slug` は `slug -> 最新文書の日付 (YYYY-MM-DD)`。
    """
    from datetime import date

    def parse(text: str):
        try:
            year, month, day = (int(part) for part in str(text)[:10].split("-"))
            return date(year, month, day)
        except (TypeError, ValueError):
            return None

    limit = parse(today)
    if limit is None:
        return []
    found: list[dict[str, Any]] = []
    for slug, newest in newest_by_slug.items():
        seen = parse(newest)
        if seen is None:
            continue
        if dated_by_slug is not None and totals_by_slug is not None:
            total = int(totals_by_slug.get(slug, 0))
            dated = int(dated_by_slug.get(slug, 0))
            if total > 0 and dated < total * STALE_MIN_DATED_RATIO:
                # 日付がほとんど読めていない。古さではなく日付の問題なので、
                # empty_date の軸で挙げる。両方に出すと、どちらの数も濁る。
                continue
        age = (limit - seen).days
        if age < int(days):
            continue
        found.append({"slug": slug, "newest": str(newest)[:10], "age_days": age})
    found.sort(key=lambda row: -row["age_days"])
    return found


# 保存済みのうち、これを下回る割合しか公開されていなければ疑う。
INDEX_GAP_RATIO = 0.5
# 少数の自治体では、重複除去のずれだけでこの比になる。
INDEX_GAP_MIN_SAVED = 30


def index_gap_rows(
    saved_by_slug: dict[str, int],
    indexed_by_slug: dict[str, int],
    *,
    ratio: float = INDEX_GAP_RATIO,
    min_saved: int = INDEX_GAP_MIN_SAVED,
) -> list[dict[str, Any]]:
    """取得済みなのに公開へ出ていない自治体を返す。

    **仲間と比べるのではなく、自分の保存データと比べる。**これが一番強い
    根拠である。仲間比（`thin`）は取得元の規模差で濁るが、こちらは
    「こちらが持っているのに出していない」という事実そのものになる。

    `sweep_never_indexed` は 1 件も無い自治体しか拾わない。各務原市は
    3,220 件を保存して 5 件しか公開しておらず、0 件ではないのでどの
    仕組みからも見えなかった。氷見市は 711 件保存して 4 件で、索引を
    積み直しただけで 541 件になった。**取得の問題ではなく、公開の問題。**
    """
    found: list[dict[str, Any]] = []
    for slug, saved in saved_by_slug.items():
        saved = int(saved or 0)
        if saved < int(min_saved):
            continue
        indexed = int(indexed_by_slug.get(slug, 0))
        if indexed >= saved * float(ratio):
            continue
        found.append(
            {
                "slug": slug,
                "saved": saved,
                "indexed": indexed,
                "gap": saved - indexed,
                "ratio": round(indexed / saved, 3) if saved else 0.0,
            }
        )
    found.sort(key=lambda row: -row["gap"])
    return found


def severe_short_body_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """短さが分割では説明できない自治体だけを返す。取り直しの対象。"""
    return [row for row in rows if row.get("severe")]


def classify(slug: str, *, published: bool, saved_files: int, excluded: bool) -> str:
    """その自治体が公開に出ていない理由を返す。出ているなら空。"""
    if published:
        return ""
    if excluded:
        return "excluded"
    if int(saved_files) > 0:
        return "not_indexed"
    return "no_saved_files"


# 取得元をまだ登録できていない自治体。台帳の分母から外すと、端から端までの
# 数え上げから消える。codex の指摘。
#
# > 台帳の対象は自治体マスタではなく `iter_*_targets()` である。URL が空の
# > 行は落ちるので、「取得元をまだ登録できていない自治体」は端から端までの
# > 台帳に入らない。
#
# 実測でマスタ 1,794 件に対し、会議録は 282 件・例規は 47 件が分母の外に
# あった。全国の 16% が「数えていないので健全」に見えていた。
UNREGISTERED = "source_unknown"


def build_section(
    doc_type: str,
    targets: Iterable[dict[str, Any]],
    published_slugs: set[str],
    count_saved: Callable[[set[str]], dict[str, int]],
    *,
    master_codes: set[str] | None = None,
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
    # 取得元を登録できていない自治体。台帳の分母に入れる。数えなければ
    # 「問題が無い」ではなく「見ていない」である。
    unregistered: list[str] = []
    if master_codes:
        known = {slug.split("-", 1)[0] for slug in slugs}
        unregistered = sorted(master_codes - known)
        for code in unregistered:
            reasons[UNREGISTERED] = reasons.get(UNREGISTERED, 0) + 1
            missing.append(
                {
                    "slug": code,
                    "name": code,
                    "system_type": "",
                    "reason": UNREGISTERED,
                    "saved_files": 0,
                }
            )
    missing.sort(key=lambda row: (row["reason"], -int(row["saved_files"]), row["slug"]))
    total = len(slugs) + len(unregistered)
    return {
        "doc_type": doc_type,
        "targets": total,
        "configured": len(slugs),
        "published": total - len(missing),
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
    # mkstemp は 0600。公開画面が読む台帳なので、読める権限にして置き換える。
    try:
        os.chmod(temporary, 0o644)
    except OSError:
        pass
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
