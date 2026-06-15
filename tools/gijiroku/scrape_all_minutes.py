#!/usr/bin/env python3
"""対応する全会議録スクレイパを束ねる batch runner。

ローカル実行と Docker scraper worker の両方で使う実行時入口。
調査 TSV から対象自治体を選び、system_type ごとの子スクレイパを起動する。
実行ループ本体（並列制御・state 記録・OpenSearch 増分更新）は
tools/tasks/batch.py に集約し、ここには会議録固有の判定だけを置く。
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
# Docker ではこの batch runner をファイルパス指定で実行する。
# package install なしでも共通 tools、会議録モジュール、隣接モジュールを import できるようにする。
import freshness_metadata
import gijiroku_targets
from tools.tasks import backfill as task_backfill
from tools.tasks import batch as scraping_batch
from tools.tasks import priority as scraping_priority


gijiroku_priority = scraping_priority.PriorityCalculator(
    "gijiroku",
    count_field="downloaded_count",
    extra_progress_reader=scraping_priority.scrape_state_progress,
)


SUPPORTED_SYSTEMS = {
    "gijiroku.com": "scrapers/gijiroku_com.py",
    "kaigiroku.net": "scrapers/kaigiroku_net.py",
    "dbsr": "scrapers/dbsr.py",
    "kensakusystem": "scrapers/kensakusystem.py",
    "kami-city-pdf": "scrapers/kami_city_pdf.py",
    "site-gikai-pdf": "scrapers/site_gikai_pdf.py",
    "static-kaigiroku-dir": "scrapers/static_kaigiroku_dir.py",
    "独自": "scrapers/gikai_pdf.py",
}
SUPPORTED_INPUT_SYSTEMS = set(SUPPORTED_SYSTEMS.keys()) | {"voices", "db-search", "kaigiroku-indexphp"}
SAVE_HTML_SYSTEMS = {
    "gijiroku.com",
    "dbsr",
    "kensakusystem",
    "kami-city-pdf",
    "site-gikai-pdf",
    "static-kaigiroku-dir",
}


# 一括会議録スクレイパの CLI オプションを定義する。
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="assembly_minutes_system_urls.tsv の対応済み system_type をまとめてスクレイピングします。"
    )
    parser.add_argument(
        "--ack-robots",
        action="store_true",
        help="robots.txt・利用規約・許諾確認済みとして実行する",
    )
    parser.add_argument(
        "--systems",
        default=",".join(SUPPORTED_SYSTEMS.keys()),
        help="対象 system_type のカンマ区切り一覧",
    )
    parser.add_argument(
        "--per-target-max-meetings",
        type=int,
        default=0,
        help="各自治体で処理する会議数上限（0 は無制限）",
    )
    parser.add_argument(
        "--per-target-max-years",
        type=int,
        default=0,
        help="kaigiroku.net 系の取得年数上限（0 は無制限）",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.5,
        help="各会議アクセス間の待機秒数",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=10_000,
        help="各スクレイパの操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--dbsr-discovery-timeout-seconds",
        type=int,
        default=900,
        help="DBSR 系の会議一覧収集に使う最大秒数（0 は無制限）",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="HTML 保存に対応する system_type で調査用 HTML を保存する",
    )
    parser.add_argument(
        "--save-debug-json",
        action="store_true",
        help="kaigiroku.net 系で調査用 JSON を保存する",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="ブラウザを表示して実行する",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="既存の保存結果を無視して最初から取り直す",
    )
    scraping_batch.add_common_arguments(parser)
    return parser


# --systems の指定を検証し、実行対象 system_type の一覧へ変換する。
def parse_requested_systems(value: str) -> list[str]:
    systems = [item.strip() for item in str(value).split(",") if item.strip()]
    if not systems:
        return list(SUPPORTED_SYSTEMS.keys())
    requested_systems: list[str] = []
    unsupported: list[str] = []
    for system in systems:
        if system not in SUPPORTED_INPUT_SYSTEMS:
            unsupported.append(system)
            continue
        if system not in requested_systems:
            requested_systems.append(system)
    if unsupported:
        raise ValueError(f"Unsupported system_type: {', '.join(unsupported)}")
    return requested_systems


# system_type に対応する個別会議録スクレイパのスクリプトパスを返す。
def child_script_path(system_type: str) -> str:
    system_family = gijiroku_targets.canonical_minutes_system_type(system_type)
    script_name = SUPPORTED_SYSTEMS[system_family]
    return str(Path("tools") / "gijiroku" / script_name)


# 自治体 1 件を取得するための子スクレイパ起動コマンドを作る。
def build_child_command(args: argparse.Namespace, target: dict) -> list[str]:
    system_type = str(target["system_type"])
    system_family = str(target.get("system_family", "")).strip() or gijiroku_targets.canonical_minutes_system_type(system_type)
    slug = str(target["slug"])
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            child_script_path(system_type),
            "--slug",
            slug,
            "--ack-robots",
            "--delay-seconds",
            str(args.delay_seconds),
            "--timeout-ms",
            str(args.timeout_ms),
        ]
    )
    if args.per_target_max_meetings > 0:
        cmd.extend(["--max-meetings", str(args.per_target_max_meetings)])
    if system_family == "dbsr":
        cmd.extend(["--discovery-timeout-seconds", str(args.dbsr_discovery_timeout_seconds)])
    if system_family == "kaigiroku.net" and args.per_target_max_years > 0:
        cmd.extend(["--max-years", str(args.per_target_max_years)])
    if args.save_html and system_family in SAVE_HTML_SYSTEMS:
        cmd.append("--save-html")
    if args.save_debug_json and system_family == "kaigiroku.net":
        cmd.append("--save-debug-json")
    if args.headful:
        cmd.append("--headful")
    if args.no_resume:
        cmd.append("--no-resume")
    return cmd


# 子スクレイパが進捗・完了判定を書く scrape_state.json のパスを返す。
def scrape_state_path(target: dict) -> Path:
    return Path(str(target.get("work_dir") or "")) / "scrape_state.json"


def classified_scrape_validation(target: dict) -> dict | None:
    """子スクレイパが残した分類済み完了判定を読む。"""
    try:
        payload = json.loads(scrape_state_path(target).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        return None
    if str(validation.get("mode") or "") != "classified_scrape_result":
        return None
    return validation


def validation_int(validation: dict, key: str) -> int:
    try:
        return max(0, int(validation.get(key) or 0))
    except Exception:
        return 0


def actual_scrape_progress(target: dict) -> tuple[int, int]:
    """保存済み成果物を数え、終了判定に使う実取得件数を返す。"""
    validation = classified_scrape_validation(target)
    if validation is not None:
        return (
            validation_int(validation, "progress_current"),
            validation_int(validation, "progress_total"),
        )

    downloads_dir = Path(str(target.get("downloads_dir") or ""))
    index_json_path = Path(str(target.get("index_json_path") or ""))
    indexed_total_count = task_backfill.load_gijiroku_index_unique_count(index_json_path)
    if indexed_total_count > 0:
        downloaded_count = task_backfill.count_gijiroku_indexed_downloads(index_json_path, downloads_dir)
    else:
        downloaded_count = task_backfill.count_gijiroku_downloads(downloads_dir)
    total_count = max(indexed_total_count, downloaded_count)
    return max(0, downloaded_count), max(0, total_count)


# 子スクレイパが returncode=0 でも、取得件数が完了していなければ成功扱いしない。
def scrape_completion_error(target: dict, progress: dict | None) -> str:
    validation = classified_scrape_validation(target)
    if validation is not None:
        discovered_count = validation_int(validation, "discovered_count")
        failed_count = validation_int(validation, "failed_count")
        unknown_missing_count = validation_int(validation, "unknown_missing_count")
        if discovered_count <= 0:
            return "取得対象件数が0件です"
        if failed_count > 0:
            return f"取得失敗: {failed_count}件"
        if unknown_missing_count > 0:
            return f"取得未確認: {unknown_missing_count}件"
        return ""

    current, total = actual_scrape_progress(target)
    if total <= 0 and isinstance(progress, dict):
        try:
            current = max(0, int(progress.get("progress_current") or 0))
            total = max(0, int(progress.get("progress_total") or 0))
        except Exception:
            return "取得件数を確認できません"
    if total <= 0:
        return "取得件数を確認できません"
    if current < total:
        return f"取得未完了: {current}/{total}"
    return ""


BATCH_SPEC = scraping_batch.BatchSpec(
    task_name="gijiroku",
    progress_unit="meeting",
    index_doc_type="minutes",
    batch_dir=gijiroku_targets.project_root() / "work" / "gijiroku" / "_minutes_batch",
    project_root=gijiroku_targets.project_root(),
    priority=gijiroku_priority,
    build_child_command=build_child_command,
    scrape_state_path=scrape_state_path,
    actual_scrape_progress=actual_scrape_progress,
    scrape_completion_error=scrape_completion_error,
    target_freshness=freshness_metadata.gijiroku_target_freshness,
)


# 会議録一括スクレイピングの入口。対象選定までを行い、実行ループは共通実装に任せる。
def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots and not args.list_targets:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。", flush=True)
        return 2
    error = scraping_batch.validate_common_args(args)
    if error:
        print(f"[ERROR] {error}", flush=True)
        return 2

    try:
        requested_systems = parse_requested_systems(args.systems)
    except ValueError as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 2

    targets = [
        freshness_metadata.attach_target_freshness("gijiroku", target)
        for target in gijiroku_targets.iter_gijiroku_targets()
        if (
            str(target.get("system_type", "")).strip() in requested_systems
            or (
                str(target.get("system_family", "")).strip()
                or gijiroku_targets.canonical_minutes_system_type(str(target.get("system_type", "")))
            )
            in requested_systems
        )
    ]

    return scraping_batch.run_batch(BATCH_SPEC, args, targets)


if __name__ == "__main__":
    raise SystemExit(main())
