#!/usr/bin/env python3
from __future__ import annotations

# 既に保存済みの clean HTML を走査し、ordinances.sqlite に未登録の行だけを補う保守バッチ。

import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import batch_status
from batch_runner_common import (
    close_worker_streams,
    extract_worker_progress_from_state,
    summarize_worker,
    target_matches as common_target_matches,
)
import reiki_priority
import reiki_targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ordinances.sqlite の欠落行を、ダウンロード済み HTML から補完します。"
    )
    parser.add_argument(
        "--filter",
        default="",
        help="slug / code / name / full_name に部分一致する自治体だけを対象にする",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        default=0,
        help="補完する自治体数の上限（0 は無制限）",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=4,
        help="同時に反映する自治体数",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=5.0,
        help="進捗表示の更新間隔（秒）",
    )
    parser.add_argument(
        "--python-command",
        default=sys.executable,
        help="子プロセス起動に使う Python コマンド",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="補完対象一覧だけ表示して終了する",
    )
    return parser


def batch_dir() -> Path:
    return reiki_targets.project_root() / "work" / "reiki" / "_reiki_reflect_batch"


def summary_output_path(run_id: str) -> Path:
    return batch_dir() / f"run_{run_id}.csv"


def logs_dir(run_id: str) -> Path:
    return batch_dir() / f"logs_{run_id}"


def target_matches(target: dict, keyword: str) -> bool:
    return common_target_matches(target, keyword, extra_fields=("system_type",))


def target_has_scraped_data(target: dict) -> bool:
    html_dir = Path(target["html_dir"])
    manifest_path = Path(target["work_root"]) / "source_manifest.json.gz"
    return html_dir.is_dir() or manifest_path.is_file()


def build_child_command(
    args: argparse.Namespace,
    target: dict,
    *,
    state_path: Path,
    result_json: Path,
) -> list[str]:
    cmd = shlex.split(str(args.python_command))
    cmd.extend(
        [
            str(Path("tools") / "reiki" / "backfill_ordinance_target.py"),
            "--slug",
            str(target["slug"]),
            "--state-path",
            str(state_path),
            "--result-json",
            str(result_json),
        ]
    )
    return cmd


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
    state_path = run_logs_dir / f"{slug}.state.json"
    result_path = run_logs_dir / f"{slug}.result.json"
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="")

    process = subprocess.Popen(
        build_child_command(args, target, state_path=state_path, result_json=result_path),
        cwd=str(reiki_targets.project_root()),
        stdout=stdout_handle,
        stderr=stderr_handle,
    )

    return {
        "seq": seq,
        "target": target,
        "process": process,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
        "stdout_handle": stdout_handle,
        "stderr_handle": stderr_handle,
        "state_path": state_path,
        "result_path": result_path,
        "started_at": batch_status.now_text(),
    }


def extract_worker_progress_for_display(worker: dict) -> dict[str, object] | None:
    return extract_worker_progress_from_state(Path(worker["state_path"]), default_unit="ordinance")


def load_worker_result(worker: dict, returncode: int) -> dict[str, object]:
    result_path = Path(worker["result_path"])
    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

    summary = summarize_worker(Path(worker["stdout_path"]), Path(worker["stderr_path"]))
    return {
        "status": "failed" if returncode != 0 else "ok",
        "returncode": int(returncode),
        "added": 0,
        "existing": 0,
        "skipped": 0,
        "total_html": 0,
        "message": summary,
    }


def print_status(active_workers: list[dict], completed_count: int, total_count: int) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[STATUS {stamp}] completed {completed_count}/{total_count}, active {len(active_workers)}", flush=True)
    for worker in active_workers:
        target = worker["target"]
        summary = summarize_worker(Path(worker["stdout_path"]), Path(worker["stderr_path"]))
        progress = extract_worker_progress_for_display(worker)
        progress_text = ""
        if progress is not None:
            progress_text = (
                f" progress={int(progress.get('progress_current', 0))}/"
                f"{int(progress.get('progress_total', 0))}"
            )
        print(
            f"  - {target['slug']} pid={worker['process'].pid}{progress_text}: {summary}",
            flush=True,
        )


def main() -> int:
    args = build_parser().parse_args()
    if args.parallel < 1:
        print("[ERROR] --parallel は 1 以上を指定してください。", flush=True)
        return 2

    targets = list(reiki_targets.iter_reiki_targets())
    keyword = str(args.filter or "").strip().lower()
    if keyword:
        targets = [target for target in targets if target_matches(target, keyword)]

    targets = [target for target in targets if target_has_scraped_data(target)]
    targets = reiki_priority.sort_targets_by_priority(targets)
    if args.max_targets > 0:
        targets = targets[: args.max_targets]

    if args.list_targets:
        print(f"[INFO] 補完対象自治体数: {len(targets)}")
        for target in targets:
            priority = reiki_priority.target_priority_info(target)
            print(
                f"{target['slug']}\t{target['code']}\t{priority['priority_label']}\t"
                f"{target['system_type']}\t{target['name']}\t{target['html_dir']}"
            )
        return 0

    if not targets:
        print("[INFO] 補完対象はありません。")
        return 0

    run_id = batch_status.now_text().replace("-", "").replace(":", "").replace(" ", "_")
    reflect_root = batch_dir()
    summary_path = summary_output_path(run_id)
    run_logs_dir = logs_dir(run_id)
    reflect_root.mkdir(parents=True, exist_ok=True)
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 補完対象自治体数: {len(targets)}", flush=True)
    print(f"[INFO] 並列数: {args.parallel}", flush=True)
    print(f"[INFO] サマリーCSV: {summary_path}", flush=True)

    status_state = batch_status.build_state("reiki_reflect", run_id, len(targets), summary_path, run_logs_dir)
    for target in targets:
        batch_status.register_target(status_state, target, "")

    def write_status_state() -> None:
        batch_status.update_runtime_metrics(
            status_state,
            running_label="反映中",
            worker_capacity=args.parallel,
            worker_active_count=len(active_workers),
        )
        batch_status.write_state("reiki_reflect", status_state)

    active_workers: list[dict] = []
    write_status_state()

    failures = 0
    total_added = 0
    pending_targets = list(targets)
    completed_count = 0
    launched_count = 0
    last_status_at = 0.0

    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slug",
                "code",
                "name",
                "priority",
                "status",
                "returncode",
                "started_at",
                "finished_at",
                "added",
                "existing",
                "skipped",
                "total_html",
            ],
        )
        writer.writeheader()

        while pending_targets or active_workers:
            made_progress = False
            completed_workers: list[tuple[dict, int]] = []
            still_running: list[dict] = []

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

            while pending_targets and len(active_workers) < args.parallel:
                target = pending_targets.pop(0)
                launched_count += 1
                try:
                    worker = launch_worker(target, launched_count, args=args, run_logs_dir=run_logs_dir)
                except Exception as exc:
                    priority = reiki_priority.target_priority_info(target)
                    finished_at = batch_status.now_text()
                    writer.writerow(
                        {
                            "slug": str(target["slug"]),
                            "code": str(target["code"]),
                            "name": str(target["name"]),
                            "priority": str(priority["priority_label"]),
                            "status": "failed",
                            "returncode": -1,
                            "started_at": "",
                            "finished_at": finished_at,
                            "added": 0,
                            "existing": 0,
                            "skipped": 0,
                            "total_html": 0,
                        }
                    )
                    handle.flush()
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="failed",
                        message=f"起動失敗: {exc}",
                        finished_at=finished_at,
                        returncode=-1,
                    )
                    failures += 1
                    completed_count += 1
                    batch_status.invalidate_runtime_caches()
                    print(f"[WARN] {target['slug']} の子プロセス起動に失敗しました: {exc}", flush=True)
                    continue

                active_workers.append(worker)
                batch_status.update_item(
                    status_state,
                    str(target["slug"]),
                    status="running",
                    message="反映中",
                    started_at=str(worker["started_at"]),
                    pid=int(worker["process"].pid),
                    progress_current=0,
                    progress_total=None,
                    progress_unit="ordinance",
                )
                write_status_state()
                made_progress = True
                print(
                    f"[START {launched_count}/{len(targets)}] {target['slug']} "
                    f"ordinances.sqlite を点検します pid={worker['process'].pid}",
                    flush=True,
                )

            for worker in active_workers:
                progress = extract_worker_progress_for_display(worker)
                if progress is None:
                    continue
                batch_status.update_item(
                    status_state,
                    str(worker["target"]["slug"]),
                    message="反映中",
                    progress_current=int(progress["progress_current"]),
                    progress_total=int(progress["progress_total"]),
                    progress_unit=str(progress["progress_unit"]),
                )

            for worker, returncode in completed_workers:
                target = worker["target"]
                priority = reiki_priority.target_priority_info(target)
                result = load_worker_result(worker, returncode)
                status = str(result.get("status", "failed"))
                finished_at = batch_status.now_text()
                total_html = int(result.get("total_html", 0) or 0)
                added = int(result.get("added", 0) or 0)
                existing = int(result.get("existing", 0) or 0)
                skipped = int(result.get("skipped", 0) or 0)
                message = str(result.get("message", "")).strip() or summarize_worker(
                    Path(worker["stdout_path"]),
                    Path(worker["stderr_path"]),
                )
                row_returncode = int(result.get("returncode", returncode) or 0)

                writer.writerow(
                    {
                        "slug": str(target["slug"]),
                        "code": str(target["code"]),
                        "name": str(target["name"]),
                        "priority": str(priority["priority_label"]),
                        "status": status,
                        "returncode": row_returncode,
                        "started_at": str(worker["started_at"]),
                        "finished_at": finished_at,
                        "added": added,
                        "existing": existing,
                        "skipped": skipped,
                        "total_html": total_html,
                    }
                )
                handle.flush()

                if status == "failed":
                    failures += 1
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="failed",
                        message=message,
                        finished_at=finished_at,
                        returncode=row_returncode,
                    )
                    print(f"[WARN] {target['slug']} の補完に失敗しました: {message}", flush=True)
                else:
                    total_added += added
                    batch_status.update_item(
                        status_state,
                        str(target["slug"]),
                        status="ok",
                        message=message,
                        finished_at=finished_at,
                        returncode=row_returncode,
                        progress_current=total_html if total_html > 0 else None,
                        progress_total=total_html if total_html > 0 else None,
                        progress_unit="ordinance",
                    )
                    print(
                        f"[DONE {completed_count + 1}/{len(targets)}] {target['slug']} {message}",
                        flush=True,
                    )

                completed_count += 1
                batch_status.invalidate_runtime_caches()
                made_progress = True

            now = time.time()
            if active_workers and (last_status_at == 0.0 or now - last_status_at >= args.refresh_seconds):
                write_status_state()
                print_status(active_workers, completed_count, len(targets))
                last_status_at = now
            else:
                write_status_state()

            if pending_targets or active_workers:
                if not made_progress:
                    time.sleep(1.0)

    for worker in active_workers:
        close_worker_streams(worker)

    batch_status.finish_batch(status_state)
    batch_status.update_runtime_metrics(
        status_state,
        running_label="反映中",
        worker_capacity=args.parallel,
        worker_active_count=0,
    )
    batch_status.write_state("reiki_reflect", status_state)
    batch_status.invalidate_runtime_caches()

    if failures:
        print(f"[WARN] {failures} 件の補完に失敗しました。 added={total_added}", flush=True)
        return 1

    print(f"[DONE] ordinances.sqlite の不足分を補完しました。 added={total_added}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
