from __future__ import annotations

from celery import Celery

from tools.remote import celery_runtime


GIJIROKU_QUEUE = "gijiroku"
REIKI_QUEUE = "reiki"
DISPATCH_INTERVAL_SECONDS = celery_runtime.env_int(
    "CELERY_DISPATCH_INTERVAL_SECONDS",
    60,
    minimum=15,
)
GIJIROKU_SCHEDULE_SECONDS = celery_runtime.env_int(
    "CELERY_GIJIROKU_SCHEDULE_SECONDS",
    6 * 60 * 60,
    minimum=60,
)
REIKI_SCHEDULE_SECONDS = celery_runtime.env_int(
    "CELERY_REIKI_SCHEDULE_SECONDS",
    6 * 60 * 60,
    minimum=60,
)

app = Celery(
    "miyabe_tools_scraping",
    broker=celery_runtime.env_text("CELERY_BROKER_URL", "redis://scraper-redis:6379/0"),
    backend=celery_runtime.env_text("CELERY_RESULT_BACKEND", "redis://scraper-redis:6379/1"),
    include=["tools.remote.celery_tasks"],
)

app.conf.update(
    timezone=celery_runtime.env_text("CELERY_TIMEZONE", celery_runtime.DEFAULT_TIMEZONE),
    enable_utc=False,
    task_default_queue="maintenance",
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    result_expires=24 * 60 * 60,
    beat_schedule={
        "dispatch-gijiroku-cycle": {
            "task": "tools.remote.celery_tasks.dispatch_gijiroku_cycle",
            "schedule": float(DISPATCH_INTERVAL_SECONDS),
            "options": {
                "queue": GIJIROKU_QUEUE,
                "expires": max(5, DISPATCH_INTERVAL_SECONDS - 5),
            },
        },
        "dispatch-reiki-cycle": {
            "task": "tools.remote.celery_tasks.dispatch_reiki_cycle",
            "schedule": float(DISPATCH_INTERVAL_SECONDS),
            "options": {
                "queue": REIKI_QUEUE,
                "expires": max(5, DISPATCH_INTERVAL_SECONDS - 5),
            },
        },
    },
    task_routes={
        "tools.remote.celery_tasks.dispatch_gijiroku_cycle": {"queue": GIJIROKU_QUEUE},
        "tools.remote.celery_tasks.run_gijiroku_backfill": {"queue": GIJIROKU_QUEUE},
        "tools.remote.celery_tasks.run_gijiroku_cycle": {"queue": GIJIROKU_QUEUE},
        "tools.remote.celery_tasks.run_gijiroku_rebuild": {"queue": GIJIROKU_QUEUE},
        "tools.remote.celery_tasks.dispatch_reiki_cycle": {"queue": REIKI_QUEUE},
        "tools.remote.celery_tasks.run_reiki_backfill": {"queue": REIKI_QUEUE},
        "tools.remote.celery_tasks.run_reiki_cycle": {"queue": REIKI_QUEUE},
        "tools.remote.celery_tasks.run_reiki_rebuild": {"queue": REIKI_QUEUE},
    },
)
