#!/usr/bin/env python3
"""対応する全例規集スクレイパを束ねる batch runner。

ローカル実行と Docker scraper worker の両方で使う実行時入口。
例規集 URL 調査 TSV を読み、Python/PHP の子スクレイパを起動する。
実行ループ本体（並列制御・state 記録・OpenSearch 増分更新）は
tools/tasks/batch.py に集約し、ここには例規集固有の判定だけを置く。
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
# 例規集 batch runner は会議録側の task/build-lock ヘルパも共有する。
# ファイルパス指定で実行されるため、関係する tools ディレクトリを明示的に import 対象へ入れる。
import freshness_metadata
import reiki_targets
from tools.tasks import backfill as task_backfill
from tools.tasks import batch as scraping_batch
from tools.tasks import priority as scraping_priority


reiki_priority = scraping_priority.PriorityCalculator(
    "reiki",
    count_field="clean_html_count",
)


SUPPORTED_SYSTEMS = {
    "d1-law": ("python", "scrapers/d1_law.py"),
    "taikei": ("php", "scrapers/taikei.php"),
    "g-reiki": ("php", "scrapers/taikei.php"),
    "joureikun": ("python", "scrapers/joureikun.py"),
    "legalcrud": ("python", "scrapers/joureikun.py"),
    "reiki.html": ("python", "scrapers/d1_law.py"),
    "reiki_menu": ("python", "scrapers/d1_law.py"),
    "h-chosonkai": ("python", "scrapers/d1_law.py"),
    "jourei-v5": ("python", "scrapers/jourei_v5.py"),
    "legal-square": ("python", "scrapers/legal_square.py"),
}
TAIKEI_LIKE_SYSTEMS = {"taikei", "g-reiki"}


# 一括例規集スクレイパの CLI オプションを定義する。
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="reiki_system_urls.tsv の対応済み system_type をまとめてスクレイピングします。"
    )
    parser.add_argument(
        "--systems",
        default=",".join(SUPPORTED_SYSTEMS.keys()),
        help="対象 system_type のカンマ区切り一覧",
    )
    parser.add_argument(
        "--per-target-limit",
        type=int,
        default=0,
        help="taikei 系で各自治体から取得する件数上限（0 は無制限）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存ソースがあっても取り直す",
    )
    parser.add_argument(
        "--check-updates",
        action="store_true",
        help="既存条例も再取得して更新を確認する",
    )
    parser.add_argument(
        "--crawl-only",
        action="store_true",
        help="taikei / g-reiki 系では体系クロールだけ行い本文取得を省く",
    )
    parser.add_argument(
        "--php-command",
        default="php",
        help="PHP 系子プロセス起動に使うコマンド",
    )
    scraping_batch.add_common_arguments(parser)
    return parser


# --systems の指定を検証し、実行対象 system_type の一覧へ変換する。
def parse_requested_systems(value: str) -> list[str]:
    systems = [item.strip() for item in str(value).split(",") if item.strip()]
    if not systems:
        return list(SUPPORTED_SYSTEMS.keys())
    unsupported = [system for system in systems if system not in SUPPORTED_SYSTEMS]
    if unsupported:
        raise ValueError(f"Unsupported system_type: {', '.join(unsupported)}")
    return systems


# 自治体 1 件を取得するための Python/PHP 子スクレイパ起動コマンドを作る。
def build_child_command(args: argparse.Namespace, target: dict) -> list[str]:
    system_type = str(target["system_type"])
    runner_kind, script_name = SUPPORTED_SYSTEMS[system_type]
    slug = str(target["slug"])
    script_path = str(Path("tools") / "reiki" / script_name)

    if runner_kind == "python":
        cmd = shlex.split(str(args.python_command))
        cmd.extend([script_path, "--slug", slug])
    else:
        cmd = shlex.split(str(args.php_command))
        cmd.extend(
            [
                script_path,
                f"--system-type={system_type}",
                "--slug",
                slug,
                "--code",
                str(target["code"]),
                "--name",
                str(target["name"]),
                "--source-url",
                str(target["source_url"]),
            ]
        )

    if args.force:
        cmd.append("--force")
    if args.check_updates:
        cmd.append("--check-updates")
    if system_type in TAIKEI_LIKE_SYSTEMS and args.crawl_only:
        cmd.append("--crawl-only")
    if system_type in TAIKEI_LIKE_SYSTEMS and args.per_target_limit > 0:
        cmd.append(f"--limit={args.per_target_limit}")
    return cmd


# 子スクレイパが進捗を書く scrape_state.json のパスを返す。
def scrape_state_path(target: dict) -> Path:
    return Path(str(target.get("work_root") or "")) / "scrape_state.json"


def actual_scrape_progress(target: dict) -> tuple[int, int]:
    """保存済み成果物を数え、終了判定に使う実取得件数を返す。"""
    work_root = Path(str(target.get("work_root") or ""))
    manifest_path = work_root / "source_manifest.json"
    source_dir = Path(str(target.get("source_dir") or ""))
    html_dir = Path(str(target.get("html_dir") or ""))
    manifest_count = task_backfill.load_json_array_count(manifest_path)
    source_count = task_backfill.count_reiki_html_files(source_dir)
    clean_html_count = task_backfill.count_reiki_html_files(html_dir)
    current_count = max(source_count, clean_html_count)
    total_count = manifest_count if manifest_count > 0 else current_count
    return max(0, current_count), max(0, max(total_count, current_count))


# 子スクレイパが returncode=0 でも、取得件数が完了していなければ成功扱いしない。
def scrape_completion_error(target: dict, progress: dict | None) -> str:
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


# --crawl-only では本文成果物が増えないので、index 更新は行わない。
def index_enabled(args: argparse.Namespace) -> bool:
    return not args.no_build_index and not args.crawl_only


BATCH_SPEC = scraping_batch.BatchSpec(
    task_name="reiki",
    progress_unit="ordinance",
    index_doc_type="reiki",
    batch_dir=reiki_targets.project_root() / "work" / "reiki" / "_reiki_batch",
    project_root=reiki_targets.project_root(),
    priority=reiki_priority,
    build_child_command=build_child_command,
    scrape_state_path=scrape_state_path,
    actual_scrape_progress=actual_scrape_progress,
    scrape_completion_error=scrape_completion_error,
    target_freshness=freshness_metadata.reiki_target_freshness,
    index_enabled=index_enabled,
)


# 例規集一括スクレイピングの入口。対象選定までを行い、実行ループは共通実装に任せる。
def main() -> int:
    args = build_parser().parse_args()
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
        freshness_metadata.attach_target_freshness("reiki", target)
        for target in reiki_targets.iter_reiki_targets()
        if str(target.get("system_type", "")) in requested_systems
    ]

    return scraping_batch.run_batch(BATCH_SPEC, args, targets)


if __name__ == "__main__":
    raise SystemExit(main())
