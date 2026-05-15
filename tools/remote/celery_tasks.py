from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from tools.remote.celery_app import app
from tools.remote import celery_runtime


ROOT = Path(__file__).resolve().parents[2]


def _python_command_text() -> str:
    return celery_runtime.env_text("SCRAPER_PYTHON_COMMAND", "python3")


def _python_command() -> list[str]:
    return shlex.split(_python_command_text())


def _php_command_text() -> str:
    return celery_runtime.env_text("SCRAPER_PHP_COMMAND", "php")


def _run_command(label: str, command: list[str]) -> None:
    print(f"[CELERY] {label}: {celery_runtime.command_text(command)}", flush=True)
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def _scraper_build_search_index() -> bool:
    return celery_runtime.env_bool("SCRAPER_BUILD_SEARCH_INDEX", False)


def _gijiroku_backfill_command() -> list[str]:
    return _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "rebuild",
        "--doc-type",
        "minutes",
    ]


def _gijiroku_scrape_command() -> list[str]:
    command = _python_command() + ["tools/gijiroku/scrape_all_minutes.py"]
    if celery_runtime.env_bool("SCRAPER_GIJIROKU_ACK_ROBOTS", True):
        command.append("--ack-robots")
    command.extend(
        [
            "--parallel",
            str(celery_runtime.env_int("SCRAPER_GIJIROKU_PARALLEL", 8, minimum=1)),
            "--index-parallel",
            str(celery_runtime.env_int("SCRAPER_GIJIROKU_INDEX_PARALLEL", 1, minimum=1)),
            "--per-host-parallel",
            str(celery_runtime.env_int("SCRAPER_GIJIROKU_PER_HOST_PARALLEL", 1, minimum=1)),
            "--per-host-start-interval",
            str(
                celery_runtime.env_float(
                    "SCRAPER_GIJIROKU_PER_HOST_START_INTERVAL",
                    2.0,
                    minimum=0.0,
                )
            ),
            "--python-command",
            _python_command_text(),
        ]
    )
    if not _scraper_build_search_index():
        command.append("--no-build-index")
    return command


def _reiki_backfill_command() -> list[str]:
    return _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "rebuild",
        "--doc-type",
        "reiki",
    ]


def _reiki_scrape_command() -> list[str]:
    command = _python_command() + ["tools/reiki/scrape_all_reiki.py"]
    if celery_runtime.env_bool("SCRAPER_REIKI_CHECK_UPDATES", True):
        command.append("--check-updates")
    command.extend(
        [
            "--parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_PARALLEL", 8, minimum=1)),
            "--per-host-parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_PER_HOST_PARALLEL", 1, minimum=1)),
            "--per-host-start-interval",
            str(
                celery_runtime.env_float(
                    "SCRAPER_REIKI_PER_HOST_START_INTERVAL",
                    2.0,
                    minimum=0.0,
                )
            ),
            "--python-command",
            _python_command_text(),
            "--php-command",
            _php_command_text(),
        ]
    )
    if not _scraper_build_search_index():
        command.append("--no-build-index")
    return command


def _rebuild_command(kind: str, name_filter: str) -> list[str]:
    doc_type = "minutes" if kind == "minutes" else "reiki"
    command = _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "rebuild",
        "--doc-type",
        doc_type,
    ]
    if name_filter.strip() != "":
        print("[CELERY] name_filter is ignored by OpenSearch rebuild", flush=True)
    return command


def _run_gijiroku_backfill_impl() -> None:
    _run_command("gijiroku backfill", _gijiroku_backfill_command())


def _run_gijiroku_scrape_impl() -> None:
    _run_command("gijiroku scrape", _gijiroku_scrape_command())


def _run_reiki_backfill_impl() -> None:
    _run_command("reiki backfill", _reiki_backfill_command())


def _run_reiki_scrape_impl() -> None:
    _run_command("reiki scrape", _reiki_scrape_command())


@app.task(name="tools.remote.celery_tasks.dispatch_gijiroku_cycle")
def dispatch_gijiroku_cycle() -> dict[str, object]:
    schedule_seconds = celery_runtime.env_int("CELERY_GIJIROKU_SCHEDULE_SECONDS", 6 * 60 * 60, minimum=60)
    if not celery_runtime.cycle_is_due("gijiroku", schedule_seconds):
        return {"enqueued": False, "task": "gijiroku", "reason": "not_due"}
    result = app.send_task("tools.remote.celery_tasks.run_gijiroku_cycle", queue="gijiroku")
    return {"enqueued": True, "task": "gijiroku", "task_id": result.id}


@app.task(name="tools.remote.celery_tasks.dispatch_reiki_cycle")
def dispatch_reiki_cycle() -> dict[str, object]:
    schedule_seconds = celery_runtime.env_int("CELERY_REIKI_SCHEDULE_SECONDS", 6 * 60 * 60, minimum=60)
    if not celery_runtime.cycle_is_due("reiki", schedule_seconds):
        return {"enqueued": False, "task": "reiki", "reason": "not_due"}
    result = app.send_task("tools.remote.celery_tasks.run_reiki_cycle", queue="reiki")
    return {"enqueued": True, "task": "reiki", "task_id": result.id}


@app.task(name="tools.remote.celery_tasks.run_gijiroku_backfill")
def run_gijiroku_backfill() -> dict[str, object]:
    _run_gijiroku_backfill_impl()
    return {"ok": True, "task": "gijiroku_backfill"}


@app.task(bind=True, name="tools.remote.celery_tasks.run_gijiroku_cycle", max_retries=None)
def run_gijiroku_cycle(self) -> dict[str, object]:
    try:
        celery_runtime.clear_retry_marker("gijiroku")
        _run_gijiroku_scrape_impl()
        celery_runtime.clear_retry_marker("gijiroku")
        return {"ok": True, "task": "gijiroku_cycle"}
    except Exception as exc:
        delay_seconds = celery_runtime.env_int("SCRAPER_FAIL_SLEEP_SECONDS", 15 * 60, minimum=60)
        celery_runtime.set_retry_marker("gijiroku", delay_seconds)
        print(f"[CELERY] gijiroku cycle failed; retrying in {delay_seconds}s", flush=True)
        raise self.retry(exc=exc, countdown=delay_seconds)


@app.task(name="tools.remote.celery_tasks.run_gijiroku_rebuild")
def run_gijiroku_rebuild(name_filter: str = "") -> dict[str, object]:
    _run_command("gijiroku rebuild", _rebuild_command("minutes", name_filter))
    return {"ok": True, "task": "gijiroku_rebuild", "filter": name_filter}


@app.task(name="tools.remote.celery_tasks.run_reiki_backfill")
def run_reiki_backfill() -> dict[str, object]:
    _run_reiki_backfill_impl()
    return {"ok": True, "task": "reiki_backfill"}


@app.task(bind=True, name="tools.remote.celery_tasks.run_reiki_cycle", max_retries=None)
def run_reiki_cycle(self) -> dict[str, object]:
    try:
        celery_runtime.clear_retry_marker("reiki")
        _run_reiki_scrape_impl()
        celery_runtime.clear_retry_marker("reiki")
        return {"ok": True, "task": "reiki_cycle"}
    except Exception as exc:
        delay_seconds = celery_runtime.env_int("SCRAPER_FAIL_SLEEP_SECONDS", 15 * 60, minimum=60)
        celery_runtime.set_retry_marker("reiki", delay_seconds)
        print(f"[CELERY] reiki cycle failed; retrying in {delay_seconds}s", flush=True)
        raise self.retry(exc=exc, countdown=delay_seconds)


@app.task(name="tools.remote.celery_tasks.run_reiki_rebuild")
def run_reiki_rebuild(name_filter: str = "") -> dict[str, object]:
    _run_command("reiki rebuild", _rebuild_command("reiki", name_filter))
    return {"ok": True, "task": "reiki_rebuild", "filter": name_filter}
