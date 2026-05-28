#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from deploy.scraper_runtime.celery.app import app, GIJIROKU_INDEX_QUEUE, REIKI_INDEX_QUEUE


TASK_CHOICES = {
    "gijiroku-cycle": ("deploy.scraper_runtime.celery.tasks.run_gijiroku_cycle", "gijiroku"),
    "gijiroku-backfill": ("deploy.scraper_runtime.celery.tasks.run_gijiroku_backfill", "gijiroku"),
    "gijiroku-rebuild": ("deploy.scraper_runtime.celery.tasks.run_gijiroku_rebuild", "gijiroku"),
    "gijiroku-index": ("deploy.scraper_runtime.celery.tasks.run_gijiroku_index_update", GIJIROKU_INDEX_QUEUE),
    "reiki-cycle": ("deploy.scraper_runtime.celery.tasks.run_reiki_cycle", "reiki"),
    "reiki-backfill": ("deploy.scraper_runtime.celery.tasks.run_reiki_backfill", "reiki"),
    "reiki-rebuild": ("deploy.scraper_runtime.celery.tasks.run_reiki_rebuild", "reiki"),
    "reiki-index": ("deploy.scraper_runtime.celery.tasks.run_reiki_index_update", REIKI_INDEX_QUEUE),
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
        help="旧互換オプション。OpenSearch rebuild では無視されます",
    )
    parser.add_argument(
        "--slug",
        default="",
        help="*-index task で更新する自治体 slug",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    task_name, queue_name = TASK_CHOICES[args.task]
    kwargs = {}
    if args.task.endswith("-rebuild"):
        kwargs["name_filter"] = args.filter.strip()
    if args.task.endswith("-index"):
        slug = args.slug.strip()
        if slug == "":
            print("--slug is required for index tasks", file=sys.stderr)
            return 2
        kwargs["slug"] = slug
    result = app.send_task(task_name, kwargs=kwargs, queue=queue_name)
    print(
        json.dumps(
            {
                "task": args.task,
                "queue": queue_name,
                "task_name": task_name,
                "task_id": result.id,
                "filter": kwargs.get("name_filter", ""),
                "slug": kwargs.get("slug", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
