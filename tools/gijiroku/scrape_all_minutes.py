#!/usr/bin/env python3
"""対応する全会議録スクレイパを束ねる batch runner。

ローカル実行と Docker scraper worker の両方で使う実行時入口。
調査 TSV から対象自治体を選び、system_type ごとの子スクレイパを起動し、
background task status を記録し、自治体ごとの OpenSearch 更新をキューへ積む。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
# Docker ではこの batch runner をファイルパス指定で実行する。
# package install なしでも共通 tools、会議録モジュール、隣接モジュールを import できるようにする。
import freshness_metadata
from tools.tasks import status as batch_status
from tools.tasks import backfill as task_backfill
from tools.tasks import priority as scraping_priority
from tools.tasks.runner import (
    close_worker_streams,
    count_active_by_host,
    extract_warning_lines,
    scrape_state_warning_lines,
    extract_worker_progress_from_log as common_extract_worker_progress_from_log,
    extract_worker_progress_from_state as common_extract_worker_progress_from_state,
    install_stop_signal_handlers,
    now_ts,
    PriorityTargetQueue,
    process_group_popen_kwargs,
    summarize_worker,
    tail_text_lines,
    target_host,
    target_matches,
    terminate_process_group,
)
import build_locks
import gijiroku_targets


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
}
SUPPORTED_INPUT_SYSTEMS = set(SUPPORTED_SYSTEMS.keys()) | {"voices", "db-search", "kaigiroku-indexphp"}
# 子スクレイパ標準出力の [PROGRESS] 行だけを拾い、自治体単位の current/total へ反映する。
PROGRESS_RE = re.compile(
    r"^\[PROGRESS\]\s+unit=(?P<unit>[a-z_]+)\s+current=(?P<current>\d+)\s+total=(?P<total>\d+)\s*$"
)
INDEX_BULK_RE = re.compile(r"^\[BULK\]\s+.*\btotal=(?P<total>\d+)\b")
INDEX_DONE_RE = re.compile(r"^\[DONE\]\s+.*\bcount=(?P<count>\d+)\b")


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
        "--max-targets",
        type=int,
        default=0,
        help="処理する自治体数の上限（0 は無制限）",
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
    parser.add_argument(
        "--parallel",
        type=int,
        default=6,
        help="同時に走らせる自治体数",
    )
    parser.add_argument(
        "--index-parallel",
        type=int,
        default=1,
        help="同時に走らせる OpenSearch 自治体別増分更新数",
    )
    parser.add_argument(
        "--per-host-parallel",
        type=int,
        default=1,
        help="同一ホストに対して同時に走らせる自治体数",
    )
    parser.add_argument(
        "--per-host-start-interval",
        type=float,
        default=2.0,
        help="同一ホストで次の自治体を起動するまでの最小待機秒数",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=5.0,
        help="進捗表示の更新間隔（秒）",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="slug / code / name / full_name に部分一致する自治体だけを対象にする",
    )
    parser.add_argument(
        "--python-command",
        default=sys.executable,
        help="子スクレイパ起動に使う Python コマンド",
    )
    parser.add_argument(
        "--no-build-index",
        action="store_true",
        help="スクレイプ完了後の OpenSearch 自治体別増分更新を行わない",
    )
    parser.add_argument(
        "--index-dispatch",
        choices=["inline", "celery"],
        default="inline",
        help="OpenSearch 増分更新の実行方法。remote では celery でスクレイピングと分離する。",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="対象自治体一覧だけ表示して終了する",
    )
    return parser


# 会議録バッチの CSV とログを置く作業ディレクトリを返す。
def batch_dir() -> Path:
    return gijiroku_targets.project_root() / "work" / "gijiroku" / "_minutes_batch"


# run_id に対応する集計 CSV の保存先を返す。
def summary_output_path(run_id: str) -> Path:
    return batch_dir() / f"run_{run_id}.csv"


# run_id に対応する子プロセスログディレクトリを返す。
def logs_dir(run_id: str) -> Path:
    return batch_dir() / f"logs_{run_id}"


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
    if system_family == "kaigiroku.net" and args.per_target_max_years > 0:
        cmd.extend(["--max-years", str(args.per_target_max_years)])
    if args.save_html and system_family in {
        "gijiroku.com",
        "dbsr",
        "kensakusystem",
        "kami-city-pdf",
        "site-gikai-pdf",
        "static-kaigiroku-dir",
    }:
        cmd.append("--save-html")
    if args.save_debug_json and system_family == "kaigiroku.net":
        cmd.append("--save-debug-json")
    if args.headful:
        cmd.append("--headful")
    if args.no_resume:
        cmd.append("--no-resume")
    return cmd


# 旧互換名。ログから子スクレイパの進捗を取り出す。
def extract_worker_progress(stdout_path: Path) -> dict[str, object] | None:
    return extract_worker_progress_from_log(stdout_path)


# scrape_state.json から会議録取得の進捗を取り出す。
def extract_worker_progress_from_state(state_path: Path) -> dict[str, object] | None:
    return common_extract_worker_progress_from_state(state_path, default_unit="meeting")


# 子スクレイパの [PROGRESS] ログから会議録取得の進捗を取り出す。
def extract_worker_progress_from_log(stdout_path: Path) -> dict[str, object] | None:
    return common_extract_worker_progress_from_log(stdout_path, PROGRESS_RE)


# 画面表示用に、state 優先・ログ補助で worker の進捗を取り出す。
def extract_worker_progress_for_display(worker: dict) -> dict[str, object] | None:
    state_path = worker.get("state_path")
    if isinstance(state_path, Path):
        progress = extract_worker_progress_from_state(state_path)
        if progress is not None:
            return progress
    return extract_worker_progress_from_log(worker["stdout_path"])


def classified_scrape_validation(target: dict[str, object]) -> dict[str, object] | None:
    """子スクレイパが残した分類済み完了判定を読む。"""
    state_path = Path(str(target.get("work_dir") or "")) / "scrape_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
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


def validation_int(validation: dict[str, object], key: str) -> int:
    try:
        return max(0, int(validation.get(key) or 0))
    except Exception:
        return 0


def actual_scrape_progress(target: dict[str, object]) -> tuple[int, int]:
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
def scrape_completion_error(target: dict[str, object], progress: dict[str, object] | None) -> str:
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
    elif total <= 0:
        return "取得件数を確認できません"

    if total <= 0:
        return "取得対象件数が0件です"
    if current < total:
        return f"取得未完了: {current}/{total}"
    return ""


def remove_stale_scrape_state(state_path: Path) -> None:
    """前回実行の scrape_state.json が今回の進捗として読まれないようにする。"""
    try:
        state_path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        return


def preserve_previous_failed_items(status_state: dict[str, object], task_name: str) -> None:
    """今回の実行対象から外した失敗済み item を main state に残す。"""
    items = status_state.setdefault("items", {})
    if not isinstance(items, dict):
        return
    for slug, item in task_backfill.previous_failed_items(task_name).items():
        if slug not in items:
            items[slug] = item
    batch_status.refresh_counts(status_state)


# index 更新の進捗母数として、直前のスクレイピング総件数を返す。
def index_worker_total(index_worker: dict) -> int:
    scrape_worker = index_worker.get("scrape_worker")
    if not isinstance(scrape_worker, dict):
        return 0
    target = scrape_worker.get("target")
    if isinstance(target, dict):
        _, actual_total = actual_scrape_progress(target)
        if actual_total > 0:
            return actual_total
    progress = extract_worker_progress_for_display(scrape_worker)
    if not isinstance(progress, dict):
        return 0
    return max(0, int(progress.get("progress_total") or progress.get("progress_current") or 0))


# OpenSearch 更新ログから、document 追加済み件数を表示用進捗に変換する。
def extract_index_progress_for_display(index_worker: dict) -> dict[str, object] | None:
    current: int | None = None
    for line in reversed(tail_text_lines(index_worker["stdout_path"], max_bytes=16_384)):
        stripped = line.strip()
        done_match = INDEX_DONE_RE.match(stripped)
        if done_match:
            current = int(done_match.group("count"))
            break
        bulk_match = INDEX_BULK_RE.match(stripped)
        if bulk_match:
            current = int(bulk_match.group("total"))
            break

    total = index_worker_total(index_worker)
    if current is None and total <= 0:
        return None
    current = max(0, int(current or 0))
    if total <= 0:
        total = current
    return {
        "progress_current": min(current, total) if total > 0 else current,
        "progress_total": total,
        "progress_unit": "document",
    }


# index worker のログから、画面に出す短い作業メッセージを作る。
def summarize_index_worker(index_worker: dict) -> str:
    stderr_path = index_worker["stderr_path"]
    if stderr_path.exists() and stderr_path.stat().st_size > 0:
        return f"エラー出力あり {stderr_path.stat().st_size}バイト"

    progress = extract_index_progress_for_display(index_worker)
    if progress is not None:
        current = int(progress["progress_current"])
        total = int(progress["progress_total"])
        if total > 0 and current >= total:
            return "検索可能状態へ反映中"
        return "インデックスへ追加中"

    for line in reversed(tail_text_lines(index_worker["stdout_path"], max_bytes=16_384)):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[DELETE] "):
            return "既存インデックスを整理中"
        if stripped.startswith("[PUBLISH] "):
            return "検索可能状態へ反映中"
        if stripped.startswith("[DONE] "):
            return "インデックス更新完了"
        if stripped.startswith("[BULK] "):
            return "インデックスへ追加中"
        if stripped.startswith("[INFO] "):
            return stripped[7:].strip() or "インデックス更新中"
        return "インデックス更新中"
    return "インデックス更新を起動中"


# 自治体 1 件の会議録スクレイピング子プロセスを起動し、worker 辞書を返す。
def launch_worker(
    target: dict,
    seq: int,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict:
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.log"
    stderr_path = run_logs_dir / f"{slug}.err.log"
    state_path = Path(target["work_dir"]) / "scrape_state.json"
    remove_stale_scrape_state(state_path)
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")

    process = subprocess.Popen(
        build_child_command(args, target),
        cwd=str(gijiroku_targets.project_root()),
        stdout=stdout_handle,
        stderr=stderr_handle,
        **process_group_popen_kwargs(),
    )

    return {
        "seq": seq,
        "target": target,
        "host": target_host(target),
        "process": process,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "stdout_handle": stdout_handle,
        "stderr_handle": stderr_handle,
        "started_at": batch_status.now_text(),
        "state_path": state_path,
    }


# 自治体 1 件分の会議録 OpenSearch index 更新コマンドを作る。
def build_index_command(args: argparse.Namespace, target: dict) -> list[str]:
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            str(Path("tools") / "search" / "build_opensearch_index.py"),
            "--mode",
            "update",
            "--doc-type",
            "minutes",
            "--slug",
            str(target["slug"]),
        ]
    )
    return cmd


# Celery の会議録 index キューへ自治体別更新タスクを投入する。
def enqueue_index_update_task(target: dict) -> str:
    from deploy.scraper_runtime.celery.app import app, GIJIROKU_INDEX_QUEUE

    result = app.send_task(
        "deploy.scraper_runtime.celery.tasks.run_gijiroku_index_update",
        kwargs={"slug": str(target["slug"])},
        queue=GIJIROKU_INDEX_QUEUE,
    )
    return str(result.id)


# inline index 更新待ちにするため、完了済み scrape worker を index キュー項目へ包む。
def queue_index_worker(scrape_worker: dict, scrape_returncode: int) -> dict:
    return {
        "target": scrape_worker["target"],
        "host": str(scrape_worker["host"]),
        "scrape_worker": scrape_worker,
        "scrape_returncode": int(scrape_returncode),
        "queued_at": batch_status.now_text(),
        "deadline_at": time.time() + 600.0,
    }


# inline 実行用の index worker を起動する。ロック取得できなければ None を返す。
def launch_index_worker(
    queued_worker: dict,
    *,
    args: argparse.Namespace,
    run_logs_dir: Path,
) -> dict | None:
    target = queued_worker["target"]
    slug = str(target["slug"])
    stdout_path = run_logs_dir / f"{slug}.index.log"
    stderr_path = run_logs_dir / f"{slug}.index.err.log"
    lock_path = build_locks.acquire_build_lock(
        slug,
        owner="scrape_all_minutes",
        wait_seconds=0.0,
    )
    if lock_path is None:
        return None

    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")
    try:
        process = subprocess.Popen(
            build_index_command(args, target),
            cwd=str(gijiroku_targets.project_root()),
            stdout=stdout_handle,
            stderr=stderr_handle,
            **process_group_popen_kwargs(),
        )
        return {
            "target": target,
            "host": str(queued_worker["host"]),
            "scrape_worker": queued_worker["scrape_worker"],
            "scrape_returncode": int(queued_worker["scrape_returncode"]),
            "process": process,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "stdout_handle": stdout_handle,
            "stderr_handle": stderr_handle,
            "started_at": batch_status.now_text(),
            "lock_path": lock_path,
        }
    except Exception:
        try:
            stdout_handle.close()
        except Exception:
            pass
        try:
            stderr_handle.close()
        except Exception:
            pass
        build_locks.release_build_lock(lock_path)
        raise
 

# index worker のログを閉じ、取得していた build lock を解放する。
def close_index_worker(index_worker: dict) -> None:
    close_worker_streams(index_worker)
    lock_path = index_worker.get("lock_path")
    if isinstance(lock_path, Path):
        build_locks.release_build_lock(lock_path)
        index_worker["lock_path"] = None


# 他プロセスが index 更新中でスキップした場合も、scrape 成功として結果に記録する。
def record_busy_index_result(
    writer,
    handle,
    *,
    status_state: dict,
    task_name: str,
    queued_worker: dict,
    run_logs_dir: Path,
) -> None:
    target = queued_worker["target"]
    scrape_worker = queued_worker["scrape_worker"]
    progress = extract_worker_progress_for_display(scrape_worker)
    stdout_path = run_logs_dir / f"{target['slug']}.index.log"
    stderr_path = run_logs_dir / f"{target['slug']}.index.err.log"
    stdout_path.write_text(
        "別プロセスが検索 index を更新中のため、このバッチからのインデックス更新はスキップしました。\n",
        encoding="utf-8",
    )
    stderr_path.write_text("", encoding="utf-8")
    summary = (
        f"{summarize_worker(scrape_worker['stdout_path'], scrape_worker['stderr_path'])} / "
        "別プロセスが検索 index を更新中のため、このバッチからのインデックス更新はスキップしました"
    )
    finished_at = batch_status.now_text()
    record_target_result(
        writer,
        handle,
        status_state=status_state,
        task_name=task_name,
        target=target,
        host=str(queued_worker["host"]),
        overall_status="ok",
        overall_returncode=int(queued_worker["scrape_returncode"]),
        scrape_returncode=int(queued_worker["scrape_returncode"]),
        index_status="busy",
        index_returncode=0,
        started_at=str(scrape_worker["started_at"]),
        finished_at=finished_at,
        stdout_log=str(scrape_worker["stdout_path"]),
        stderr_log=str(scrape_worker["stderr_path"]),
        index_stdout_log=str(stdout_path),
        index_stderr_log=str(stderr_path),
        message=summary,
        progress=progress,
    )
    batch_status.invalidate_runtime_caches()


# コンソール向けに、現在稼働中の scrape/index worker 一覧を出力する。
def print_status(
    active_workers: list[dict],
    active_index_workers: list[dict],
    queued_index_count: int,
    completed_count: int,
    total_count: int,
) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(
        f"[STATUS {stamp}] completed {completed_count}/{total_count}, "
        f"scrape_active {len(active_workers)}, index_active {len(active_index_workers)}, "
        f"index_queued {queued_index_count}",
        flush=True,
    )
    for worker in active_workers:
        target = worker["target"]
        summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
        print(
            f"  - [scrape] {target['slug']} [{target['system_type']}] {worker['host']} "
            f"pid={worker['process'].pid}: {summary}",
            flush=True,
        )
    for worker in active_index_workers:
        target = worker["target"]
        summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
        print(
            f"  - [index] {target['slug']} [{target['system_type']}] {worker['host']} "
            f"pid={worker['process'].pid}: {summary}",
            flush=True,
        )

# 稼働中 worker の進捗を state に反映し、heartbeat を更新する。
def refresh_active_worker_heartbeats(
    status_state: dict,
    task_name: str,
    active_workers: list[dict],
    active_index_workers: list[dict],
    pending_index_workers: list[dict],
    *,
    worker_capacity: int,
    index_capacity: int,
    per_host_capacity: int,
) -> None:
    if not active_workers and not active_index_workers:
        return
    for worker in active_workers:
        target = worker["target"]
        update_kwargs = {
            "message": summarize_worker(worker["stdout_path"], worker["stderr_path"]),
        }
        progress = extract_worker_progress_for_display(worker)
        if progress is not None:
            update_kwargs.update(progress)
        batch_status.update_item(
            status_state,
            str(target["slug"]),
            **update_kwargs,
        )
    for worker in active_index_workers:
        target = worker["target"]
        update_kwargs = {
            "message": summarize_index_worker(worker),
        }
        progress = extract_index_progress_for_display(worker)
        if progress is not None:
            update_kwargs.update(progress)
        batch_status.update_item(
            status_state,
            str(target["slug"]),
            **update_kwargs,
        )
    batch_status.update_runtime_metrics(
        status_state,
        running_label="スクレイピング中",
        worker_capacity=worker_capacity,
        worker_active_count=len(active_workers),
        index_capacity=index_capacity,
        index_active_count=len(active_index_workers),
        index_queue_count=len(pending_index_workers),
        per_host_capacity=per_host_capacity,
    )
    batch_status.write_state(task_name, status_state)


# 自治体 1 件の最終結果を CSV と task state に書き込む。
def record_target_result(
    writer,
    handle,
    *,
    status_state: dict,
    task_name: str,
    target: dict,
    host: str,
    overall_status: str,
    overall_returncode: int,
    scrape_returncode: int,
    index_status: str,
    index_returncode: int | str,
    started_at: str,
    finished_at: str,
    stdout_log: str,
    stderr_log: str,
    index_stdout_log: str,
    index_stderr_log: str,
    message: str,
    progress: dict[str, object] | None = None,
) -> None:
    # 1 自治体の終了結果を CSV と background_tasks JSON の両方へ反映する。
    # ここで警告・取得件数・鮮度メタデータをまとめ、画面表示の元データを確定する。
    writer.writerow(
        {
            "slug": str(target["slug"]),
            "code": str(target["code"]),
            "name": str(target["name"]),
            "system_type": str(target["system_type"]),
            "host": host,
            "source_url": str(target["source_url"]),
            "status": overall_status,
            "returncode": overall_returncode,
            "scrape_returncode": scrape_returncode,
            "index_status": index_status,
            "index_returncode": index_returncode,
            "started_at": started_at,
            "finished_at": finished_at,
            "stdout_log": stdout_log,
            "stderr_log": stderr_log,
            "index_stdout_log": index_stdout_log,
            "index_stderr_log": index_stderr_log,
        }
    )
    handle.flush()
    update_kwargs = {
        "status": overall_status,
        "message": message,
        "finished_at": finished_at,
        "returncode": int(overall_returncode),
    }
    if started_at:
        update_kwargs["started_at"] = started_at
    if progress is not None:
        update_kwargs.update(progress)
    warning_lines = extract_warning_lines(
        Path(stderr_log),
        Path(index_stderr_log),
        Path(stdout_log),
        Path(index_stdout_log),
    )
    for line in scrape_state_warning_lines(Path(target["work_dir"]) / "scrape_state.json"):
        if line not in warning_lines:
            warning_lines.append(line)
    latest_freshness = (
        freshness_metadata.gijiroku_target_freshness(target)
        if overall_status == "ok"
        else {"freshness_date": str(target.get("freshness_date", "") or ""), "freshness_basis": str(target.get("freshness_basis", "") or "")}
    )
    update_kwargs["extra_fields"] = {
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "index_stdout_log": index_stdout_log,
        "index_stderr_log": index_stderr_log,
        "scrape_returncode": int(scrape_returncode),
        "index_status": str(index_status),
        "index_returncode": index_returncode,
        "warning_count": len(warning_lines),
        "warning_lines": warning_lines,
        "freshness_date": latest_freshness["freshness_date"],
        "freshness_basis": latest_freshness["freshness_basis"],
    }
    if overall_status == "ok":
        update_kwargs["extra_fields"]["last_checked_at"] = finished_at
    batch_status.update_item(
        status_state,
        str(target["slug"]),
        **update_kwargs,
    )
    batch_status.write_state(task_name, status_state)
    if overall_status == "ok" and progress is not None:
        sync_success_snapshot(
            task_name=task_name,
            target=target,
            host=host,
            finished_at=finished_at,
            message=message,
            progress=progress,
            extra_fields=update_kwargs["extra_fields"],
        )


# 成功結果を snapshot state に保存し、次回の優先度計算で参照できるようにする。
def sync_success_snapshot(
    *,
    task_name: str,
    target: dict,
    host: str,
    finished_at: str,
    message: str,
    progress: dict[str, object],
    extra_fields: dict[str, object],
) -> None:
    # 成功した自治体だけを snapshot に残す。
    # 次回バッチが途中で止まっても、前回成功した取得件数を優先度計算に使えるようにする。
    snapshot_task = f"{task_name}_snapshot"
    snapshot_state = batch_status.read_state(snapshot_task)
    if not isinstance(snapshot_state.get("items"), dict):
        snapshot_state = batch_status.build_state(
            snapshot_task,
            run_id=f"incremental_{now_ts()}",
            total_count=0,
            summary_path=Path(""),
            log_dir=Path(""),
        )
        snapshot_state["running"] = False
        snapshot_state["started_at"] = ""
        snapshot_state["finished_at"] = ""
        snapshot_state["last_finished_at"] = ""

    slug = str(target["slug"])
    if slug not in snapshot_state.setdefault("items", {}):
        batch_status.register_target(snapshot_state, target, host)

    batch_status.update_item(
        snapshot_state,
        slug,
        status="snapshot",
        message="前回成功結果",
        finished_at=finished_at,
        returncode=0,
        progress_current=progress.get("progress_current"),
        progress_total=progress.get("progress_total"),
        progress_unit=str(progress.get("progress_unit") or "meeting"),
        extra_fields=extra_fields,
    )
    snapshot_state["running"] = False
    snapshot_state["active_count"] = 0
    snapshot_state["finished_at"] = finished_at
    snapshot_state["last_finished_at"] = finished_at
    batch_status.write_state(snapshot_task, snapshot_state)


# 実行せず、対象自治体と優先度情報だけを標準出力へ一覧表示する。
def list_targets(targets: list[dict]) -> None:
    print(f"[INFO] 対象自治体数: {len(targets)}")
    for target in targets:
        priority = gijiroku_priority.target_priority_info(target)
        print(
            f"{target['slug']}\t{target['code']}\tscore={priority['priority_score']}\t{priority['priority_label']}\t"
            f"{priority['current_count']}/{priority['total_count']}\t{target['system_type']}\t"
            f"fresh={priority.get('freshness_date', '') or '-'}\tchecked={priority.get('last_checked_at', '') or '-'}\t"
            f"{target_host(target)}\t{target['name']}\t{target['source_url']}"
        )


# score が 0 より大きい自治体だけを抽出し、優先度順に並べる。
def select_runnable_targets(targets: list[dict]) -> list[dict]:
    skipped_zero_score = 0
    label_counts: dict[str, int] = {}
    runnable_targets: list[dict] = []
    for target in targets:
        try:
            priority = gijiroku_priority.target_priority_info(target)
        except Exception:
            priority = {"priority_score": 2_000_000_000, "priority_label": "unknown_total"}
        label = str(priority.get("priority_label") or "unknown")
        label_counts[label] = label_counts.get(label, 0) + 1
        if int(priority.get("priority_score") or 0) <= 0:
            skipped_zero_score += 1
            continue
        runnable_targets.append(target)

    priority_summary = " ".join(f"{label}={count}" for label, count in sorted(label_counts.items()))

    print(
        "[INFO] 実行対象を priority queue で選定しました: "
        f"{priority_summary} skip_score_zero={skipped_zero_score}",
        flush=True,
    )
    return gijiroku_priority.sort_targets_by_priority(runnable_targets)


# 会議録一括スクレイピング全体の制御ループ。
def main() -> int:
    args = build_parser().parse_args()
    stop_controller = install_stop_signal_handlers()
    if not args.ack_robots and not args.list_targets:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。", flush=True)
        return 2
    if args.parallel < 1:
        print("[ERROR] --parallel は 1 以上を指定してください。", flush=True)
        return 2
    if args.index_parallel < 1:
        print("[ERROR] --index-parallel は 1 以上を指定してください。", flush=True)
        return 2
    if args.per_host_parallel < 1:
        print("[ERROR] --per-host-parallel は 1 以上を指定してください。", flush=True)
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

    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword, extra_fields=("system_type",))]

    targets = select_runnable_targets(targets)
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if not targets:
        print("[INFO] 対象自治体がありません。", flush=True)
        return 0

    if args.list_targets:
        list_targets(targets)
        return 0

    run_id = now_ts()
    batch_root = batch_dir()
    summary_path = summary_output_path(run_id)
    run_logs_dir = logs_dir(run_id)
    batch_root.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 対象自治体数: {len(targets)}", flush=True)
    print(f"[INFO] 並列数: {args.parallel}", flush=True)
    print(
        f"[INFO] OpenSearch 自治体別増分更新: {'無効' if args.no_build_index else '有効'} "
        f"(並列数 {args.index_parallel})",
        flush=True,
    )
    print(f"[INFO] ホストごとの並列数: {args.per_host_parallel}", flush=True)
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)
    print(f"[INFO] ログディレクトリ: {run_logs_dir}", flush=True)

    status_state = batch_status.build_state("gijiroku", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, target_host(target))
    preserve_previous_failed_items(status_state, "gijiroku")

    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slug",
                "code",
                "name",
                "system_type",
                "host",
                "source_url",
                "status",
                "returncode",
                "scrape_returncode",
                "index_status",
                "index_returncode",
                "started_at",
                "finished_at",
                "stdout_log",
                "stderr_log",
                "index_stdout_log",
                "index_stderr_log",
            ],
        )
        writer.writeheader()

        # 実行順はここで一度だけ priority queue に載せ、以後は先頭から取り出す。
        pending_targets = PriorityTargetQueue(targets, gijiroku_priority.priority_queue_key)
        active_workers: list[dict] = []
        pending_index_workers: list[dict] = []
        active_index_workers: list[dict] = []
        completed_count = 0
        launched_count = 0
        last_status_at = 0.0
        host_last_start_at: dict[str, float] = {}
        shutdown_started = False

        # スクレイピング枠と index 枠を分けて state に書き、画面でも別々に見せる。
        def write_status_state() -> None:
            batch_status.update_runtime_metrics(
                status_state,
                running_label="スクレイピング中",
                worker_capacity=args.parallel,
                worker_active_count=len(active_workers),
                index_capacity=args.index_parallel,
                index_active_count=len(active_index_workers),
                index_queue_count=len(pending_index_workers),
                per_host_capacity=args.per_host_parallel,
            )
            batch_status.write_state("gijiroku", status_state)

        write_status_state()

        while pending_targets or active_workers or pending_index_workers or active_index_workers:
            now = time.time()
            made_progress = False
            completed_workers: list[tuple[dict, int]] = []
            completed_index_workers: list[tuple[dict, int]] = []
            still_running: list[dict] = []
            still_index_running: list[dict] = []

            if stop_controller.should_stop() and not shutdown_started:
                shutdown_started = True
                print("[STOP] 停止シグナルを受信しました。新規起動を止め、実行中の子プロセスを終了します。", flush=True)
                finished_at = batch_status.now_text()
                for target in pending_targets.remaining_targets():
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="failed",
                        message="停止により未実行",
                        finished_at=finished_at,
                        returncode=stop_controller.returncode(),
                    )
                pending_targets.clear()
                for queued_worker in pending_index_workers:
                    target = queued_worker["target"]
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="failed",
                        message="停止によりインデックス更新を中断",
                        finished_at=finished_at,
                        returncode=stop_controller.returncode(),
                    )
                pending_index_workers.clear()
                write_status_state()
                for worker in active_index_workers:
                    terminate_process_group(worker["process"])
                for worker in active_workers:
                    terminate_process_group(worker["process"])
                made_progress = True

            for worker in active_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_running.append(worker)
                    continue
                close_worker_streams(worker)
                completed_workers.append((worker, int(returncode)))

            active_workers = still_running
            if completed_workers:
                made_progress = True

            for worker in active_index_workers:
                returncode = worker["process"].poll()
                if returncode is None:
                    still_index_running.append(worker)
                    continue
                close_index_worker(worker)
                completed_index_workers.append((worker, int(returncode)))

            active_index_workers = still_index_running
            if completed_index_workers:
                made_progress = True

            for worker, returncode in completed_workers:
                target = worker["target"]
                summary = summarize_worker(worker["stdout_path"], worker["stderr_path"])
                progress = extract_worker_progress_for_display(worker)
                validation_error = scrape_completion_error(target, progress) if returncode == 0 else ""
                scrape_status = "ok" if returncode == 0 and validation_error == "" else "failed"
                overall_status = scrape_status
                overall_returncode = int(returncode if validation_error == "" else -1)
                if validation_error:
                    summary = f"{summary} / {validation_error}"
                index_status = "skipped"
                index_returncode = ""
                index_stdout_log = ""
                index_stderr_log = ""

                if scrape_status == "ok" and not args.no_build_index and args.index_dispatch == "celery":
                    finished_at = batch_status.now_text()
                    try:
                        index_task_id = enqueue_index_update_task(target)
                        index_status = "queued"
                        index_message = f"{summary} / インデックス更新を別キューへ投入"
                        index_returncode = ""
                    except Exception as exc:
                        index_task_id = ""
                        index_status = "failed"
                        index_message = f"{summary} / インデックス更新投入失敗: {exc}"
                        index_returncode = -1
                    record_target_result(
                        writer,
                        handle,
                        status_state=status_state,
                        task_name="gijiroku",
                        target=target,
                        host=str(worker["host"]),
                        overall_status=overall_status,
                        overall_returncode=overall_returncode,
                        scrape_returncode=int(returncode),
                        index_status=index_status,
                        index_returncode=index_returncode,
                        started_at=str(worker["started_at"]),
                        finished_at=finished_at,
                        stdout_log=str(worker["stdout_path"]),
                        stderr_log=str(worker["stderr_path"]),
                        index_stdout_log="",
                        index_stderr_log="",
                        message=index_message,
                        progress=progress,
                    )
                    batch_status.invalidate_runtime_caches()
                    completed_count += 1
                    print(
                        f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                        f"[{target['system_type']}] returncode={overall_returncode} {summary} "
                        f"/ index_task={index_task_id or index_status}",
                        flush=True,
                    )
                    made_progress = True
                    continue

                if scrape_status == "ok" and not args.no_build_index:
                    pending_index_workers.append(queue_index_worker(worker, returncode))
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="running",
                        message="インデックス待機中",
                        # 子スクレイパの取得件数は完了済みなので、ここからは
                        # 「最後の 100% で止まった」ように見えないよう一旦外す。
                        progress_current=None,
                        progress_total=None,
                        progress_unit="",
                    )
                    write_status_state()
                    print(f"[INDEX-QUEUE] {target['slug']} 検索 index 更新を待機キューへ追加", flush=True)
                    made_progress = True
                    continue

                finished_at = batch_status.now_text()
                record_target_result(
                    writer,
                    handle,
                    status_state=status_state,
                    task_name="gijiroku",
                    target=target,
                    host=str(worker["host"]),
                    overall_status=overall_status,
                    overall_returncode=overall_returncode,
                    scrape_returncode=int(returncode),
                    index_status=index_status,
                    index_returncode=index_returncode,
                    started_at=str(worker["started_at"]),
                    finished_at=finished_at,
                    stdout_log=str(worker["stdout_path"]),
                    stderr_log=str(worker["stderr_path"]),
                    index_stdout_log=index_stdout_log,
                    index_stderr_log=index_stderr_log,
                    message=summary,
                    progress=progress,
                )
                if overall_returncode == 0 and args.no_build_index:
                    batch_status.invalidate_runtime_caches()
                completed_count += 1
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"[{target['system_type']}] returncode={overall_returncode} {summary}",
                    flush=True,
                )
                if overall_returncode != 0:
                    print(f"[WARN] {target['slug']} は returncode={overall_returncode} で終了しました。", flush=True)

            for index_worker, returncode in completed_index_workers:
                target = index_worker["target"]
                scrape_worker = index_worker["scrape_worker"]
                scrape_summary = summarize_worker(scrape_worker["stdout_path"], scrape_worker["stderr_path"])
                index_summary = summarize_worker(index_worker["stdout_path"], index_worker["stderr_path"])
                overall_status = "ok" if returncode == 0 else "failed"
                overall_returncode = int(index_worker["scrape_returncode"] if returncode == 0 else returncode)
                progress = extract_worker_progress_for_display(scrape_worker)
                finished_at = batch_status.now_text()
                status_state["index_finished_at"] = finished_at
                record_target_result(
                    writer,
                    handle,
                    status_state=status_state,
                    task_name="gijiroku",
                    target=target,
                    host=str(index_worker["host"]),
                    overall_status=overall_status,
                    overall_returncode=overall_returncode,
                    scrape_returncode=int(index_worker["scrape_returncode"]),
                    index_status="ok" if returncode == 0 else "failed",
                    index_returncode=int(returncode),
                    started_at=str(scrape_worker["started_at"]),
                    finished_at=finished_at,
                    stdout_log=str(scrape_worker["stdout_path"]),
                    stderr_log=str(scrape_worker["stderr_path"]),
                    index_stdout_log=str(index_worker["stdout_path"]),
                    index_stderr_log=str(index_worker["stderr_path"]),
                    message=f"{scrape_summary} / {index_summary}",
                    progress=progress,
                )
                if returncode == 0:
                    batch_status.invalidate_runtime_caches()
                completed_count += 1
                print(
                    f"[DONE {completed_count}/{len(targets)}] {target['slug']} "
                    f"[{target['system_type']}] returncode={overall_returncode} {scrape_summary} / {index_summary}",
                    flush=True,
                )
                if returncode != 0:
                    print(f"[WARN] {target['slug']} のインデックス更新は returncode={returncode} で終了しました。", flush=True)

            host_active_counts = count_active_by_host(active_workers)
            while pending_targets and len(active_workers) < args.parallel and not shutdown_started:
                # 同一ホストへの同時接続数と起動間隔だけを実行直前に判定する。
                # 優先度そのものはキュー内で維持される。
                def can_launch_target(target: dict) -> bool:
                    host = target_host(target)
                    if host_active_counts.get(host, 0) >= args.per_host_parallel:
                        return False
                    last_started_at = host_last_start_at.get(host, 0.0)
                    if last_started_at and now - last_started_at < args.per_host_start_interval:
                        return False
                    return True

                target = pending_targets.pop_runnable(can_launch_target)
                if target is None:
                    break

                host = target_host(target)
                launched_count += 1
                try:
                    worker = launch_worker(target, launched_count, args=args, run_logs_dir=run_logs_dir)
                except Exception as exc:
                    finished_at = batch_status.now_text()
                    stdout_path = run_logs_dir / f"{target['slug']}.log"
                    stderr_path = run_logs_dir / f"{target['slug']}.err.log"
                    error_message = f"起動失敗: {exc}"
                    try:
                        stderr_path.write_text(error_message + "\n", encoding="utf-8")
                    except Exception:
                        pass
                    record_target_result(
                        writer,
                        handle,
                        status_state=status_state,
                        task_name="gijiroku",
                        target=target,
                        host=host,
                        overall_status="failed",
                        overall_returncode=-1,
                        scrape_returncode=-1,
                        index_status="skipped",
                        index_returncode="",
                        started_at="",
                        finished_at=finished_at,
                        stdout_log=str(stdout_path),
                        stderr_log=str(stderr_path),
                        index_stdout_log="",
                        index_stderr_log="",
                        message=error_message,
                    )
                    completed_count += 1
                    made_progress = True
                    print(
                        f"[WARN] {target['slug']} の子プロセス起動に失敗しました: {exc}",
                        flush=True,
                    )
                    now = time.time()
                    continue

                active_workers.append(worker)
                host_active_counts[host] = host_active_counts.get(host, 0) + 1
                host_last_start_at[host] = time.time()
                made_progress = True
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    status="running",
                    message="起動中",
                    started_at=str(worker["started_at"]),
                    pid=int(worker["process"].pid),
                )
                write_status_state()
                print(
                    f"[START {launched_count}/{len(targets)}] {target['name']} "
                    f"({target['slug']}, {target['system_type']}, {host}) pid={worker['process'].pid}",
                    flush=True,
                )
                now = time.time()

            while pending_index_workers and len(active_index_workers) < args.index_parallel and not shutdown_started:
                launch_index = None
                launched_worker: dict | None = None
                for index, queued_worker in enumerate(pending_index_workers):
                    try:
                        candidate = launch_index_worker(queued_worker, args=args, run_logs_dir=run_logs_dir)
                    except Exception as exc:
                        target = queued_worker["target"]
                        scrape_worker = queued_worker["scrape_worker"]
                        progress = extract_worker_progress_for_display(scrape_worker)
                        finished_at = batch_status.now_text()
                        record_target_result(
                            writer,
                            handle,
                            status_state=status_state,
                            task_name="gijiroku",
                            target=target,
                            host=str(queued_worker["host"]),
                            overall_status="failed",
                            overall_returncode=-1,
                            scrape_returncode=int(queued_worker["scrape_returncode"]),
                            index_status="failed",
                            index_returncode=-1,
                            started_at=str(scrape_worker["started_at"]),
                            finished_at=finished_at,
                            stdout_log=str(scrape_worker["stdout_path"]),
                            stderr_log=str(scrape_worker["stderr_path"]),
                            index_stdout_log="",
                            index_stderr_log="",
                            message=(
                                f"{summarize_worker(scrape_worker['stdout_path'], scrape_worker['stderr_path'])} / "
                                f"インデックス更新起動失敗: {exc}"
                            ),
                            progress=progress,
                        )
                        pending_index_workers.pop(index)
                        completed_count += 1
                        made_progress = True
                        print(
                            f"[WARN] {target['slug']} のインデックス更新起動に失敗しました: {exc}",
                            flush=True,
                        )
                        break

                    if candidate is None:
                        if time.time() >= float(queued_worker["deadline_at"]):
                            record_busy_index_result(
                                writer,
                                handle,
                                status_state=status_state,
                                task_name="gijiroku",
                                queued_worker=queued_worker,
                                run_logs_dir=run_logs_dir,
                            )
                            pending_index_workers.pop(index)
                            completed_count += 1
                            made_progress = True
                            print(
                                f"[WARN] {queued_worker['target']['slug']} のインデックス更新はロック待ちのためスキップしました。",
                                flush=True,
                            )
                            break
                        continue

                    launch_index = index
                    launched_worker = candidate
                    break

                if launch_index is None or launched_worker is None:
                    break

                pending_index_workers.pop(launch_index)
                active_index_workers.append(launched_worker)
                made_progress = True
                status_state["index_started_at"] = batch_status.now_text()
                batch_status.update_item(
                    status_state,
                    str(launched_worker["target"]["slug"]),
                    status="running",
                    message=summarize_index_worker(launched_worker),
                    pid=int(launched_worker["process"].pid),
                    **(extract_index_progress_for_display(launched_worker) or {
                        "progress_current": 0,
                        "progress_total": index_worker_total(launched_worker) or None,
                        "progress_unit": "document",
                    }),
                )
                write_status_state()
                print(
                    f"[INDEX-START] {launched_worker['target']['slug']} "
                    f"検索 index を更新中 pid={launched_worker['process'].pid}",
                    flush=True,
                )

            now = time.time()
            if (active_workers or active_index_workers) and (
                last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds
            ):
                refresh_active_worker_heartbeats(
                    status_state,
                    "gijiroku",
                    active_workers,
                    active_index_workers,
                    pending_index_workers,
                    worker_capacity=args.parallel,
                    index_capacity=args.index_parallel,
                    per_host_capacity=args.per_host_parallel,
                )
                print_status(
                    active_workers,
                    active_index_workers,
                    len(pending_index_workers),
                    completed_count,
                    len(targets),
                )
                last_status_at = now

            if pending_targets or active_workers or pending_index_workers or active_index_workers:
                if not made_progress:
                    time.sleep(1.0)

        for worker in active_workers:
            close_worker_streams(worker)
        for worker in active_index_workers:
            close_index_worker(worker)

    batch_status.finish_batch(status_state)
    batch_status.update_runtime_metrics(
        status_state,
        running_label="スクレイピング中",
        worker_capacity=args.parallel,
        worker_active_count=0,
        index_capacity=args.index_parallel,
        index_active_count=0,
        index_queue_count=0,
        per_host_capacity=args.per_host_parallel,
    )
    batch_status.write_state("gijiroku", status_state)
    print(f"[DONE] {summary_path}", flush=True)
    return 143 if stop_controller.should_stop() else 0


if __name__ == "__main__":
    raise SystemExit(main())
