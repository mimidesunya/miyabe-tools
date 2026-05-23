from __future__ import annotations

import shlex
import signal
import subprocess
import time
import re
from pathlib import Path

from tools.remote.celery_app import app
from tools.remote import celery_runtime
from tools.batch_runner_common import process_group_popen_kwargs, terminate_process_group
from tools import batch_status
from tools.gijiroku import gijiroku_targets
from tools.reiki import reiki_targets


ROOT = Path(__file__).resolve().parents[2]
INDEX_BULK_RE = re.compile(r"^\[BULK\]\s+.*\btotal=(?P<total>\d+)\b")
INDEX_DONE_RE = re.compile(r"^\[DONE\]\s+.*\bcount=(?P<count>\d+)\b")


def _python_command_text() -> str:
    return celery_runtime.env_text("SCRAPER_PYTHON_COMMAND", "python3")


def _python_command() -> list[str]:
    return shlex.split(_python_command_text())


def _php_command_text() -> str:
    return celery_runtime.env_text("SCRAPER_PHP_COMMAND", "php")


def _run_command(label: str, command: list[str]) -> None:
    print(f"[CELERY] {label}: {celery_runtime.command_text(command)}", flush=True)
    stop_requested = False
    previous_handlers: dict[int, object] = {}

    def request_stop(signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        print(f"[CELERY] {label}: received signal {int(signum)}; stopping child process", flush=True)

    for signame in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)

    process = subprocess.Popen(command, cwd=str(ROOT), **process_group_popen_kwargs())
    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            if stop_requested:
                returncode = terminate_process_group(process)
                if returncode is None:
                    returncode = -signal.SIGTERM
                break
            time.sleep(1.0)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {returncode}")


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
            "--index-dispatch",
            "celery",
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


def _metadata_reconcile_command(task_name: str) -> list[str]:
    return _python_command() + [
        "tools/backfill_background_tasks.py",
        "--tasks",
        task_name,
    ]


def _reiki_scrape_command() -> list[str]:
    command = _python_command() + ["tools/reiki/scrape_all_reiki.py"]
    if celery_runtime.env_bool("SCRAPER_REIKI_CHECK_UPDATES", True):
        command.append("--check-updates")
    command.extend(
        [
            "--parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_PARALLEL", 1, minimum=1)),
            "--index-parallel",
            str(celery_runtime.env_int("SCRAPER_REIKI_INDEX_PARALLEL", 1, minimum=1)),
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
            "--index-dispatch",
            "celery",
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


def _index_update_command(doc_type: str, slug: str) -> list[str]:
    return _python_command() + [
        "tools/search/build_opensearch_index.py",
        "--mode",
        "update",
        "--doc-type",
        doc_type,
        "--slug",
        slug,
    ]


def _target_by_slug(kind: str, slug: str) -> dict[str, object]:
    targets = gijiroku_targets.iter_gijiroku_targets() if kind == "gijiroku" else reiki_targets.iter_reiki_targets()
    for target in targets:
        if str(target.get("slug") or "").strip() == slug:
            return target
    return {"slug": slug, "code": "", "name": slug, "full_name": slug, "system_type": "", "source_url": ""}


def _index_document_total(kind: str, slug: str) -> int:
    try:
        from tools.search import build_opensearch_index as search_index

        slugs = {slug}
        if kind == "gijiroku":
            return max(0, int(search_index.count_minutes_documents_by_slug(slugs=slugs).get(slug, 0)))
        return max(0, int(search_index.count_reiki_documents_by_slug(slugs=slugs).get(slug, 0)))
    except Exception as exc:
        print(f"[CELERY] index document count failed for {kind} {slug}: {exc}", flush=True)
        return 0


def _reflect_state(task_name: str, target: dict[str, object], *, progress_total: int = 0) -> dict[str, object]:
    state = batch_status.read_state(task_name)
    now = batch_status.now_text()
    if not state or not isinstance(state.get("items"), dict):
        state = batch_status.build_state(
            task_name,
            now.replace("-", "").replace(":", "").replace(" ", "_"),
            0,
            ROOT / "data" / "background_tasks" / f"{task_name}.csv",
            ROOT / "data" / "background_tasks",
        )
    state["task"] = task_name
    state["running"] = True
    state["running_label"] = "インデックス更新中"
    state["started_at"] = str(state.get("started_at") or now)
    state["last_started_at"] = now
    state["finished_at"] = ""
    state["worker_capacity"] = 1
    state["worker_active_count"] = 1
    state["worker_idle_count"] = 0
    state["index_capacity"] = 1
    state["index_active_count"] = 1
    state["index_idle_count"] = 0
    state["index_queue_count"] = 0
    items = state.setdefault("items", {})
    slug = str(target.get("slug") or "").strip()
    items[slug] = {
        "slug": slug,
        "code": str(target.get("code") or "").strip(),
        "name": str(target.get("name") or "").strip(),
        "full_name": str(target.get("full_name") or "").strip(),
        "system_type": str(target.get("system_type") or "").strip(),
        "host": "",
        "source_url": str(target.get("source_url") or "").strip(),
        "status": "running",
        "message": "インデックス更新中",
        "started_at": now,
        "finished_at": "",
        "updated_at": now,
        "progress_updated_at": "",
        "returncode": None,
        "pid": None,
        "progress_current": 0 if progress_total > 0 else None,
        "progress_total": progress_total if progress_total > 0 else None,
        "progress_unit": "document" if progress_total > 0 else "",
    }
    batch_status.refresh_counts(state)
    return state


def _run_index_update_command_with_status(kind: str, slug: str, state: dict[str, object], progress_total: int) -> None:
    command = _index_update_command("minutes" if kind == "gijiroku" else "reiki", slug)
    print(f"[CELERY] {kind} index update {slug}: {celery_runtime.command_text(command)}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **process_group_popen_kwargs(),
    )
    batch_status.update_item(state, slug, pid=int(process.pid))
    batch_status.write_state(f"{kind}_reflect", state)
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        print(line, flush=True)
        current = None
        match = INDEX_BULK_RE.match(line) or INDEX_DONE_RE.match(line)
        if match is not None:
            current = max(0, int(match.group("total") if "total" in match.groupdict() else match.group("count")))
        if current is not None:
            batch_status.update_item(
                state,
                slug,
                message="インデックス更新中",
                progress_current=min(current, progress_total) if progress_total > 0 else current,
                progress_total=progress_total if progress_total > 0 else current,
                progress_unit="document",
            )
            batch_status.write_state(f"{kind}_reflect", state)
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"{kind} index update {slug} failed with exit code {returncode}")


def _run_index_update_impl(kind: str, slug: str) -> None:
    slug = slug.strip()
    if slug == "":
        raise ValueError("slug is required")
    task_name = f"{kind}_reflect"
    target = _target_by_slug(kind, slug)
    progress_total = _index_document_total(kind, slug)
    state = _reflect_state(task_name, target, progress_total=progress_total)
    batch_status.write_state(task_name, state)
    ok = False
    message = ""
    try:
        _run_index_update_command_with_status(kind, slug, state, progress_total)
        ok = True
        message = "インデックス更新完了"
    except Exception as exc:
        message = str(exc)
        raise
    finally:
        finished_at = batch_status.now_text()
        batch_status.update_item(
            state,
            slug,
            status="ok" if ok else "failed",
            message=message,
            finished_at=finished_at,
            returncode=0 if ok else -1,
            progress_current=1 if ok else None,
            progress_total=1 if ok else None,
            progress_unit="municipality" if ok else "",
        )
        state["running"] = False
        state["finished_at"] = finished_at
        state["last_finished_at"] = finished_at
        state["worker_active_count"] = 0
        state["worker_idle_count"] = 1
        state["index_active_count"] = 0
        state["index_idle_count"] = 1
        batch_status.refresh_counts(state)
        batch_status.write_state(task_name, state)
        batch_status.invalidate_runtime_caches(include_homepage_payload=True)


def _run_gijiroku_backfill_impl() -> None:
    _run_command("gijiroku backfill", _gijiroku_backfill_command())


def _run_gijiroku_scrape_impl() -> None:
    _run_command("gijiroku scrape", _gijiroku_scrape_command())


def _run_reiki_backfill_impl() -> None:
    _run_command("reiki backfill", _reiki_backfill_command())


def _run_reiki_scrape_impl() -> None:
    _run_command("reiki scrape", _reiki_scrape_command())


def _recover_stale_metadata(task_name: str) -> None:
    if not celery_runtime.task_is_stale_running(task_name):
        return
    _run_command(f"{task_name} metadata reconcile", _metadata_reconcile_command(task_name))


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
        _recover_stale_metadata("gijiroku")
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


@app.task(name="tools.remote.celery_tasks.run_gijiroku_index_update")
def run_gijiroku_index_update(slug: str) -> dict[str, object]:
    _run_index_update_impl("gijiroku", slug)
    return {"ok": True, "task": "gijiroku_index_update", "slug": slug}


@app.task(name="tools.remote.celery_tasks.run_reiki_backfill")
def run_reiki_backfill() -> dict[str, object]:
    _run_reiki_backfill_impl()
    return {"ok": True, "task": "reiki_backfill"}


@app.task(bind=True, name="tools.remote.celery_tasks.run_reiki_cycle", max_retries=None)
def run_reiki_cycle(self) -> dict[str, object]:
    try:
        celery_runtime.clear_retry_marker("reiki")
        _recover_stale_metadata("reiki")
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


@app.task(name="tools.remote.celery_tasks.run_reiki_index_update")
def run_reiki_index_update(slug: str) -> dict[str, object]:
    _run_index_update_impl("reiki", slug)
    return {"ok": True, "task": "reiki_index_update", "slug": slug}
