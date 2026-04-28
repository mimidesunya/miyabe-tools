from __future__ import annotations

import hashlib
from pathlib import Path


SCRAPING_COMPOSE_PROJECT = "miyabe-tools-scraping"
SCRAPER_IMAGE_INPUTS = (
    "docker/scraper/Dockerfile",
    "tools/requirements-scraping.txt",
)


def yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if isinstance(value, str):
        escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
        return f"\"{escaped}\""
    if text == "" or any(ch in text for ch in ":#[]{}&,>*!|%@`'\" \t"):
        escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
        return f"\"{escaped}\""
    return text


def yaml_dump(value: object, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(yaml_dump(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(yaml_dump(item, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"


def build_scraping_compose(
    *,
    image_name: str,
    shared_data_dir: str,
    uid: str,
    gid: str,
    gijiroku_loop_seconds: int,
    reiki_loop_seconds: int,
    fail_sleep_seconds: int,
) -> str:
    common_environment = {
        "HOME": "/tmp",
        "PYTHONUNBUFFERED": "1",
        "CELERY_BROKER_URL": "redis://scraper-redis:6379/0",
        "CELERY_RESULT_BACKEND": "redis://scraper-redis:6379/1",
        "CELERY_TIMEZONE": "Asia/Tokyo",
        "CELERY_DISPATCH_INTERVAL_SECONDS": "60",
        "CELERY_GIJIROKU_SCHEDULE_SECONDS": str(gijiroku_loop_seconds),
        "CELERY_REIKI_SCHEDULE_SECONDS": str(reiki_loop_seconds),
        "SCRAPER_FAIL_SLEEP_SECONDS": str(fail_sleep_seconds),
        "SCRAPER_PYTHON_COMMAND": "python3",
        "SCRAPER_PHP_COMMAND": "php",
    }
    compose = {
        "name": SCRAPING_COMPOSE_PROJECT,
        "services": {
            "scraper-redis": {
                "image": "redis:7-alpine",
                "restart": "unless-stopped",
                "command": [
                    "redis-server",
                    "--save",
                    "60",
                    "1",
                    "--loglevel",
                    "warning",
                ],
            },
            "scraper-gijiroku": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "depends_on": ["scraper-redis"],
                "environment": {
                    **common_environment,
                    "SCRAPER_GIJIROKU_ACK_ROBOTS": "1",
                    "SCRAPER_GIJIROKU_REFLECT_PARALLEL": "4",
                    "SCRAPER_GIJIROKU_REBUILD_PARALLEL": "4",
                    "SCRAPER_GIJIROKU_PARALLEL": "8",
                    "SCRAPER_GIJIROKU_INDEX_PARALLEL": "1",
                    "SCRAPER_GIJIROKU_PER_HOST_PARALLEL": "1",
                    "SCRAPER_GIJIROKU_PER_HOST_START_INTERVAL": "2",
                },
                "volumes": [
                    ".:/workspace",
                    f"{shared_data_dir}/gijiroku:/workspace/data/gijiroku",
                ],
                "command": [
                    "celery",
                    "-A",
                    "tools.remote.celery_app:app",
                    "worker",
                    "--loglevel=INFO",
                    "--pool=solo",
                    "--concurrency=1",
                    "-Q",
                    "gijiroku",
                    "-n",
                    "gijiroku@%h",
                ],
            },
            "scraper-reiki": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "depends_on": ["scraper-redis"],
                "environment": {
                    **common_environment,
                    "SCRAPER_REIKI_CHECK_UPDATES": "1",
                    "SCRAPER_REIKI_REFLECT_PARALLEL": "4",
                    "SCRAPER_REIKI_PARALLEL": "8",
                    "SCRAPER_REIKI_PER_HOST_PARALLEL": "1",
                    "SCRAPER_REIKI_PER_HOST_START_INTERVAL": "2",
                },
                "volumes": [
                    ".:/workspace",
                    f"{shared_data_dir}/reiki:/workspace/data/reiki",
                ],
                "command": [
                    "celery",
                    "-A",
                    "tools.remote.celery_app:app",
                    "worker",
                    "--loglevel=INFO",
                    "--pool=solo",
                    "--concurrency=1",
                    "-Q",
                    "reiki",
                    "-n",
                    "reiki@%h",
                ],
            },
            "scraper-beat": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "depends_on": ["scraper-redis"],
                "environment": common_environment,
                "volumes": [
                    ".:/workspace",
                ],
                "command": [
                    "celery",
                    "-A",
                    "tools.remote.celery_app:app",
                    "beat",
                    "--loglevel=INFO",
                    "-s",
                    "/workspace/work/celery/celerybeat-schedule",
                ],
            },
        },
    }
    return yaml_dump(compose)


def scraper_image_source_hash(workspace_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"scraper-image-v1\n")
    for relative in SCRAPER_IMAGE_INPUTS:
        path = workspace_root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
