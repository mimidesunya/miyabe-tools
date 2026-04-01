#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import load_config, prepare_ssh_key, resolve_remote_dest_dir, run_command, ssh_exec


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
    parser = argparse.ArgumentParser(description="リモートスクレイピング用の tools/work を同期します。")
    parser.add_argument("config_file", nargs="?", default="deploy.json", help="デプロイ設定 JSON")
    parser.add_argument("--dry-run", action="store_true", help="実際には転送せず内容だけ確認する")
    parser.add_argument("--sync-gijiroku-work", action="store_true", help="work/gijiroku も追加同期する")
    parser.add_argument("--sync-reiki-work", action="store_true", help="work/reiki も追加同期する")
    parser.add_argument("--build-image", action="store_true", help="同期後にリモートでスクレイパ用イメージをビルドする")
    parser.add_argument("--image-name", default="miyabe-tools-scraper", help="スクレイパ用 Docker イメージ名")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config_file)
    original_key_path = config["key_path"]
    config["key_path"] = prepare_ssh_key(original_key_path)

    dest_dir = resolve_remote_dest_dir(config["dest_dir"])
    ssh_base = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"

    ssh_exec(
        config,
        (
            f"mkdir -p {dest_dir}/tools/gijiroku {dest_dir}/tools/reiki {dest_dir}/tools/remote "
            f"{dest_dir}/work/municipalities {dest_dir}/work/gijiroku {dest_dir}/work/reiki "
            f"{dest_dir}/docker/scraper {dest_dir}/logs/scraping"
        ),
    )

    rsync_file(config, ssh_base, "tools/batch_status.py", f"{dest_dir}/tools/batch_status.py", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "tools/municipality_slugs.py", f"{dest_dir}/tools/municipality_slugs.py", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "tools/requirements-scraping.txt", f"{dest_dir}/tools/requirements-scraping.txt", dry_run=args.dry_run)
    rsync_file(config, ssh_base, "data/config.json", f"{dest_dir}/data/config.json", dry_run=args.dry_run)

    rsync_dir(config, ssh_base, "tools/gijiroku/", f"{dest_dir}/tools/gijiroku/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "tools/reiki/", f"{dest_dir}/tools/reiki/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "tools/remote/", f"{dest_dir}/tools/remote/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "work/municipalities/", f"{dest_dir}/work/municipalities/", dry_run=args.dry_run, delete=True)
    rsync_dir(config, ssh_base, "docker/scraper/", f"{dest_dir}/docker/scraper/", dry_run=args.dry_run, delete=True)

    if args.sync_gijiroku_work:
        rsync_dir(config, ssh_base, "work/gijiroku/", f"{dest_dir}/work/gijiroku/", dry_run=args.dry_run, delete=False)
    if args.sync_reiki_work:
        rsync_dir(config, ssh_base, "work/reiki/", f"{dest_dir}/work/reiki/", dry_run=args.dry_run, delete=False)

    if args.build_image and not args.dry_run:
        ssh_exec(config, f"cd {dest_dir} && SCRAPER_IMAGE_NAME={args.image_name} sh ./tools/remote/build_scraper_image.sh")

    print("\n=== Remote Commands ===")
    print(f"cd {dest_dir}")
    print(f"SCRAPER_IMAGE_NAME={args.image_name} sh ./tools/remote/build_scraper_image.sh")
    print(
        "nohup sh ./tools/remote/run_gijiroku_remote.sh --ack-robots --parallel 4 --per-host-parallel 1 "
        "> logs/scraping/gijiroku.out 2>&1 &"
    )
    print(
        "nohup sh ./tools/remote/run_reiki_remote.sh --parallel 4 --per-host-parallel 1 --check-updates "
        "> logs/scraping/reiki.out 2>&1 &"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
