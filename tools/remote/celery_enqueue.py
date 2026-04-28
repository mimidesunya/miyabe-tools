#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from tools.remote.celery_app import app


TASK_CHOICES = {
    "gijiroku-cycle": ("tools.remote.celery_tasks.run_gijiroku_cycle", "gijiroku"),
    "gijiroku-backfill": ("tools.remote.celery_tasks.run_gijiroku_backfill", "gijiroku"),
    "gijiroku-rebuild": ("tools.remote.celery_tasks.run_gijiroku_rebuild", "gijiroku"),
    "reiki-cycle": ("tools.remote.celery_tasks.run_reiki_cycle", "reiki"),
    "reiki-backfill": ("tools.remote.celery_tasks.run_reiki_backfill", "reiki"),
    "reiki-rebuild": ("tools.remote.celery_tasks.run_reiki_rebuild", "reiki"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Celery queue にスクレイパ task を投入します。")
    parser.add_argument(
        "task",
        choices=sorted(TASK_CHOICES.keys()),
        help="投入する task 名",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="rebuild task 用の自治体フィルタ",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    task_name, queue_name = TASK_CHOICES[args.task]
    kwargs = {}
    if args.task.endswith("-rebuild"):
        kwargs["name_filter"] = args.filter.strip()
    result = app.send_task(task_name, kwargs=kwargs, queue=queue_name)
    print(
        json.dumps(
            {
                "task": args.task,
                "queue": queue_name,
                "task_name": task_name,
                "task_id": result.id,
                "filter": kwargs.get("name_filter", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
