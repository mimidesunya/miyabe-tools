#!/usr/bin/env python3
from __future__ import annotations

# スクレイパ専用の tools/data/work と compose 設定を remote へ配る。

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraping_stack import (
    SCRAPING_COMPOSE_PROJECT,
    build_scraping_compose,
    scraper_image_source_hash,
)
from deploy import (
    DEFAULT_SCRAPER_IMAGE_NAME,
    load_config,
    prepare_ssh_key_from_config,
    remote_file_text,
    remote_scraper_image_stamp_path,
    remote_scraper_cleanup_cmd,
    remote_scraping_compose_cmd,
    resolve_remote_dest_dir,
    resolve_remote_shared_data_dir,
    run_command,
    ssh_copy_content,
    ssh_exec,
    verify_scraping_services_running,
    cleanup_legacy_search_artifacts,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


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
        f"rsync -avz --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r{delete_flag}{dry_flag} "
        f"--exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' "
        f"-e \"{ssh_base}\" {local_path} {config['user']}@{config['host']}:{remote_path}"
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
        f"rsync -avz --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r{dry_flag} "
        f"-e \"{ssh_base}\" {local_path} {config['user']}@{config['host']}:{remote_path}"
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


def ensure_scraper_image(config: dict, dest_dir: str, image_name: str, *, build_image: bool) -> None:
    expected_hash = scraper_image_source_hash(WORKSPACE_ROOT)
    stamp_path = remote_scraper_image_stamp_path(dest_dir)
    image_present = ssh_exec(
        config,
        f"docker image inspect {image_name} >/dev/null 2>&1 && echo present || echo missing",
    ).strip()
    current_hash = remote_file_text(config, stamp_path).strip()
    if build_image or image_present != "present" or current_hash != expected_hash:
        ssh_exec(config, f"cd {dest_dir} && SCRAPER_IMAGE_NAME={image_name} sh ./tools/remote/build_scraper_image.sh")
        ssh_exec(config, f"mkdir -p {dest_dir}/work/celery")
        ssh_copy_content(config, expected_hash + "\n", stamp_path)


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
    prepare_ssh_key_from_config(config)

    dest_dir = resolve_remote_dest_dir(config["dest_dir"])
    shared_data_dir = resolve_remote_shared_data_dir(config)
    rsync_key_path = str(config["key_path"]).replace("\\", "/")
    if os.name == "nt" and len(rsync_key_path) >= 3 and rsync_key_path[1:3] == ":/":
        rsync_key_path = f"/cygdrive/{rsync_key_path[0].lower()}/{rsync_key_path[3:]}"
    ssh_binary = "/usr/bin/ssh" if os.name == "nt" else "ssh"
    ssh_base = f"{ssh_binary} -i {rsync_key_path} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"

    ssh_exec(
        config,
        (
            f"mkdir -p {dest_dir}/tools {dest_dir}/lib/python {dest_dir}/data/municipalities "
            f"{dest_dir}/work/gijiroku {dest_dir}/work/reiki {dest_dir}/work/celery "
            f"{dest_dir}/docker/scraper {dest_dir}/logs/scraping"
        ),
    )

    rsync_file(config, ssh_base, ".dockerignore", f"{dest_dir}/.dockerignore", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "data/config.json", f"{dest_dir}/data/config.json", dry_run=args.dry_run)
    rsync_dir(config, ssh_base, "data/municipalities/", f"{dest_dir}/data/municipalities/", dry_run=args.dry_run, delete=True)

    rsync_dir(config, ssh_base, "tools/", f"{dest_dir}/tools/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "lib/python/", f"{dest_dir}/lib/python/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "docker/scraper/", f"{dest_dir}/docker/scraper/", dry_run=args.dry_run, delete=True)

    if args.sync_gijiroku_work:
        rsync_dir(config, ssh_base, "work/gijiroku/", f"{dest_dir}/work/gijiroku/", dry_run=args.dry_run, delete=False)
    if args.sync_reiki_work:
        rsync_dir(config, ssh_base, "work/reiki/", f"{dest_dir}/work/reiki/", dry_run=args.dry_run, delete=False)

    if not args.dry_run:
        cleanup_legacy_search_artifacts(config, dest_dir, shared_data_dir)
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
    print(f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml logs -f scraper-beat")
    print(f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml restart scraper-gijiroku scraper-reiki scraper-beat")
    print(
        "docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml exec scraper-gijiroku "
        "python3 tools/remote/celery_enqueue.py gijiroku-cycle"
    )
    print(
        "docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml exec scraper-reiki "
        "python3 tools/remote/celery_enqueue.py reiki-cycle"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
