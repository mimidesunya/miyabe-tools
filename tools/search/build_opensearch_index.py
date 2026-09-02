#!/usr/bin/env python3
"""スクレイパ成果物から Miyabe の OpenSearch index を構築する。

公開検索 API は OpenSearch だけを読むため、このコマンドが保存済み成果物と
公開 alias の橋渡しになる。rebuild は新しい versioned index を作り、
update は現在の alias 配下で指定自治体の文書だけを差し替える。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parents[1] / "gijiroku"))
sys.path.append(str(Path(__file__).resolve().parents[1] / "reiki"))
sys.path.append(str(Path(__file__).resolve().parents[2] / "lib" / "python"))
# このコマンドはローカルでも Docker でも子プロセスからファイルパス指定で実行される。
# PYTHONPATH の事前設定に頼らず、必要な scraper 補助モジュールを import できるようにする。

import gijiroku_targets  # type: ignore
import reiki_targets  # type: ignore
import build_locks  # type: ignore
from opensearch_mappings import build_index_body
from parser_generation import PARSER_GENERATION  # type: ignore
from scraped_source_records import (  # type: ignore
    SOURCE_URL_HEADER_PATTERN,
    build_alias_map,
    build_minutes_record,
    build_reiki_record,
    choose_minutes_source_files,
    collect_reiki_preferred_files,
    load_reiki_manifest_index,
    parse_minutes_source_meta,
    reiki_sortable_prefixes,
)

try:
    import japanese_search_tokenizer  # type: ignore
except Exception:  # pragma: no cover - 最小構成の環境では tokenizer なしでも動かす
    japanese_search_tokenizer = None


PREFECTURE_NAMES = {
    "01": "北海道",
    "02": "青森県",
    "03": "岩手県",
    "04": "宮城県",
    "05": "秋田県",
    "06": "山形県",
    "07": "福島県",
    "08": "茨城県",
    "09": "栃木県",
    "10": "群馬県",
    "11": "埼玉県",
    "12": "千葉県",
    "13": "東京都",
    "14": "神奈川県",
    "15": "新潟県",
    "16": "富山県",
    "17": "石川県",
    "18": "福井県",
    "19": "山梨県",
    "20": "長野県",
    "21": "岐阜県",
    "22": "静岡県",
    "23": "愛知県",
    "24": "三重県",
    "25": "滋賀県",
    "26": "京都府",
    "27": "大阪府",
    "28": "兵庫県",
    "29": "奈良県",
    "30": "和歌山県",
    "31": "鳥取県",
    "32": "島根県",
    "33": "岡山県",
    "34": "広島県",
    "35": "山口県",
    "36": "徳島県",
    "37": "香川県",
    "38": "愛媛県",
    "39": "高知県",
    "40": "福岡県",
    "41": "佐賀県",
    "42": "長崎県",
    "43": "熊本県",
    "44": "大分県",
    "45": "宮崎県",
    "46": "鹿児島県",
    "47": "沖縄県",
}


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$")


# OpenSearch への素の HTTP クライアントは opensearch_client.py へ分離した。
from opensearch_client import OpenSearchClient, OpenSearchRequestError  # type: ignore  # noqa: E402

# rebuild の進捗 state 書き込み（UI 補助）は rebuild_status.py へ分離した。
from rebuild_status import (  # type: ignore  # noqa: E402
    search_rebuild_status_finish,
    search_rebuild_status_progress,
    search_rebuild_status_slug_published,
    search_rebuild_status_start,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or incrementally update OpenSearch indexes from scraper-produced source files."
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "rebuild", "update", "resume"],
        default="auto",
        help=(
            "auto は --slug 指定時だけ増分更新し、それ以外は versioned rebuild します。"
            " update は current alias に slug 単位で delete+bulk します。"
            " resume は中断した rebuild を --resume-index の途中状態から再開します。"
        ),
    )
    parser.add_argument("--doc-type", choices=["all", "minutes", "reiki"], default="all")
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="増分更新または部分 rebuild 対象の自治体 slug。カンマ区切り・複数指定可。",
    )
    parser.add_argument("--build-id", default="", help="Index build id. Defaults to a UTC timestamp.")
    parser.add_argument(
        "--resume-index",
        default="",
        help="--mode resume で続きを書き込む構築途中の index 名。部分公開 alias の filter から完了済み自治体を読み取って飛ばす。",
    )
    parser.add_argument("--opensearch-url", default=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"))
    parser.add_argument("--opensearch-user", default=os.environ.get("OPENSEARCH_USER", ""))
    parser.add_argument("--opensearch-password", default=os.environ.get("OPENSEARCH_PASSWORD", ""))
    parser.add_argument(
        "--insecure-dev",
        action="store_true",
        default=os.environ.get("OPENSEARCH_INSECURE_DEV", "").lower() in {"1", "true", "yes", "on"},
        help="Disable TLS verification for local HTTPS OpenSearch endpoints.",
    )
    parser.add_argument("--documents-alias", default=os.environ.get("MIYABE_SEARCH_ALIAS", "miyabe-documents-current"))
    parser.add_argument("--minutes-alias", default=os.environ.get("MIYABE_MINUTES_ALIAS", "miyabe-minutes-current"))
    parser.add_argument("--reiki-alias", default=os.environ.get("MIYABE_REIKI_ALIAS", "miyabe-reiki-current"))
    parser.add_argument("--shards", type=int, default=int(os.environ.get("MIYABE_OPENSEARCH_SHARDS", "1")))
    parser.add_argument("--replicas", type=int, default=int(os.environ.get("MIYABE_OPENSEARCH_REPLICAS", "0")))
    parser.add_argument(
        "--bulk-size",
        type=int,
        default=int(os.environ.get("MIYABE_OPENSEARCH_BULK_SIZE", "200")),
        help="1 回の _bulk に載せる最大 document 数",
    )
    parser.add_argument(
        "--bulk-bytes",
        type=int,
        default=int(os.environ.get("MIYABE_OPENSEARCH_BULK_BYTES", str(8 * 1024 * 1024))),
        help="1 回の _bulk に載せる最大ペイロードバイト数（本文が大きい文書での上限）",
    )
    parser.add_argument(
        "--bulk-concurrency",
        type=int,
        default=int(os.environ.get("MIYABE_OPENSEARCH_BULK_CONCURRENCY", "2")),
        help="同時にインフライトさせる _bulk リクエスト数。文書の解析と索引付けを重ねる。",
    )
    parser.add_argument("--limit", type=int, default=0, help="Development limit per document type.")
    parser.add_argument("--no-switch-alias", action="store_true")
    parser.add_argument(
        "--allow-partial-alias",
        action="store_true",
        help=(
            "一部の自治体だけ、または --limit で切って再構築した索引を、"
            "それでも公開の alias に切り替える。既定では拒む（残りの自治体が"
            "検索から消えるため）。"
        ),
    )
    parser.add_argument(
        "--allow-empty-slug-delete",
        action="store_true",
        help=(
            "--mode update で文書を 1 件も生成できなかった slug の旧文書も削除する。"
            "取得ディレクトリ欠落と原典での全廃を自動では区別できないため、既定では"
            "旧文書を残す。"
        ),
    )
    return parser.parse_args()


# 列挙に失敗して索引から丸ごと落ちた自治体。strict でない全量 rebuild では
# 警告して先へ進むので、公開に切り替える前にここを見る。
SKIPPED_SOURCES: list[str] = []


@dataclass
class SourceIntegrityAudit:
    """候補ファイルを、yield・意図的除外・説明不能dropへ排他的に分ける。"""

    doc_type: str
    slug: str
    outcomes: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)


SOURCE_INTEGRITY_AUDITS: dict[tuple[str, str], SourceIntegrityAudit] = {}
INTENTIONAL_SOURCE_OUTCOMES = frozenset({"toc", "aux", "duplicate_body", "limit"})


def reset_source_integrity_tracking() -> None:
    """同じプロセスで main やテストを繰り返しても、前回のdropを持ち越さない。"""
    SKIPPED_SOURCES.clear()
    SOURCE_INTEGRITY_AUDITS.clear()


def start_source_integrity_audit(
    doc_type: str, slug: str, source_paths: Iterable[Path]
) -> SourceIntegrityAudit:
    audit = SourceIntegrityAudit(
        doc_type=doc_type,
        slug=slug,
        outcomes={str(path): "pending" for path in source_paths},
    )
    SOURCE_INTEGRITY_AUDITS[(doc_type, slug)] = audit
    return audit


def record_source_integrity_outcome(
    doc_type: str,
    slug: str,
    source_path: Path,
    outcome: str,
    *,
    reason: str = "",
) -> None:
    key = (doc_type, slug)
    audit = SOURCE_INTEGRITY_AUDITS.get(key)
    if audit is None:
        audit = start_source_integrity_audit(doc_type, slug, [source_path])
    path = str(source_path)
    audit.outcomes.setdefault(path, "pending")
    audit.outcomes[path] = outcome
    if reason:
        audit.reasons[path] = reason


def mark_pending_sources_as_limited(doc_type: str, slug: str) -> None:
    audit = SOURCE_INTEGRITY_AUDITS.get((doc_type, slug))
    if audit is None:
        return
    for path, outcome in list(audit.outcomes.items()):
        if outcome == "pending":
            audit.outcomes[path] = "limit"


def source_integrity_failures(
    doc_type: str | None = None, slug: str | None = None
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for (audit_doc_type, audit_slug), audit in sorted(SOURCE_INTEGRITY_AUDITS.items()):
        if doc_type is not None and audit_doc_type != doc_type:
            continue
        if slug is not None and audit_slug != slug:
            continue
        for path, outcome in sorted(audit.outcomes.items()):
            if outcome == "yielded" or outcome in INTENTIONAL_SOURCE_OUTCOMES:
                continue
            failures.append(
                {
                    "doc_type": audit_doc_type,
                    "slug": audit_slug,
                    "path": path,
                    "reason": audit.reasons.get(path) or outcome,
                }
            )
    return failures


def source_integrity_summary(doc_type: str, slug: str) -> dict[str, int]:
    audit = SOURCE_INTEGRITY_AUDITS.get((doc_type, slug))
    if audit is None:
        return {
            "raw_total": 0,
            "yielded": 0,
            "intentional_excluded": 0,
            "expected_indexable": 0,
            "unexplained_drop": 0,
        }
    counts = Counter(audit.outcomes.values())
    intentional = sum(counts.get(kind, 0) for kind in INTENTIONAL_SOURCE_OUTCOMES)
    yielded = counts.get("yielded", 0)
    raw_total = len(audit.outcomes)
    return {
        "raw_total": raw_total,
        "yielded": yielded,
        "intentional_excluded": intentional,
        # toc/aux/重複など説明できる除外だけを引く。残差は、読めていれば
        # 索引対象だった可能性があるため、公開前に説明不能dropとして止める。
        "expected_indexable": raw_total - intentional,
        "unexplained_drop": len(source_integrity_failures(doc_type, slug)),
    }


def source_slug_can_be_published(
    doc_type: str, slug: str, *, allow_partial_alias: bool = False
) -> bool:
    return allow_partial_alias or not source_integrity_failures(doc_type, slug)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_build_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def parse_slug_filter(values: list[str]) -> set[str]:
    slugs: set[str] = set()
    for value in values:
        for item in str(value or "").split(","):
            slug = item.strip()
            if slug:
                slugs.add(slug)
    return slugs


def normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if DATE_RE.match(text) else None


def plausible_meeting_date(value: Any) -> str | None:
    """会議録の開催日として取り得る日付だけを返す。

    まだ開かれていない会議の会議録は無い。未来の日付は、和暦の読み違い
    （年度を年と取る）か、本文中の別の日付（期限や施行日）を拾った結果で
    ある。実際に 20 件あり、横須賀市の 2025 年 12 月開催が 2026 年 12 月に
    なっていた。日付が信用できないなら、日付を持たせない方がよい。
    """
    text = normalize_date(value)
    if text is None:
        return None
    try:
        # 形だけ合っていても 13 月は日付ではない。
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    # 1 日ぶんは時差と取得元の表記ゆれの余地として許す。
    limit = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    if text > limit:
        return None
    return text


def normalize_datetime(value: Any) -> str | None:
    text = str(value or "").strip()
    if text == "":
        return None
    return text if DATETIME_RE.match(text) else None


def terms_text(value: str) -> str:
    if value == "":
        return ""
    if japanese_search_tokenizer is not None:
        try:
            return str(japanese_search_tokenizer.document_terms_text(value)).strip()
        except Exception:
            pass
    parts = re.split(r"[\s\u3000]+", value)
    return " ".join(part for part in parts if part)


def pref_code_from_code(code: str) -> str:
    code = str(code or "").strip()
    return code[:2] if re.match(r"^\d{2}", code) else ""


def target_metadata(target: dict[str, Any]) -> dict[str, str]:
    code = str(target.get("code") or "").strip()
    pref_code = pref_code_from_code(code)
    return {
        "slug": str(target.get("slug") or "").strip(),
        "municipality_code": code,
        "pref_code": pref_code,
        "pref_name": PREFECTURE_NAMES.get(pref_code, ""),
        "municipality_name": str(target.get("name") or "").strip(),
    }


def stable_local_id(*parts: str) -> str:
    material = "\0".join(str(part) for part in parts)
    return hashlib.sha1(material.encode("utf-8", errors="replace")).hexdigest()


# 内部の識別子（`necessityScore`、`fiscalImpactScore`、`Class` など）は
# 利用者が探す語ではない。検索語から外す。日本語は残す。
# 識別子は日本語と地続きで書かれる（`necessityScoreを-1とし、Class Gに分類する`）。
# 空白で切ってトークンごとに見ると、日本語がくっついた分が残る。文字列として除く。
#
# **CamelCase を一律に消してはいけない。**一度そうしたところ、評価文から
# `DX` `IoT` `RPA` `WebAPI` まで消え、「DX推進の基盤となる規則」が
# 「 推進の基盤となる規則」になった。落とすのは、AI 評価が使う項目名だけにする。
INTERNAL_IDENTIFIERS = (
    "necessityScore",
    "fiscalImpactScore",
    "regulatoryBurdenScore",
    "policyEffectivenessScore",
    "primaryClass",
    "secondaryTags",
    "lensTags",
    "lensEvaluation",
    "combinedStance",
    "analyzedAt",
    "documentType",
    "responsibleDepartment",
    "readingKana",
)
_INTERNAL_IDENTIFIER_RE = re.compile(
    "(?:" + "|".join(re.escape(name) for name in INTERNAL_IDENTIFIERS) + ")",
    re.IGNORECASE,
)
# `Class G` は分類の記号で、利用者が探す語ではない。
_CLASS_LABEL_RE = re.compile(r"(?<![A-Za-z])Class[ 　]*[A-G](?![A-Za-z])")


def drop_internal_identifiers(terms: str) -> str:
    text = _INTERNAL_IDENTIFIER_RE.sub(" ", str(terms or ""))
    text = _CLASS_LABEL_RE.sub(" ", text)
    return " ".join(text.split())


# 取得元が返したエラーページは条例ではない。題名と本文の両方で見る。
ERROR_PAGE_TITLES = {"エラー", "error", "Error", "ページが見つかりません"}
# **`404` や `Not Found` を単独のマーカーにしてはいけない。**一度そうしたところ、
# 「地方税法（…）第404条」を引く固定資産評価員設置条例のような短い本物が
# 落ちる形になった（公開に 17 件。八街市 154 字、栗東市 161 字、佐賀市 166 字）。
# 落とすのは、取得元が返したと分かる文面だけにする。
ERROR_PAGE_BODY_MARKERS = (
    "ご指定のページは見つかりませんでした",
    "指定されたページは見つかりません",
    "ページが見つかりませんでした",
    "お探しのページは見つかりません",
    # 境港市は「見つかりません」ではなく「存在しません」と書く。
    "お探しのページは存在しません",
    "ページが存在しません",
)
# 本文がこの長さを超えるなら、たまたま文言を含む条例とみなして落とさない。
ERROR_PAGE_BODY_MAX_LENGTH = 200


def looks_like_error_page(title: str, content_text: str) -> bool:
    body = " ".join(str(content_text or "").split())
    stripped_title = str(title or "").strip()
    if stripped_title in ERROR_PAGE_TITLES and len(body) <= ERROR_PAGE_BODY_MAX_LENGTH:
        return True
    if len(body) > ERROR_PAGE_BODY_MAX_LENGTH:
        return False
    return any(marker in body for marker in ERROR_PAGE_BODY_MARKERS)


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def first_date(value: Any) -> str | None:
    text = clean_text(value)
    if len(text) >= 10:
        return normalize_date(text[:10])
    return None


def preferred_reiki_sidecar(files: dict[str, Path], key: str) -> Path | None:
    return files.get(key) or files.get(Path(key).name)


DOCUMENT_KINDS_FILENAME = "document_kinds.json"


def write_document_kind_counts(
    target: dict[str, Any],
    kinds: dict[str, int],
    counted_at: str,
    *,
    raw_total: int,
    indexable_before_dedupe: int,
    deduplicated: int,
    yielded: int,
) -> None:
    """取得したファイルの種別内訳を取得元ごとに残す。

    保存件数と検索できる件数の差が、目次しか公開されていないためなのか、
    検索への反映がまだなのかを収集状況ページで区別するために使う。
    """
    work_dir = Path(str(target.get("work_dir") or ""))
    if not work_dir.is_dir():
        return
    exclusive_total = sum(kinds.values())
    duplicate_count = int(kinds.get("duplicate_body", 0))
    if exclusive_total != raw_total:
        raise RuntimeError(
            f"document kind counts are not exhaustive: raw={raw_total} kinds={exclusive_total}"
        )
    if int(kinds.get("minutes", 0)) != yielded:
        raise RuntimeError(
            f"document kind yielded count mismatch: minutes={kinds.get('minutes', 0)} yielded={yielded}"
        )
    if indexable_before_dedupe != deduplicated + duplicate_count:
        raise RuntimeError(
            "document kind deduplication count mismatch: "
            f"before={indexable_before_dedupe} after={deduplicated} duplicates={duplicate_count}"
        )
    if deduplicated != yielded:
        raise RuntimeError(
            f"document kind yield count mismatch: deduplicated={deduplicated} yielded={yielded}"
        )
    payload = {
        "version": 2,
        "counted_at": counted_at,
        "raw_total": raw_total,
        "indexable_before_dedupe": indexable_before_dedupe,
        "deduplicated": deduplicated,
        "yielded": yielded,
        # v1 の読み手にも正しい値を返す互換名。重複除去前の値へは戻さない。
        "total": raw_total,
        "indexable": yielded,
        "kinds": dict(sorted(kinds.items())),
    }
    path = work_dir / DOCUMENT_KINDS_FILENAME
    try:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except Exception as exc:
        print(f"[WARN] failed to write {path}: {exc}", file=sys.stderr)


# 保存ファイルの頭には、年ラベル・題名に続けて `出典: <URL>` が入る。
# 取得元の住所であって文書の中身ではない。同じ文書かどうかの判定から外す。
def body_without_source_header(text: str) -> str:
    return "\n".join(
        line
        for line in str(text or "").split("\n")
        if not SOURCE_URL_HEADER_PATTERN.search(line)
    )


def iter_minutes_documents(
    limit: int = 0,
    slugs: set[str] | None = None,
    *,
    strict: bool = False,
    exclude_slugs: set[str] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    indexed_at = utc_now_iso()
    emitted = 0
    slug_filter = slugs or set()
    skip_slugs = exclude_slugs or set()
    for target in gijiroku_targets.iter_gijiroku_targets():
        target_slug = str(target.get("slug") or "").strip()
        if slug_filter and target_slug not in slug_filter:
            continue
        if target_slug in skip_slugs:
            continue
        downloads_dir = Path(target["downloads_dir"])
        # 保存ディレクトリが見えないのは「文書が無い」ではない。取得側の
        # データを共有していないワーカーで走らせると、全自治体が 0 件に
        # なり、しかも成功として終わる。例規ワーカーで会議録を索引したときに
        # 実際に起きた。**見えないことを 0 件にしない。**
        if not downloads_dir.exists() and slugs and target_slug in slugs:
            raise RuntimeError(
                f"保存ディレクトリが見当たりません: {downloads_dir}。"
                "この worker から取得データが見えていない可能性があります。"
            )
        if not downloads_dir.is_dir():
            continue
        meta = target_metadata(target)
        assembly_name = str(target.get("assembly_name") or (meta["municipality_name"] + "議会")).strip()
        source_system = str(target.get("system_family") or target.get("system_type") or "").strip()
        try:
            source_files = choose_minutes_source_files(downloads_dir)
        except Exception as exc:
            if strict:
                raise RuntimeError(f"failed to enumerate minutes files dir={downloads_dir}: {exc}") from exc
            print(f"[WARN] failed to enumerate minutes files dir={downloads_dir}: {exc}", file=sys.stderr)
            # 自治体がまるごと索引から落ちる。このまま公開に切り替えると、
            # その自治体は検索できなくなる。
            SKIPPED_SOURCES.append(f"会議録 {meta['slug']}: {exc}")
            continue
        start_source_integrity_audit("minutes", target_slug, source_files)
        try:
            # 壊れた一覧JSONを空一覧と同一視すると、本文は載っても原典URLだけ
            # 失われる。非strict rebuildでは例外をここで回収し、候補pathを全件
            # 不完全として残してalias guardへ渡す。
            meta_map = parse_minutes_source_meta(
                Path(target["index_json_path"]), strict=True
            )
        except Exception as exc:
            for file_path in source_files:
                record_source_integrity_outcome(
                    "minutes",
                    target_slug,
                    file_path,
                    "metadata_error",
                    reason=f"failed to load {target['index_json_path']}: {exc}",
                )
            if strict:
                raise RuntimeError(f"failed to enumerate minutes files dir={downloads_dir}: {exc}") from exc
            print(f"[WARN] failed to enumerate minutes files dir={downloads_dir}: {exc}", file=sys.stderr)
            SKIPPED_SOURCES.append(f"会議録 {meta['slug']}: {exc}")
            continue

        # 取得できたのが目次だけの取得元がある。件数だけでは「反映待ち」と
        # 区別できないので、種別ごとの内訳を残して収集状況ページで使う。
        kind_counts: dict[str, int] = {}
        indexable_before_dedupe = 0
        deduplicated_count = 0
        yielded_count = 0
        truncated = False
        # 同じ本文を複数の会議種別に置く取得元がある。北海道議会の連合審査会は
        # 「総務」「産炭地域」「連合審査会」の 3 つに同じ本文が入っており、
        # 検索結果に同じ会議が 3 回並ぶ。自治体ごとに 1 回だけ載せる。
        seen_bodies: set[str] = set()
        for file_path in source_files:
            try:
                record = build_minutes_record(file_path, downloads_dir, meta_map, indexed_at)
            except Exception as exc:
                kind_counts["unreadable"] = kind_counts.get("unreadable", 0) + 1
                record_source_integrity_outcome(
                    "minutes", target_slug, file_path, "parse_error", reason=str(exc)
                )
                if strict:
                    raise RuntimeError(f"failed to parse minutes file={file_path}: {exc}") from exc
                print(f"[WARN] failed to parse minutes file={file_path}: {exc}", file=sys.stderr)
                continue
            if record is None:
                kind_counts["unreadable"] = kind_counts.get("unreadable", 0) + 1
                record_source_integrity_outcome(
                    "minutes",
                    target_slug,
                    file_path,
                    "unreadable",
                    reason="file did not produce an indexable record",
                )
                if strict:
                    raise RuntimeError(f"minutes file did not produce an indexable record: {file_path}")
                print(
                    f"[WARN] minutes file did not produce an indexable record: {file_path}",
                    file=sys.stderr,
                )
                continue

            kind = str(record.doc_type or "unknown")
            if kind != "minutes":
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                if kind in {"toc", "aux"}:
                    record_source_integrity_outcome("minutes", target_slug, file_path, kind)
                else:
                    record_source_integrity_outcome(
                        "minutes",
                        target_slug,
                        file_path,
                        "unexpected_doc_type",
                        reason=f"unexpected document kind: {kind}",
                    )
                    if strict:
                        raise RuntimeError(
                            f"minutes file produced unexpected document kind={kind}: {file_path}"
                        )
                    print(
                        f"[WARN] minutes file produced unexpected document kind={kind}: {file_path}",
                        file=sys.stderr,
                    )
                continue

            indexable_before_dedupe += 1
            # 同じ PDF を別のパスで配る取得元がある。南国市の議決一覧は
            # `fd_17file` と `fd_21file` の下に同じ `downfile105294.pdf` があり、
            # 本文は同じで `出典:` 行だけが違う。その行を含めて比べていたので
            # 別物として二つとも載せていた。取得元の住所は文書の中身ではない。
            body_key = hashlib.sha1(
                body_without_source_header(str(record.content or "")).encode("utf-8")
            ).hexdigest()
            if body_key in seen_bodies:
                kind_counts["duplicate_body"] = kind_counts.get("duplicate_body", 0) + 1
                record_source_integrity_outcome(
                    "minutes", target_slug, file_path, "duplicate_body"
                )
                continue
            seen_bodies.add(body_key)
            deduplicated_count += 1
            local_id = stable_local_id(meta["slug"], record.rel_path)
            title = clean_text(record.title)
            meeting_name = clean_text(record.meeting_name)
            body = str(record.content or "")
            title_terms = " ".join(part for part in [record.title_terms, record.meeting_name_terms] if clean_text(part))
            source_url = clean_text(record.source_url)
            held_on = plausible_meeting_date(record.held_on)
            document = {
                **meta,
                "doc_type": "minutes",
                "title": title,
                "title_terms": title_terms or terms_text(" ".join([title, meeting_name])),
                "body": body,
                "body_terms": clean_text(record.content_terms) or terms_text(body),
                "body_length": len(body),
                "source_url": source_url,
                "detail_url": source_url,
                "source_file": record.rel_path,
                "source_system": source_system,
                "indexed_at": indexed_at,
                "parser_generation": PARSER_GENERATION,
                "updated_at": normalize_datetime(record.indexed_at) or indexed_at,
                "sort_date": held_on,
                "assembly_name": assembly_name,
                "meeting_name": meeting_name,
                "year_label": clean_text(record.year_label),
                "held_on": held_on,
                "speaker": "",
                "speaker_role": "",
                "local_id": local_id,
            }
            # yield直前の本数を正とする。重複判定前に minutes を増やすと、
            # 検索へ載らない重複まで「索引可能」と数えて反映待ちが永久に残る。
            kind_counts["minutes"] = kind_counts.get("minutes", 0) + 1
            yielded_count += 1
            record_source_integrity_outcome(
                "minutes", target_slug, file_path, "yielded"
            )
            yield f"minutes:{meta['slug']}:{local_id}", compact_document(document)
            emitted += 1
            if limit > 0 and emitted >= limit:
                truncated = True
                break

        # 途中で打ち切ったときの内訳は取得元の実態を表さないので残さない。
        if not truncated:
            write_document_kind_counts(
                target,
                kind_counts,
                indexed_at,
                raw_total=len(source_files),
                indexable_before_dedupe=indexable_before_dedupe,
                deduplicated=deduplicated_count,
                yielded=yielded_count,
            )
        if truncated:
            mark_pending_sources_as_limited("minutes", target_slug)
            return


# 壊れたマニフェストは脇へどけて、本文ファイルから索引を作る。
#
# 浦添市の source_manifest.json.gz は 0 バイトのまま 3 か月あり、その間
# 索引は 17 回失敗して 1 件も更新されなかった。マニフェストは補助の
# メタデータで、本文は html にある。読めないなら無いものとして進み、
# 次の取得が書き直せるように名前を変えて残す。
def load_reiki_manifest_index_or_quarantine(path: Path) -> dict[str, dict[str, Any]]:
    try:
        return load_reiki_manifest_index(path, strict=True)
    except Exception as exc:
        if not path.exists():
            return {}
        quarantined = path.with_name(
            f"{path.name}.corrupt-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        try:
            path.rename(quarantined)
        except Exception as rename_exc:
            raise RuntimeError(
                f"broken reiki manifest {path} could not be set aside: {rename_exc}"
            ) from exc
        print(
            f"[WARN] broken reiki manifest set aside: {path} -> {quarantined.name} ({exc})",
            file=sys.stderr,
        )
        return {}


def iter_reiki_documents(
    limit: int = 0,
    slugs: set[str] | None = None,
    *,
    strict: bool = False,
    exclude_slugs: set[str] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    indexed_at = utc_now_iso()
    emitted = 0
    slug_filter = slugs or set()
    skip_slugs = exclude_slugs or set()
    for target in reiki_targets.iter_reiki_targets():
        target_slug = str(target.get("slug") or "").strip()
        if slug_filter and target_slug not in slug_filter:
            continue
        if target_slug in skip_slugs:
            continue
        clean_html_dir = Path(target["html_dir"])
        source_html_dir = Path(target["source_dir"])
        # 会議録側と同じ理由。保存ディレクトリが見えないのは「文書が無い」では
        # ない。取得データを共有していない worker で走らせると全自治体が 0 件に
        # なり、しかも成功で終わる。**見えないことを 0 件にしない。**
        if (
            slugs
            and target_slug in slugs
            and not clean_html_dir.exists()
            and not source_html_dir.exists()
        ):
            raise RuntimeError(
                f"保存ディレクトリが見当たりません: {clean_html_dir}。"
                "この worker から取得データが見えていない可能性があります。"
            )
        html_root = clean_html_dir if clean_html_dir.is_dir() else source_html_dir
        has_local_detail = clean_html_dir.is_dir()
        if not html_root.is_dir():
            continue
        meta = target_metadata(target)
        source_system = str(target.get("system_type") or "").strip()
        try:
            html_files = collect_reiki_preferred_files(html_root, {".html", ".htm"})
        except Exception as exc:
            if strict:
                raise RuntimeError(f"failed to enumerate reiki files dir={html_root}: {exc}") from exc
            print(f"[WARN] failed to enumerate reiki files dir={html_root}: {exc}", file=sys.stderr)
            SKIPPED_SOURCES.append(f"例規 {meta['slug']}: {exc}")
            continue
        start_source_integrity_audit("reiki", target_slug, html_files.values())
        try:
            markdown_files = build_alias_map(
                collect_reiki_preferred_files(Path(target["markdown_dir"]), {".md"})
            )
            classification_files = build_alias_map(
                collect_reiki_preferred_files(Path(target["classification_dir"]), {".json"})
            )
            manifest_index = load_reiki_manifest_index_or_quarantine(
                Path(target["work_root"]) / "source_manifest.json.gz"
            )
            prefixes = reiki_sortable_prefixes(target)
        except Exception as exc:
            for html_path in html_files.values():
                record_source_integrity_outcome(
                    "reiki",
                    target_slug,
                    html_path,
                    "metadata_error",
                    reason=str(exc),
                )
            if strict:
                raise RuntimeError(f"failed to enumerate reiki files dir={html_root}: {exc}") from exc
            print(f"[WARN] failed to enumerate reiki files dir={html_root}: {exc}", file=sys.stderr)
            SKIPPED_SOURCES.append(f"例規 {meta['slug']}: {exc}")
            continue

        for key, html_path in sorted(html_files.items()):
            try:
                record = build_reiki_record(
                    key,
                    html_path,
                    preferred_reiki_sidecar(markdown_files, key),
                    preferred_reiki_sidecar(classification_files, key),
                    manifest_index.get(key) or manifest_index.get(Path(key).name),
                    prefixes,
                    target,
                    strict=True,
                )
            except Exception as exc:
                record_source_integrity_outcome(
                    "reiki", target_slug, html_path, "parse_error", reason=str(exc)
                )
                if strict:
                    raise RuntimeError(f"failed to parse reiki file={html_path}: {exc}") from exc
                print(f"[WARN] failed to parse reiki file={html_path}: {exc}", file=sys.stderr)
                continue
            if not isinstance(record, dict):
                record_source_integrity_outcome(
                    "reiki",
                    target_slug,
                    html_path,
                    "unreadable",
                    reason="file did not produce an indexable record",
                )
                # 1 ファイル読めないだけで自治体全体を止めない。厚岸町は
                # 1,700 件のうち 1 件で止まり、何度積み直しても 1 件も
                # 載らなかった。読めない分は監査に残し、残りは載せる。
                # 全部が読めなければ「0 件成功の禁止」が止める。
                print(
                    f"[WARN] reiki file did not produce an indexable record: {html_path}",
                    file=sys.stderr,
                )
                continue

            filename = clean_text(record.get("filename")) or key
            local_id = stable_local_id(meta["slug"], filename)
            title = clean_text(record.get("title")) or Path(filename).name
            # 取得元のエラーページを例規として公開しない。題名「エラー」が
            # 17 件、本文が「ご指定のページは見つかりませんでした」だけのものが
            # 13 件（上里町）あった。条文は無いのに「例規が 17 件ある」と見える。
            if looks_like_error_page(title, str(record.get("content_text") or "")):
                record_source_integrity_outcome(
                    "reiki", target_slug, html_path, "error_page"
                )
                continue
            # 本文は取得元の原文だけにする。AI 評価や所管課をここへ混ぜると、
            # 検索結果の本文が評価文から始まり、自治体の見解や法文と読み違える。
            body = str(record.get("content_text") or "")
            # AI が付けた評価。本文とは別に持ち、混ざらないようにする。
            # 検索対象にもするので、内部の識別子（`necessityScore` `Class G`）は
            # ここでも落とす。落とさないと、本文に無い語で条例が当たる。
            evaluation_text = "\n".join(
                part
                for part in [
                    drop_internal_identifiers(clean_text(record.get("combined_stance"))),
                    drop_internal_identifiers(clean_text(record.get("combined_reason"))),
                    drop_internal_identifiers(clean_text(record.get("reason"))),
                ]
                if part
            )
            title_terms = " ".join(
                part
                for part in [
                    clean_text(record.get("title_terms")),
                    clean_text(record.get("reading_terms")),
                ]
                if part
            ) or terms_text(title)
            body_terms = " ".join(
                part
                for part in [
                    clean_text(record.get("content_terms")),
                    clean_text(record.get("department_terms")),
                    # AI 評価の文には `necessityScore` `Class G` のような内部の
                    # 識別子が混ざる。そのまま検索語に入れると、本文に無い語で
                    # 条例が当たる（実際 `necessityScore` で 13 件出ていた）。
                    # 日本語の評価語は残し、英数字だけの識別子を落とす。
                    drop_internal_identifiers(clean_text(record.get("combined_reason_terms"))),
                    drop_internal_identifiers(clean_text(record.get("reason_terms"))),
                    clean_text(record.get("secondary_terms")),
                    clean_text(record.get("lens_terms")),
                    clean_text(record.get("taxonomy_terms")),
                ]
                if part
            ) or terms_text(body)
            promulgated_on = normalize_date(record.get("enactment_date"))
            updated_at = normalize_datetime(record.get("updated_at")) or indexed_at
            detail_file = filename if filename.lower().endswith((".html", ".htm")) else filename + ".html"
            source_url = clean_text(record.get("source_url"))
            detail_url = (
                "/reiki/?" + urlencode({"slug": meta["slug"], "file": detail_file})
                if has_local_detail
                else source_url
            )
            document = {
                **meta,
                "doc_type": "reiki",
                "title": title,
                "title_terms": title_terms,
                "body": body,
                "body_terms": body_terms,
                "body_length": len(body),
                "source_url": source_url,
                "detail_url": detail_url,
                "source_file": clean_text(record.get("source_file")) or detail_file,
                "source_system": source_system,
                # AI 評価の文面。本文とは分けて持つ。混ぜると、評価文が
                # 例規本文として表示され、自治体の見解と読み違えられる。
                "evaluation_text": evaluation_text,
                "indexed_at": indexed_at,
                "parser_generation": PARSER_GENERATION,
                "updated_at": updated_at,
                # 公布日が読めないときに取得日を入れると、昭和 26 年の規則が
                # 今日の日付で「最新」に見える。日付が無いなら持たせない。
                # 実データで 72 件がこの形だった（中央区の昭和 29 年条例など）。
                "sort_date": promulgated_on,
                "filename": filename,
                "ordinance_no": clean_text(record.get("number") or record.get("ordinance_no")),
                "category": clean_text(record.get("primary_class")) or clean_text(record.get("document_type")),
                "promulgated_on": promulgated_on,
                "enforced_on": None,
                # 最終改正日は取得元から読めていない。取得日を入れると
                # 「今日改正された」と読める。読めるまでは空にする。
                "amended_on": None,
                "local_id": local_id,
            }
            record_source_integrity_outcome(
                "reiki", target_slug, html_path, "yielded"
            )
            yield f"reiki:{meta['slug']}:{local_id}", compact_document(document)
            emitted += 1
            if limit > 0 and emitted >= limit:
                mark_pending_sources_as_limited("reiki", target_slug)
                return


def _count_documents_by_slug(
    targets: Iterable[dict[str, Any]],
    count_one: Callable[[dict[str, Any]], int],
    *,
    limit: int,
    slugs: set[str] | None,
    exclude_slugs: set[str] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    total = 0
    slug_filter = slugs or set()
    skip_slugs = exclude_slugs or set()
    for target in targets:
        slug = str(target.get("slug") or "").strip()
        if slug == "" or (slug_filter and slug not in slug_filter) or slug in skip_slugs:
            continue
        try:
            count = count_one(target)
        except Exception as exc:
            print(f"[WARN] failed to count documents slug={slug}: {exc}", file=sys.stderr)
            count = 0
        if count <= 0:
            continue
        if limit > 0 and total + count > limit:
            count = max(0, limit - total)
        counts[slug] = count
        total += count
        if limit > 0 and total >= limit:
            break
    return counts


def _count_minutes_target(target: dict[str, Any]) -> int:
    downloads_dir = Path(target["downloads_dir"])
    if not downloads_dir.is_dir():
        return 0
    return len(choose_minutes_source_files(downloads_dir))


def _count_reiki_target(target: dict[str, Any]) -> int:
    clean_html_dir = Path(target["html_dir"])
    html_root = clean_html_dir if clean_html_dir.is_dir() else Path(target["source_dir"])
    if not html_root.is_dir():
        return 0
    return len(collect_reiki_preferred_files(html_root, {".html", ".htm"}))


def count_minutes_documents_by_slug(
    limit: int = 0, slugs: set[str] | None = None, exclude_slugs: set[str] | None = None
) -> dict[str, int]:
    return _count_documents_by_slug(
        gijiroku_targets.iter_gijiroku_targets(), _count_minutes_target, limit=limit, slugs=slugs, exclude_slugs=exclude_slugs
    )


def count_reiki_documents_by_slug(
    limit: int = 0, slugs: set[str] | None = None, exclude_slugs: set[str] | None = None
) -> dict[str, int]:
    return _count_documents_by_slug(
        reiki_targets.iter_reiki_targets(), _count_reiki_target, limit=limit, slugs=slugs, exclude_slugs=exclude_slugs
    )


def compact_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if value is not None and not (isinstance(value, str) and value == "")
    }


def create_versioned_index(
    client: OpenSearchClient,
    index_name: str,
    *,
    shards: int,
    replicas: int,
) -> None:
    body = build_index_body(shards=shards, replicas=replicas, refresh_interval="-1")
    client.request("PUT", f"/{quote(index_name)}", body=body)


def update_index_after_bulk(client: OpenSearchClient, index_name: str, *, replicas: int) -> None:
    client.request(
        "PUT",
        f"/{quote(index_name)}/_settings",
        body={
            "index": {
                "refresh_interval": "1s",
                "number_of_replicas": max(0, int(replicas)),
            }
        },
    )
    client.request("POST", f"/{quote(index_name)}/_refresh")


def index_documents(
    client: OpenSearchClient,
    index_name: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    *,
    bulk_size: int,
    bulk_bytes: int = 8 * 1024 * 1024,
    bulk_concurrency: int = 2,
    progress_callback: Callable[[int, dict[str, Any], int], None] | None = None,
    slug_complete_callback: Callable[[str, dict[str, Any], int], None] | None = None,
) -> int:
    # NDJSON 行はここで一度だけ bytes 化し、件数とペイロードサイズの両方で flush する。
    # 会議録の本文は 1 件で数百 KB になることがあるため、件数だけだと過大 bulk になりうる。
    #
    # bulk 送信は ThreadPoolExecutor で多重インフライト化する。読み込み・解析と
    # OpenSearch 側の索引付けが交互待ちで直列化すると、双方が半分遊んだまま
    # スループットが頭打ちになる（全量 rebuild の実測でどちらも 50% 未満だった）。
    # slug 境界では全 bulk の完了を待ってから slug_complete_callback（部分公開）を呼ぶ。
    pending_lines: list[bytes] = []
    pending_count = 0
    pending_bytes = 0
    total = 0
    current_slug = ""
    current_slug_start_total = 0
    current_slug_last_source: dict[str, Any] = {}
    in_flight: deque[tuple[Future, int, dict[str, Any]]] = deque()
    max_in_flight = max(1, int(bulk_concurrency))

    with ThreadPoolExecutor(max_workers=max_in_flight) as pool:

        def reap_oldest() -> None:
            nonlocal total
            future, count, batch_last_source = in_flight.popleft()
            future.result()  # bulk 失敗はここで送出され、rebuild/update 全体を失敗させる
            total += count
            print(f"[BULK] index={index_name} total={total}", flush=True)
            if progress_callback is not None:
                progress_callback(total, batch_last_source, max(0, total - current_slug_start_total))

        def reap_all() -> None:
            while in_flight:
                reap_oldest()

        def flush_actions() -> None:
            nonlocal pending_lines, pending_count, pending_bytes
            if not pending_lines:
                return
            while len(in_flight) >= max_in_flight:
                reap_oldest()
            in_flight.append(
                (
                    pool.submit(client.bulk_lines, pending_lines, pending_count),
                    pending_count,
                    current_slug_last_source,
                )
            )
            pending_lines = []
            pending_count = 0
            pending_bytes = 0

        try:
            for doc_id, source in documents:
                slug = str(source.get("slug") or "").strip()
                if current_slug and slug != "" and slug != current_slug:
                    flush_actions()
                    reap_all()
                    if slug_complete_callback is not None:
                        slug_complete_callback(current_slug, current_slug_last_source, total)
                    current_slug_start_total = total
                if slug != "":
                    if current_slug == "":
                        current_slug_start_total = total
                    current_slug = slug
                    current_slug_last_source = source
                meta_line = json.dumps(
                    {"index": {"_index": index_name, "_id": doc_id}}, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                source_line = json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                pending_lines.append(meta_line)
                pending_lines.append(source_line)
                pending_count += 1
                pending_bytes += len(meta_line) + len(source_line) + 2
                if pending_count >= bulk_size or pending_bytes >= max(1, bulk_bytes):
                    flush_actions()
            flush_actions()
            reap_all()
        except BaseException:
            # 失敗時は未開始の bulk を捨てて早く抜ける（実行中のものは完了を待つ）。
            for future, _count, _source in in_flight:
                future.cancel()
            raise
    if current_slug and slug_complete_callback is not None:
        slug_complete_callback(current_slug, current_slug_last_source, total)
    return total


def indices_for_alias(client: OpenSearchClient, alias: str) -> list[str]:
    try:
        response = client.request("GET", f"/_alias/{quote(alias)}")
    except OpenSearchRequestError as exc:
        if exc.status == 404:
            return []
        raise
    if not isinstance(response, dict):
        return []
    return sorted(response.keys())


def alias_partial_completed_slugs(client: OpenSearchClient, alias: str, index_name: str) -> set[str]:
    """部分公開 alias の terms filter から、構築完了済み slug 一覧を読み取る。

    rebuild は自治体が終わるたびに「新 index は完了 slug だけを公開する」filter を
    張り替えるので、この filter がそのまま resume 時のスキップリストになる。"""
    try:
        response = client.request("GET", f"/_alias/{quote(alias)}")
    except OpenSearchRequestError as exc:
        if exc.status == 404:
            return set()
        raise
    if not isinstance(response, dict):
        return set()
    entry = response.get(index_name)
    aliases = entry.get("aliases") if isinstance(entry, dict) else None
    info = aliases.get(alias) if isinstance(aliases, dict) else None
    filter_body = info.get("filter") if isinstance(info, dict) else None
    terms = filter_body.get("terms") if isinstance(filter_body, dict) else None
    slugs = terms.get("slug") if isinstance(terms, dict) else None
    if not isinstance(slugs, list):
        return set()
    return {str(slug).strip() for slug in slugs if str(slug).strip()}


def single_index_for_alias(client: OpenSearchClient, alias: str) -> str | None:
    indices = indices_for_alias(client, alias)
    if len(indices) > 1:
        raise RuntimeError(f"Alias {alias} points to multiple indexes; cannot use it as an update target.")
    return indices[0] if indices else None


def delete_documents_for_slugs(
    client: OpenSearchClient,
    *,
    index_or_alias: str,
    doc_type: str,
    slugs: set[str],
    indexed_before: str | None = None,
) -> int:
    if not slugs:
        raise ValueError("Incremental update requires at least one slug.")
    filters: list[dict[str, Any]] = [
        {"term": {"doc_type": doc_type}},
        {"terms": {"slug": sorted(slugs)}},
    ]
    if indexed_before:
        # 今回投入分（indexed_at が新しい）を残し、前回までの世代だけを消す。
        filters.append({"range": {"indexed_at": {"lt": indexed_before}}})
    response = client.request(
        "POST",
        f"/{quote(index_or_alias)}/_delete_by_query",
        query={"conflicts": "proceed", "refresh": "false"},
        body={"query": {"bool": {"filter": filters}}},
    )
    deleted = int(response.get("deleted") or 0) if isinstance(response, dict) else 0
    print(f"[DELETE] target={index_or_alias} doc_type={doc_type} slugs={len(slugs)} deleted={deleted}", flush=True)
    return deleted


def refresh_search_target(client: OpenSearchClient, index_or_alias: str) -> None:
    client.request("POST", f"/{quote(index_or_alias)}/_refresh")


def switch_aliases(
    client: OpenSearchClient,
    *,
    minutes_index: str | None,
    reiki_index: str | None,
    minutes_alias: str,
    reiki_alias: str,
    documents_alias: str,
) -> None:
    # alias 切り替えが公開反映の境界になる。
    # 先に versioned index へ構築し、会議録・例規集 alias と統合 documents alias を
    # 原子的に差し替えることで、読み手に構築途中の index を見せない。
    target_minutes = [minutes_index] if minutes_index else indices_for_alias(client, minutes_alias)
    target_reiki = [reiki_index] if reiki_index else indices_for_alias(client, reiki_alias)

    actions: list[dict[str, Any]] = []
    for alias in [minutes_alias, reiki_alias, documents_alias]:
        for index in indices_for_alias(client, alias):
            actions.append({"remove": {"index": index, "alias": alias}})
    for index in target_minutes:
        if index:
            actions.append({"add": {"index": index, "alias": minutes_alias}})
    for index in target_reiki:
        if index:
            actions.append({"add": {"index": index, "alias": reiki_alias}})
    for index in target_minutes + target_reiki:
        if index:
            actions.append({"add": {"index": index, "alias": documents_alias}})
    if actions:
        client.request("POST", "/_aliases", body={"actions": actions})


def alias_filter_for_completed_slugs(slugs: set[str]) -> dict[str, Any]:
    return {"terms": {"slug": sorted(slugs)}}


def alias_filter_excluding_completed_slugs(slugs: set[str]) -> dict[str, Any]:
    return {"bool": {"must_not": [{"terms": {"slug": sorted(slugs)}}]}}


def add_alias_action(index: str, alias: str, *, filter_body: dict[str, Any] | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"index": index, "alias": alias}
    if filter_body is not None:
        action["filter"] = filter_body
    return {"add": action}


def publish_partial_aliases(
    client: OpenSearchClient,
    *,
    minutes_index: str | None,
    reiki_index: str | None,
    initial_minutes_indices: list[str],
    initial_reiki_indices: list[str],
    completed_minutes_slugs: set[str],
    completed_reiki_slugs: set[str],
    minutes_alias: str,
    reiki_alias: str,
    documents_alias: str,
) -> None:
    # 長い rebuild 中は、完了済み slug だけを新 index から公開し、
    # 未完了分は旧 index の alias に残す。alias filter を使うことで、
    # 旧文書を新 index へコピーしなくても安全に混在公開できる。
    actions: list[dict[str, Any]] = []
    for alias in [minutes_alias, reiki_alias, documents_alias]:
        for index in indices_for_alias(client, alias):
            actions.append({"remove": {"index": index, "alias": alias}})

    if completed_minutes_slugs and minutes_index:
        actions.append(
            add_alias_action(minutes_index, minutes_alias, filter_body=alias_filter_for_completed_slugs(completed_minutes_slugs))
        )
    for index in initial_minutes_indices:
        actions.append(
            add_alias_action(
                index,
                minutes_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_minutes_slugs)
                if completed_minutes_slugs
                else None,
            )
        )

    if completed_reiki_slugs and reiki_index:
        actions.append(add_alias_action(reiki_index, reiki_alias, filter_body=alias_filter_for_completed_slugs(completed_reiki_slugs)))
    for index in initial_reiki_indices:
        actions.append(
            add_alias_action(
                index,
                reiki_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_reiki_slugs)
                if completed_reiki_slugs
                else None,
            )
        )

    if completed_minutes_slugs and minutes_index:
        actions.append(
            add_alias_action(
                minutes_index,
                documents_alias,
                filter_body=alias_filter_for_completed_slugs(completed_minutes_slugs),
            )
        )
    for index in initial_minutes_indices:
        actions.append(
            add_alias_action(
                index,
                documents_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_minutes_slugs)
                if completed_minutes_slugs
                else None,
            )
        )

    if completed_reiki_slugs and reiki_index:
        actions.append(add_alias_action(reiki_index, documents_alias, filter_body=alias_filter_for_completed_slugs(completed_reiki_slugs)))
    for index in initial_reiki_indices:
        actions.append(
            add_alias_action(
                index,
                documents_alias,
                filter_body=alias_filter_excluding_completed_slugs(completed_reiki_slugs)
                if completed_reiki_slugs
                else None,
            )
        )

    if actions:
        client.request("POST", "/_aliases", body={"actions": actions})


def publish_completed_slug(
    client: OpenSearchClient,
    *,
    doc_type: str,
    index_name: str,
    minutes_index: str | None,
    reiki_index: str | None,
    slug: str,
    initial_minutes_indices: list[str],
    initial_reiki_indices: list[str],
    completed_minutes_slugs: set[str],
    completed_reiki_slugs: set[str],
    minutes_alias: str,
    reiki_alias: str,
    documents_alias: str,
) -> None:
    slug = slug.strip()
    if slug == "":
        return
    if doc_type == "minutes":
        completed_minutes_slugs.add(slug)
    elif doc_type == "reiki":
        completed_reiki_slugs.add(slug)
    else:
        return

    refresh_search_target(client, index_name)
    publish_partial_aliases(
        client,
        minutes_index=minutes_index,
        reiki_index=reiki_index,
        initial_minutes_indices=initial_minutes_indices,
        initial_reiki_indices=initial_reiki_indices,
        completed_minutes_slugs=completed_minutes_slugs,
        completed_reiki_slugs=completed_reiki_slugs,
        minutes_alias=minutes_alias,
        reiki_alias=reiki_alias,
        documents_alias=documents_alias,
    )
    print(f"[PUBLISH] doc_type={doc_type} slug={slug} index={index_name}", flush=True)


def build_one(
    client: OpenSearchClient,
    *,
    index_name: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    shards: int,
    replicas: int,
    bulk_size: int,
    bulk_bytes: int = 8 * 1024 * 1024,
    bulk_concurrency: int = 2,
    create_index: bool = True,
    progress_callback: Callable[[int, dict[str, Any], int], None] | None = None,
    slug_complete_callback: Callable[[str, dict[str, Any], int], None] | None = None,
) -> int:
    if create_index:
        print(f"[CREATE] {index_name}", flush=True)
        create_versioned_index(client, index_name, shards=shards, replicas=replicas)
    else:
        # resume: 既存 index へ追記する。中断時点の設定に関わらず bulk 向けに戻す。
        print(f"[RESUME] {index_name}", flush=True)
        client.request(
            "PUT",
            f"/{quote(index_name)}/_settings",
            body={"index": {"refresh_interval": "-1", "number_of_replicas": 0}},
        )
    count = index_documents(
        client,
        index_name,
        documents,
        bulk_size=bulk_size,
        bulk_bytes=bulk_bytes,
        bulk_concurrency=bulk_concurrency,
        progress_callback=progress_callback,
        slug_complete_callback=slug_complete_callback,
    )
    update_index_after_bulk(client, index_name, replicas=replicas)
    print(f"[DONE] index={index_name} count={count}", flush=True)
    return count


def update_one(
    client: OpenSearchClient,
    *,
    doc_type: str,
    index_prefix: str,
    alias: str,
    documents_alias: str,
    minutes_alias: str,
    reiki_alias: str,
    build_id: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    slugs: set[str],
    shards: int,
    replicas: int,
    bulk_size: int,
    bulk_bytes: int,
    bulk_concurrency: int,
    switch_alias: bool,
    allow_empty_slug_delete: bool = False,
) -> int:
    if not slugs:
        raise ValueError("Incremental update requires --slug.")
    # documents の各 indexed_at は iterator 評価時に採番されるため、
    # 先に cutoff を取れば「今回投入分 >= cutoff > 前回まで」の関係が保証される。
    update_cutoff = utc_now_iso()
    documents_list = list(documents)
    yielded_slugs = {
        str(source.get("slug") or "").strip()
        for _doc_id, source in documents_list
        if str(source.get("slug") or "").strip()
    }
    unexpected_slugs = yielded_slugs - slugs
    if unexpected_slugs:
        raise RuntimeError(
            f"Incremental update generated documents outside requested slugs: {sorted(unexpected_slugs)}"
        )
    empty_slugs = slugs - yielded_slugs
    if empty_slugs:
        # 0件は「原典から全廃」と「取得dir欠落・parser drop」を区別できない。
        # 実際に生成できたslugだけを世代削除へ渡し、全廃は明示時だけ扱う。
        if allow_empty_slug_delete:
            message = "明示フラグにより旧文書も削除します。"
        else:
            message = (
                "旧文書を保持します。削除を意図している場合だけ "
                "--allow-empty-slug-delete を付けてください。"
            )
        print(
            f"[WARN] {doc_type} generated no indexable documents for "
            f"{','.join(sorted(empty_slugs))}; {message}",
            file=sys.stderr,
        )
    delete_slugs = slugs if allow_empty_slug_delete else yielded_slugs
    if not documents_list and not delete_slugs:
        return 0

    current_index = single_index_for_alias(client, alias)
    if current_index is None and not documents_list:
        print(
            f"[WARN] alias={alias} has no index; no empty slug documents to delete.",
            file=sys.stderr,
        )
        return 0
    if current_index is None:
        # まだどの index も指していない alias には、差分更新の delete+bulk ができない。
        # 初回だけ file lock の下で bootstrap し、同時実行された slug 更新同士が
        # 競合する初期 index を作らないようにする。
        lock_path = build_locks.acquire_build_lock(
            f"opensearch-{doc_type}-bootstrap",
            owner="build_opensearch_index",
            wait_seconds=900.0,
        )
        if lock_path is None:
            raise RuntimeError(f"Could not acquire OpenSearch bootstrap lock for {doc_type}.")
        try:
            current_index = single_index_for_alias(client, alias)
            if current_index is None:
                index_name = f"{index_prefix}-v{build_id}"
                print(f"[BOOTSTRAP] alias={alias} has no index; creating {index_name} from selected slugs", flush=True)
                count = build_one(
                    client,
                    index_name=index_name,
                    documents=documents_list,
                    shards=shards,
                    replicas=replicas,
                    bulk_size=bulk_size,
                    bulk_bytes=bulk_bytes,
                    bulk_concurrency=bulk_concurrency,
                )
                if switch_alias:
                    switch_aliases(
                        client,
                        minutes_index=index_name if doc_type == "minutes" else None,
                        reiki_index=index_name if doc_type == "reiki" else None,
                        minutes_alias=minutes_alias,
                        reiki_alias=reiki_alias,
                        documents_alias=documents_alias,
                    )
                    print(f"[ALIAS] {alias}={index_name} {documents_alias}=combined", flush=True)
                return count
        finally:
            build_locks.release_build_lock(lock_path)

    print(f"[UPDATE] alias={alias} index={current_index} slugs={','.join(sorted(slugs))}", flush=True)
    # update mode は指定自治体だけを意図的に書き換える。
    # alias 配下の他自治体 index は公開したまま触らない。
    # document ID は slug+ファイルパス由来で安定しているため、まず bulk で上書き投入し、
    # そのあとで前回世代（indexed_at が cutoff より古い文書）だけを削除する。
    # 削除を先にすると、途中で落ちた場合にその自治体が次の成功まで検索から消えてしまう。
    count = 0
    if documents_list:
        count = index_documents(
            client,
            alias,
            documents_list,
            bulk_size=bulk_size,
            bulk_bytes=bulk_bytes,
            bulk_concurrency=bulk_concurrency,
        )
        refresh_search_target(client, alias)
    if delete_slugs:
        delete_documents_for_slugs(
            client,
            index_or_alias=alias,
            doc_type=doc_type,
            slugs=delete_slugs,
            # 全件0の明示削除には今回世代が無いので、時刻条件を付けず全て消す。
            indexed_before=update_cutoff if documents_list else None,
        )
    refresh_search_target(client, alias)
    print(f"[DONE] alias={alias} doc_type={doc_type} count={count}", flush=True)
    return count


def main() -> int:
    args = parse_args()
    reset_source_integrity_tracking()
    build_id = args.build_id.strip() or default_build_id()
    slugs = parse_slug_filter(args.slug)
    mode = args.mode
    if mode == "auto":
        mode = "update" if slugs else "rebuild"
    if mode == "update" and not slugs:
        print("[ERROR] --mode update requires --slug.", file=sys.stderr, flush=True)
        return 2
    # 一部だけ作った索引を公開の alias に切り替えると、残りの自治体が
    # まるごと検索から消える。--mode update は自治体ごとの差し替えなので
    # 別（alias はそのまま）。rebuild だけを止める。
    # 差分更新は、その自治体の文書を全部消してから入れ直す。--limit で
    # 切ると、消したあと一部しか戻らない。生きている検索から大半が消える。
    if mode == "update" and int(args.limit or 0) > 0 and not args.allow_partial_alias:
        print(
            f"[ERROR] --mode update に --limit {args.limit} は付けられません。"
            "差分更新は対象自治体の文書を全部消してから入れ直すので、"
            "切った分がそのまま検索から消えます。"
            "意図しているなら --allow-partial-alias を付けてください。",
            file=sys.stderr,
            flush=True,
        )
        return 2

    # resume も途中から作り直すので、slug や limit で絞れば部分索引になる。
    partial_rebuild = mode in {"rebuild", "resume"} and (
        bool(slugs) or int(args.limit or 0) > 0
    )
    if partial_rebuild and not args.no_switch_alias and not args.allow_partial_alias:
        reason = []
        if slugs:
            reason.append(f"{len(slugs)}自治体だけ")
        if int(args.limit or 0) > 0:
            reason.append(f"--limit {args.limit}")
        print(
            "[ERROR] " + "・".join(reason) + "で作った索引は公開の alias に"
            "切り替えられません。残りの自治体が検索から消えます。"
            "作るだけなら --no-switch-alias、意図して公開するなら"
            " --allow-partial-alias を付けてください。",
            file=sys.stderr,
            flush=True,
        )
        return 2

    resume_index = str(args.resume_index or "").strip()
    if mode == "resume":
        if resume_index == "":
            print("[ERROR] --mode resume requires --resume-index.", file=sys.stderr, flush=True)
            return 2
        if args.doc_type not in {"minutes", "reiki"}:
            print("[ERROR] --mode resume requires --doc-type minutes or reiki.", file=sys.stderr, flush=True)
            return 2

    client = OpenSearchClient(
        args.opensearch_url,
        user=args.opensearch_user,
        password=args.opensearch_password,
        insecure_dev=bool(args.insecure_dev),
    )
    bulk_size = max(1, args.bulk_size)
    bulk_bytes = max(1, args.bulk_bytes)
    bulk_concurrency = max(1, args.bulk_concurrency)

    if mode == "update":
        if args.doc_type in {"all", "minutes"}:
            update_one(
                client,
                doc_type="minutes",
                index_prefix="miyabe-minutes",
                alias=args.minutes_alias,
                documents_alias=args.documents_alias,
                minutes_alias=args.minutes_alias,
                reiki_alias=args.reiki_alias,
                build_id=build_id,
                documents=iter_minutes_documents(limit=args.limit, slugs=slugs, strict=True),
                slugs=slugs,
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                bulk_bytes=bulk_bytes,
                bulk_concurrency=bulk_concurrency,
                switch_alias=not args.no_switch_alias,
                allow_empty_slug_delete=bool(args.allow_empty_slug_delete),
            )
        if args.doc_type in {"all", "reiki"}:
            update_one(
                client,
                doc_type="reiki",
                index_prefix="miyabe-reiki",
                alias=args.reiki_alias,
                documents_alias=args.documents_alias,
                minutes_alias=args.minutes_alias,
                reiki_alias=args.reiki_alias,
                build_id=build_id,
                documents=iter_reiki_documents(limit=args.limit, slugs=slugs, strict=True),
                slugs=slugs,
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                bulk_bytes=bulk_bytes,
                bulk_concurrency=bulk_concurrency,
                switch_alias=not args.no_switch_alias,
                allow_empty_slug_delete=bool(args.allow_empty_slug_delete),
            )
        return 0

    # 0 件の索引を公開に切り替えないための計数。作らなかった側は None のまま。
    minutes_count: int | None = None
    reiki_count: int | None = None
    built_minutes_index: str | None = None
    built_reiki_index: str | None = None
    initial_minutes_indices = indices_for_alias(client, args.minutes_alias)
    initial_reiki_indices = indices_for_alias(client, args.reiki_alias)
    completed_minutes_slugs: set[str] = set()
    completed_reiki_slugs: set[str] = set()
    resume_done_slugs: set[str] = set()
    if mode == "resume":
        # 構築途中の index が実在することを確かめてから、部分公開 filter の
        # 完了済み slug をそのまま再開時のスキップリストにする。
        client.request("GET", f"/{quote(resume_index)}")
        resume_alias = args.minutes_alias if args.doc_type == "minutes" else args.reiki_alias
        resume_done_slugs = alias_partial_completed_slugs(client, resume_alias, resume_index)
        if args.doc_type == "minutes":
            completed_minutes_slugs = set(resume_done_slugs)
            initial_minutes_indices = [name for name in initial_minutes_indices if name != resume_index]
        else:
            completed_reiki_slugs = set(resume_done_slugs)
            initial_reiki_indices = [name for name in initial_reiki_indices if name != resume_index]
        print(
            f"[RESUME] index={resume_index} completed_slugs={len(resume_done_slugs)} "
            f"initial_minutes={initial_minutes_indices} initial_reiki={initial_reiki_indices}",
            flush=True,
        )
    minutes_counts_by_slug = (
        count_minutes_documents_by_slug(limit=args.limit, slugs=slugs, exclude_slugs=resume_done_slugs)
        if args.doc_type in {"all", "minutes"}
        else {}
    )
    reiki_counts_by_slug = (
        count_reiki_documents_by_slug(limit=args.limit, slugs=slugs, exclude_slugs=resume_done_slugs)
        if args.doc_type in {"all", "reiki"}
        else {}
    )
    # 進捗表示用の総数は、上の slug 別集計をそのまま合算する（全ファイル走査を二度しない）。
    total_document_count = sum(minutes_counts_by_slug.values()) + sum(reiki_counts_by_slug.values())
    print(f"[COUNT] doc_type={args.doc_type} total={total_document_count}", flush=True)
    status_state = search_rebuild_status_start(
        build_id=build_id,
        doc_type=args.doc_type,
        total_count=total_document_count,
    )
    processed_offset = 0

    def publish_rebuild_slug(
        doc_type: str, index_name: str, slug: str, source: dict[str, Any]
    ) -> None:
        failures = source_integrity_failures(doc_type, slug)
        if not source_slug_can_be_published(
            doc_type, slug, allow_partial_alias=bool(args.allow_partial_alias)
        ):
            # 最終guardまで待つと、このslugだけ先に新indexへ部分公開され、
            # 破損ファイル分の旧文書が既に隠れる。失敗slugは旧alias側へ残す。
            print(
                f"[WARN] keep old alias documents doc_type={doc_type} slug={slug}; "
                f"unexplained_drops={len(failures)} example={failures[0]['path']}",
                file=sys.stderr,
                flush=True,
            )
            return
        publish_completed_slug(
            client,
            doc_type=doc_type,
            index_name=index_name,
            minutes_index=built_minutes_index,
            reiki_index=built_reiki_index,
            slug=slug,
            initial_minutes_indices=initial_minutes_indices,
            initial_reiki_indices=initial_reiki_indices,
            completed_minutes_slugs=completed_minutes_slugs,
            completed_reiki_slugs=completed_reiki_slugs,
            minutes_alias=args.minutes_alias,
            reiki_alias=args.reiki_alias,
            documents_alias=args.documents_alias,
        )
        search_rebuild_status_slug_published(
            status_state,
            source=source,
            published_slug_count=len(completed_minutes_slugs) + len(completed_reiki_slugs),
            published_municipality_count=len(completed_minutes_slugs | completed_reiki_slugs),
        )

    try:
        if args.doc_type in {"all", "minutes"}:
            built_minutes_index = resume_index if mode == "resume" else f"miyabe-minutes-v{build_id}"
            minutes_count = build_one(
                client,
                index_name=built_minutes_index,
                documents=iter_minutes_documents(limit=args.limit, slugs=slugs, exclude_slugs=resume_done_slugs),
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                bulk_bytes=bulk_bytes,
                bulk_concurrency=bulk_concurrency,
                create_index=mode != "resume",
                progress_callback=lambda total, source, slug_current: search_rebuild_status_progress(
                    status_state,
                    stage="minutes",
                    index_name=built_minutes_index or "",
                    processed_count=processed_offset + total,
                    source=source,
                    current_slug_processed_count=slug_current,
                    current_slug_total_count=minutes_counts_by_slug.get(str(source.get("slug") or "").strip(), 0),
                ),
                slug_complete_callback=(
                    None
                    if args.no_switch_alias
                    else lambda slug, source, _total: publish_rebuild_slug(
                        "minutes", built_minutes_index or "", slug, source
                    )
                ),
            )
            processed_offset += minutes_count
        if args.doc_type in {"all", "reiki"}:
            built_reiki_index = resume_index if mode == "resume" else f"miyabe-reiki-v{build_id}"
            reiki_count = build_one(
                client,
                index_name=built_reiki_index,
                documents=iter_reiki_documents(limit=args.limit, slugs=slugs, exclude_slugs=resume_done_slugs),
                shards=args.shards,
                replicas=args.replicas,
                bulk_size=bulk_size,
                bulk_bytes=bulk_bytes,
                bulk_concurrency=bulk_concurrency,
                create_index=mode != "resume",
                progress_callback=lambda total, source, slug_current: search_rebuild_status_progress(
                    status_state,
                    stage="reiki",
                    index_name=built_reiki_index or "",
                    processed_count=processed_offset + total,
                    source=source,
                    current_slug_processed_count=slug_current,
                    current_slug_total_count=reiki_counts_by_slug.get(str(source.get("slug") or "").strip(), 0),
                ),
                slug_complete_callback=(
                    None
                    if args.no_switch_alias
                    else lambda slug, source, _total: publish_rebuild_slug(
                        "reiki", built_reiki_index or "", slug, source
                    )
                ),
            )
            processed_offset += reiki_count

        # 0 件の索引に切り替えると、公開検索が丸ごと空になる。work の
        # マウント漏れやディレクトリ構成の変更で、正常終了したまま起きる。
        empty_builds = [
            name
            for name, count, index in (
                ("会議録", minutes_count, built_minutes_index),
                ("例規", reiki_count, built_reiki_index),
            )
            if index and count is not None and count <= 0
        ]
        unexplained_drops = source_integrity_failures()
        if unexplained_drops:
            # 候補indexを調査用に残す場合も、どの取得済みpathが落ちたかは
            # 省略しない。先頭数件だけでは後続の修復対象が失われるため全件出す。
            for failure in unexplained_drops:
                print(
                    "[DROP] "
                    f"doc_type={failure['doc_type']} slug={failure['slug']} "
                    f"path={failure['path']} reason={failure['reason']}",
                    file=sys.stderr,
                    flush=True,
                )
        if unexplained_drops and not args.no_switch_alias and not args.allow_partial_alias:
            print(
                f"[ERROR] {len(unexplained_drops)} 件の取得済みファイルを説明なく"
                "索引へ載せられませんでした。公開 alias は切り替えません。"
                "意図して公開する場合だけ --allow-partial-alias を付けてください。",
                file=sys.stderr,
                flush=True,
            )
            search_rebuild_status_finish(
                status_state,
                ok=False,
                message=f"説明不能な取得ファイルdrop {len(unexplained_drops)}件",
            )
            return 2

        if SKIPPED_SOURCES and not args.no_switch_alias and not args.allow_partial_alias:
            print(
                f"[ERROR] {len(SKIPPED_SOURCES)} 自治体を列挙できず索引から落としました。"
                "このまま公開に切り替えると、その自治体は検索できなくなります。"
                "意図しているなら --allow-partial-alias を付けてください。"
                " 例: " + " / ".join(SKIPPED_SOURCES[:3]),
                file=sys.stderr,
                flush=True,
            )
            search_rebuild_status_finish(
                status_state, ok=False, message=f"{len(SKIPPED_SOURCES)} 自治体を列挙できず"
            )
            return 2

        if empty_builds and not args.no_switch_alias and not args.allow_partial_alias:
            print(
                "[ERROR] " + "・".join(empty_builds) + "の索引が 0 件です。"
                "このまま公開に切り替えると検索が空になります。"
                "取得元のディレクトリを確認してください。"
                "意図しているなら --allow-partial-alias を付けてください。",
                file=sys.stderr,
                flush=True,
            )
            search_rebuild_status_finish(status_state, ok=False, message="索引が 0 件")
            return 2

        if not args.no_switch_alias:
            print("[ALIAS] atomic switch", flush=True)
            switch_aliases(
                client,
                minutes_index=built_minutes_index,
                reiki_index=built_reiki_index,
                minutes_alias=args.minutes_alias,
                reiki_alias=args.reiki_alias,
                documents_alias=args.documents_alias,
            )
            print(
                "[ALIAS] "
                f"{args.minutes_alias}={built_minutes_index or 'unchanged'} "
                f"{args.reiki_alias}={built_reiki_index or 'unchanged'} "
                f"{args.documents_alias}=combined",
                flush=True,
            )
    except Exception as exc:
        search_rebuild_status_finish(status_state, ok=False, message=str(exc))
        raise
    search_rebuild_status_finish(status_state, ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
