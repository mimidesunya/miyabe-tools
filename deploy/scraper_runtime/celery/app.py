from __future__ import annotations

from celery import Celery

from deploy.scraper_runtime.celery import runtime as celery_runtime


GIJIROKU_QUEUE = "gijiroku"
REIKI_QUEUE = "reiki"
GIJIROKU_INDEX_QUEUE = "gijiroku-index"
REIKI_INDEX_QUEUE = "reiki-index"
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
INDEX_OUTBOX_SWEEP_SECONDS = celery_runtime.env_int(
    "CELERY_INDEX_OUTBOX_SWEEP_SECONDS",
    10 * 60,
    minimum=60,
)
# 索引側の解釈を直した自治体を拾い直す間隔。取得はやり直さないので、
# 掃き取り自体は軽い。積む自治体の数は `sweep_stale_parser_generation` で絞る。
STALE_GENERATION_SWEEP_SECONDS = celery_runtime.env_int(
    "CELERY_STALE_GENERATION_SWEEP_SECONDS",
    60 * 60,
    minimum=300,
)
# 索引に 1 件も無い自治体を拾う間隔。件数は少ないので、間隔は長くてよい。
NEVER_INDEXED_SWEEP_SECONDS = celery_runtime.env_int(
    "CELERY_NEVER_INDEXED_SWEEP_SECONDS",
    6 * 60 * 60,
    minimum=600,
)
# 取りこぼし台帳を書き出す間隔。見るための仕組みなので、1 日 2 回で足りる。
COVERAGE_LEDGER_SECONDS = celery_runtime.env_int(
    "CELERY_COVERAGE_LEDGER_SECONDS",
    12 * 60 * 60,
    minimum=600,
)

app = Celery(
    "miyabe_tools_scraping",
    broker=celery_runtime.env_text("CELERY_BROKER_URL", "redis://scraper-redis:6379/0"),
    backend=celery_runtime.env_text("CELERY_RESULT_BACKEND", "redis://scraper-redis:6379/1"),
    include=["deploy.scraper_runtime.celery.tasks"],
)

app.conf.update(
    timezone=celery_runtime.env_text("CELERY_TIMEZONE", celery_runtime.DEFAULT_TIMEZONE),
    enable_utc=False,
    task_default_queue="maintenance",
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # OpenSearch の全量 rebuild は半日以上かかる。Redis broker の visibility timeout
    # （既定 1 時間）を超えると未 ACK メッセージがキューへ再配達され、完了直後に
    # 同じ rebuild がもう一度走る。最長タスクより十分長い 48 時間にする。
    broker_transport_options={"visibility_timeout": 48 * 60 * 60},
    task_track_started=True,
    result_expires=24 * 60 * 60,
    beat_schedule={
        "dispatch-gijiroku-cycle": {
            "task": "deploy.scraper_runtime.celery.tasks.dispatch_gijiroku_cycle",
            "schedule": float(DISPATCH_INTERVAL_SECONDS),
            "options": {
                "queue": GIJIROKU_QUEUE,
                "expires": max(5, DISPATCH_INTERVAL_SECONDS - 5),
            },
        },
        "dispatch-reiki-cycle": {
            "task": "deploy.scraper_runtime.celery.tasks.dispatch_reiki_cycle",
            "schedule": float(DISPATCH_INTERVAL_SECONDS),
            "options": {
                "queue": REIKI_QUEUE,
                "expires": max(5, DISPATCH_INTERVAL_SECONDS - 5),
            },
        },
        # 索引へ載せられなかった自治体を、取得のやり直しを待たずに投げ直す。
        # これが無いと、索引だけが落ちた自治体は次の取得（最大 30 日後）まで
        # 公開へ反映されない。決定的な失敗なら永久に反映されない。
        "sweep-index-outbox": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_index_outbox",
            "schedule": float(INDEX_OUTBOX_SWEEP_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, INDEX_OUTBOX_SWEEP_SECONDS - 5),
            },
        },
        # 索引側のパーサを直しても、その自治体を再索引するまで公開は古いまま。
        # 再取得は 30 日周期なので、放っておくと直した日から最大 30 日ずれる。
        # 世代印が古い自治体を少しずつ積み直して、取得を待たずに追いつかせる。
        "sweep-stale-parser-generation": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_stale_parser_generation",
            "schedule": float(STALE_GENERATION_SWEEP_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, STALE_GENERATION_SWEEP_SECONDS - 5),
            },
        },
        # 世代の掃き取りは「載っている文書の世代」を見るので、1 件も載って
        # いない自治体は見えない。鳥栖市は例規 588 件を取得済みなのに公開へ
        # 1 件も出ておらず、待ち行列にも残っていなかった。
        "sweep-never-indexed": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_never_indexed",
            "schedule": float(NEVER_INDEXED_SWEEP_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, NEVER_INDEXED_SWEEP_SECONDS - 5),
            },
        },
        # 公開に 1 件も出ていない自治体を、原因まで分けて数えて書き出す。
        # 直す仕組みではなく、見える仕組み。能代市は取得の失敗が記録され
        # 続けていたのに、何ヶ月も誰の目にも入らなかった。
        "write-coverage-ledger": {
            "task": "deploy.scraper_runtime.celery.tasks.write_coverage_ledger",
            "schedule": float(COVERAGE_LEDGER_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, COVERAGE_LEDGER_SECONDS - 5),
            },
        },
    },
    task_routes={
        "deploy.scraper_runtime.celery.tasks.dispatch_gijiroku_cycle": {"queue": GIJIROKU_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_gijiroku_backfill": {"queue": GIJIROKU_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_gijiroku_cycle": {"queue": GIJIROKU_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_gijiroku_rebuild": {"queue": GIJIROKU_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_gijiroku_index_update": {"queue": GIJIROKU_INDEX_QUEUE},
        "deploy.scraper_runtime.celery.tasks.dispatch_reiki_cycle": {"queue": REIKI_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_reiki_backfill": {"queue": REIKI_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_reiki_cycle": {"queue": REIKI_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_reiki_rebuild": {"queue": REIKI_QUEUE},
        "deploy.scraper_runtime.celery.tasks.run_reiki_index_update": {"queue": REIKI_INDEX_QUEUE},
    },
)
