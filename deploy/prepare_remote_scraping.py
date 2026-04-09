#!/usr/bin/env python3
from __future__ import annotations

# スクレイパ専用の tools/data/work と compose 設定を remote へ配る。

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import (
    DEFAULT_SCRAPER_IMAGE_NAME,
    SCRAPING_COMPOSE_PROJECT,
    load_config,
    prepare_ssh_key,
    remote_scraper_cleanup_cmd,
    remote_scraping_compose_cmd,
    resolve_remote_dest_dir,
    resolve_remote_shared_data_dir,
    run_command,
    ssh_copy_content,
    ssh_exec,
    verify_scraping_services_running,
)


def rsync_dir(
    config: dict,
    ssh_base: str,
    local_path: str,
    remote_path: str,
    *,
    dry_run: bool,
    delete: bool,
) -> None:
    delete_flag = " --delete" if delete else ""
    dry_flag = " --dry-run" if dry_run else ""
    cmd = (
        f"rsync -avz{delete_flag}{dry_flag} "
        f"-e '{ssh_base}' {local_path} {config['user']}@{config['host']}:{remote_path}"
    )
    run_command(cmd, capture_output=False)


def rsync_file(
    config: dict,
    ssh_base: str,
    local_path: str,
    remote_path: str,
    *,
    dry_run: bool,
) -> None:
    dry_flag = " --dry-run" if dry_run else ""
    cmd = (
        f"rsync -avz{dry_flag} "
        f"-e '{ssh_base}' {local_path} {config['user']}@{config['host']}:{remote_path}"
    )
    run_command(cmd, capture_output=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="リモートスクレイピング用の tools/data/work を同期します。")
    parser.add_argument("config_file", nargs="?", default="deploy.json", help="デプロイ設定 JSON")
    parser.add_argument("--dry-run", action="store_true", help="実際には転送せず内容だけ確認する")
    parser.add_argument("--sync-gijiroku-work", action="store_true", help="work/gijiroku も追加同期する")
    parser.add_argument("--sync-reiki-work", action="store_true", help="work/reiki も追加同期する")
    parser.add_argument("--build-image", action="store_true", help="同期後にリモートでスクレイパ用イメージをビルドする")
    parser.add_argument(
        "--no-restart-services",
        action="store_true",
        help="同期後にスクレイパサービスを自動再起動しない",
    )
    parser.add_argument("--image-name", default=DEFAULT_SCRAPER_IMAGE_NAME, help="スクレイパ用 Docker イメージ名")
    parser.add_argument(
        "--gijiroku-loop-seconds",
        type=int,
        default=21600,
        help="会議録スクレイパの実行サイクル間隔（秒）",
    )
    parser.add_argument(
        "--reiki-loop-seconds",
        type=int,
        default=21600,
        help="例規スクレイパの実行サイクル間隔（秒）",
    )
    parser.add_argument(
        "--fail-sleep-seconds",
        type=int,
        default=900,
        help="スクレイパ失敗時の再試行待機秒数",
    )
    return parser


def remote_user_ids(config: dict) -> tuple[str, str]:
    uid = ssh_exec(config, "id -u").strip()
    gid = ssh_exec(config, "id -g").strip()
    if uid == "" or gid == "":
        raise RuntimeError("Could not determine remote uid/gid.")
    return uid, gid


# ここで作る compose は、Web アプリとは別に常駐するスクレイパ専用。
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
    compose = {
        "name": SCRAPING_COMPOSE_PROJECT,
        "services": {
            "scraper-gijiroku": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "environment": {
                    "HOME": "/tmp",
                    "PYTHONUNBUFFERED": "1",
                    "SCRAPER_LOOP_SECONDS": str(gijiroku_loop_seconds),
                    "SCRAPER_FAIL_SLEEP_SECONDS": str(fail_sleep_seconds),
                },
                "volumes": [
                    ".:/workspace",
                    f"{shared_data_dir}/gijiroku:/workspace/data/gijiroku",
                ],
                "command": [
                    "sh",
                    "./tools/remote/run_gijiroku_service.sh",
                    "--ack-robots",
                    "--parallel",
                    "8",
                    "--per-host-parallel",
                    "1",
                    "--per-host-start-interval",
                    "2",
                ],
            },
            "scraper-reiki": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "environment": {
                    "HOME": "/tmp",
                    "PYTHONUNBUFFERED": "1",
                    "SCRAPER_LOOP_SECONDS": str(reiki_loop_seconds),
                    "SCRAPER_FAIL_SLEEP_SECONDS": str(fail_sleep_seconds),
                },
                "volumes": [
                    ".:/workspace",
                    f"{shared_data_dir}/reiki:/workspace/data/reiki",
                ],
                "command": [
                    "sh",
                    "./tools/remote/run_reiki_service.sh",
                    "--parallel",
                    "8",
                    "--per-host-parallel",
                    "1",
                    "--per-host-start-interval",
                    "2",
                    "--check-updates",
                ],
            },
        }
    }
    return yaml_dump(compose)


def yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # compose の command/env は数値風の文字列も string として解釈させたいので、
    # 文字列値は常に明示的に quote して型ぶれを防ぐ。
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


def ensure_scraper_image(config: dict, dest_dir: str, image_name: str, *, build_image: bool) -> None:
    if build_image:
        ssh_exec(config, f"cd {dest_dir} && SCRAPER_IMAGE_NAME={image_name} sh ./tools/remote/build_scraper_image.sh")
        return

    image_present = ssh_exec(
        config,
        f"docker image inspect {image_name} >/dev/null 2>&1 && echo present || echo missing",
    ).strip()
    if image_present != "present":
        ssh_exec(config, f"cd {dest_dir} && SCRAPER_IMAGE_NAME={image_name} sh ./tools/remote/build_scraper_image.sh")


def restart_scraping_services(config: dict, dest_dir: str, image_name: str) -> None:
    ssh_exec(
        config,
        remote_scraper_cleanup_cmd(image_name)
        + "\n"
        + remote_scraping_compose_cmd(dest_dir, "up -d --force-recreate --remove-orphans"),
    )
    verify_scraping_services_running(config, dest_dir)


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config_file)
    original_key_path = config["key_path"]
    config["key_path"] = prepare_ssh_key(original_key_path)

    dest_dir = resolve_remote_dest_dir(config["dest_dir"])
    shared_data_dir = resolve_remote_shared_data_dir(config)
    ssh_base = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"

    ssh_exec(
        config,
        (
            f"mkdir -p {dest_dir}/tools/gijiroku {dest_dir}/tools/reiki {dest_dir}/tools/remote "
            f"{dest_dir}/data/municipalities {dest_dir}/work/gijiroku {dest_dir}/work/reiki "
            f"{dest_dir}/docker/scraper {dest_dir}/logs/scraping"
        ),
    )

    rsync_file(config, ssh_base, "tools/batch_status.py", f"{dest_dir}/tools/batch_status.py", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "tools/municipality_slugs.py", f"{dest_dir}/tools/municipality_slugs.py", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "tools/requirements-scraping.txt", f"{dest_dir}/tools/requirements-scraping.txt", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "data/config.json", f"{dest_dir}/data/config.json", dry_run=args.dry_run)
    rsync_dir(config, ssh_base, "data/municipalities/", f"{dest_dir}/data/municipalities/", dry_run=args.dry_run, delete=True)

    rsync_dir(config, ssh_base, "tools/gijiroku/", f"{dest_dir}/tools/gijiroku/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "tools/reiki/", f"{dest_dir}/tools/reiki/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "tools/remote/", f"{dest_dir}/tools/remote/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "docker/scraper/", f"{dest_dir}/docker/scraper/", dry_run=args.dry_run, delete=True)

    if args.sync_gijiroku_work:
        rsync_dir(config, ssh_base, "work/gijiroku/", f"{dest_dir}/work/gijiroku/", dry_run=args.dry_run, delete=False)
    if args.sync_reiki_work:
        rsync_dir(config, ssh_base, "work/reiki/", f"{dest_dir}/work/reiki/", dry_run=args.dry_run, delete=False)

    if not args.dry_run:
        uid, gid = remote_user_ids(config)
        compose_text = build_scraping_compose(
            image_name=args.image_name,
            shared_data_dir=shared_data_dir,
            uid=uid,
            gid=gid,
            gijiroku_loop_seconds=args.gijiroku_loop_seconds,
            reiki_loop_seconds=args.reiki_loop_seconds,
            fail_sleep_seconds=args.fail_sleep_seconds,
        )
        ssh_copy_content(config, compose_text + "\n", f"{dest_dir}/docker-compose.scraping.yml")

        if not args.no_restart_services:
            ensure_scraper_image(config, dest_dir, args.image_name, build_image=args.build_image)
            restart_scraping_services(config, dest_dir, args.image_name)

    print("\n=== Remote Commands ===")
    print(f"cd {dest_dir}")
    print(f"SCRAPER_IMAGE_NAME={args.image_name} sh ./tools/remote/build_scraper_image.sh")
    print(f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml ps")
    print(f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml logs -f scraper-gijiroku")
    print(f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml logs -f scraper-reiki")
    print(f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml restart scraper-gijiroku scraper-reiki")
    print(
        "nohup sh ./tools/remote/run_gijiroku_remote.sh --ack-robots --parallel 8 --per-host-parallel 1 --per-host-start-interval 2 "
        "> logs/scraping/gijiroku.out 2>&1 &"
    )
    print(
        "nohup sh ./tools/remote/run_reiki_remote.sh --parallel 8 --per-host-parallel 1 --per-host-start-interval 2 --check-updates "
        "> logs/scraping/reiki.out 2>&1 &"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
