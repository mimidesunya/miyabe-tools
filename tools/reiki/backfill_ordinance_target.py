#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).parent))
import build_ordinance_index
import reiki_io
import reiki_targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="1 自治体ぶんの ordinances.sqlite 欠落行を補完します。"
    )
    parser.add_argument("--slug", required=True, help="対象自治体 slug")
    parser.add_argument("--state-path", type=Path, required=True, help="進捗 JSON の出力先")
    parser.add_argument("--result-json", type=Path, required=True, help="結果 JSON の出力先")
    return parser


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def emit_progress(state_path: Path, progress: dict[str, int | str]) -> None:
    processed = int(progress.get("processed", 0) or 0)
    total_html = int(progress.get("total_html", 0) or 0)
    reiki_io.update_progress_state(
        state_path,
        current=processed,
        total=total_html,
        unit="ordinance",
    )
    print(
        f"[PROGRESS] unit=ordinance current={processed} total={total_html}",
        flush=True,
    )


def main() -> int:
    args = build_parser().parse_args()
    target = reiki_targets.load_reiki_target(args.slug)
    state_path = args.state_path
    result_json = args.result_json

    try:
        state_path.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        def on_progress(progress: dict[str, int | str]) -> None:
            emit_progress(state_path, progress)

        stats = build_ordinance_index.backfill_missing_rows(
            slug=str(target["slug"]),
            clean_html_dir=Path(target["html_dir"]),
            classification_dir=Path(target["classification_dir"]),
            markdown_dir=Path(target["markdown_dir"]),
            manifest_json=Path(target["work_root"]) / "source_manifest.json.gz",
            output_db=Path(target["db_path"]),
            progress_callback=on_progress,
        )
        total_html = int(stats["total_html"])
        reiki_io.update_progress_state(
            state_path,
            current=total_html,
            total=total_html,
            unit="ordinance",
        )
        message = (
            f"added={stats['added']} existing={stats['existing']} "
            f"skipped={stats['skipped']} total_html={stats['total_html']}"
        )
        write_json_atomic(
            result_json,
            {
                "status": "ok",
                "returncode": 0,
                "added": int(stats["added"]),
                "existing": int(stats["existing"]),
                "skipped": int(stats["skipped"]),
                "total_html": total_html,
                "message": message,
            },
        )
        print(f"[DONE] {message}", flush=True)
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        write_json_atomic(
            result_json,
            {
                "status": "failed",
                "returncode": 1,
                "added": 0,
                "existing": 0,
                "skipped": 0,
                "total_html": 0,
                "message": message,
            },
        )
        print(f"[ERROR] {message}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
