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
# 取得済みなのに公開へ出ていない自治体を積み直す間隔。保存件数を数えるのに
# 全国のファイルを歩くので、1 日 1 回でよい。
INDEX_GAP_SWEEP_SECONDS = celery_runtime.env_int(
    "CELERY_INDEX_GAP_SWEEP_SECONDS",
    24 * 60 * 60,
    minimum=3600,
)
# 本文がほとんど空の自治体を取得からやり直す間隔。取り直しは重いので、
# 間隔は長くてよい。1 回に 2 自治体まで、同じ自治体は 3 日空ける。
EMPTY_BODY_SWEEP_SECONDS = celery_runtime.env_int(
    "CELERY_EMPTY_BODY_SWEEP_SECONDS",
    6 * 60 * 60,
    minimum=600,
)
COVERAGE_LEDGER_SECONDS = celery_runtime.env_int(
    "CELERY_COVERAGE_LEDGER_SECONDS",
    12 * 60 * 60,
    minimum=600,
)
# 文字情報のない PDF の OCR。1 自治体に数十分かかることがあるので、
# 間隔を空けて少数ずつ進める。放っておけば全件をひと回りする。
PDF_OCR_SECONDS = celery_runtime.env_int(
    "CELERY_PDF_OCR_SECONDS",
    3 * 60 * 60,
    minimum=600,
)
# 取得元の探索。1 自治体あたり十数ページ開くので、間隔を空けて少しずつ回す。
SOURCE_DISCOVERY_SECONDS = celery_runtime.env_int(
    "CELERY_SOURCE_DISCOVERY_SECONDS",
    6 * 60 * 60,
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
        # 取得済みなのに公開へ出ていない自治体を積み直す。sweep-never-indexed
        # は 1 件も無い自治体しか拾わない。各務原市は 3,220 件を保存して
        # 5 件しか公開しておらず、0 件ではないので見えなかった。
        "sweep-index-gap": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_index_gap",
            "schedule": float(INDEX_GAP_SWEEP_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, INDEX_GAP_SWEEP_SECONDS - 5),
            },
        },
        # 本文がほとんど空の自治体を、取得からやり直す。索引を積み直しても
        # 直らない形がある。高崎市は保存されていたのが本文ではなく
        # フレーム枠だった。取得側を直しても resume が読み飛ばすので、
        # 取り直しを指示する経路が要る。
        "sweep-empty-body": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_empty_body",
            "schedule": float(EMPTY_BODY_SWEEP_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, EMPTY_BODY_SWEEP_SECONDS - 5),
            },
        },
        # 文字情報のない PDF を OCR で本文にする。OCR が無い間は、何周回しても
        # 同じように除外され続ける。取得の周期には混ぜず、ここで少しずつ進める。
        "sweep-pdf-ocr": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_pdf_ocr",
            "schedule": float(PDF_OCR_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, PDF_OCR_SECONDS - 5),
            },
        },
        # 取得元 URL が空の自治体を探索して埋める。埋まらない限り巡回の
        # キューに載らないので、放置しても状態が変わらない自治体が残る。
        # 2026-09-06 の点検では会議録 245・例規集 27 自治体がこの形だった。
        "sweep-source-discovery": {
            "task": "deploy.scraper_runtime.celery.tasks.sweep_source_discovery",
            "schedule": float(SOURCE_DISCOVERY_SECONDS),
            "options": {
                "queue": "maintenance",
                "expires": max(5, SOURCE_DISCOVERY_SECONDS - 5),
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
