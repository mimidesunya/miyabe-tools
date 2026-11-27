#!/usr/bin/env python3
"""行政文書の公開用の束（work/gyosei/public）を本番の data/gyosei/ へ送る。

束は `tools/gyosei/build_public.py` が I:\\行政文書 から作る。リポジトリにも deploy.py の同期にも入れない
（PDF が重いのと、元が手元の I: にしか無いため）。nginx は /data/gyosei/<id>/<nn>.pdf を出し、
case.json と <nn>.json は PHP（app/gyosei/）だけが読む。

    python deploy/sync_gyosei.py deploy.json --dry-run
    python deploy/sync_gyosei.py deploy.json

--delete を付けるので、publish.json から外した案件は本番からも消える。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(WORKSPACE_ROOT))
sys.path.append(str(WORKSPACE_ROOT / "deploy"))

import deploy as deploy_module  # noqa: E402
from sync_ocr_tool import rsync_path_text, rsync_ssh_base  # noqa: E402

SOURCE = WORKSPACE_ROOT / "work" / "gyosei" / "public"
REMOTE_RELATIVE = "data/gyosei"


def main() -> int:
    parser = argparse.ArgumentParser(description="行政文書の公開用の束を本番へ送る。")
    parser.add_argument("config_file", help="deploy.json のパス")
    parser.add_argument("--dry-run", action="store_true", help="転送せず対象だけ表示する")
    args = parser.parse_args()

    cases = sorted(path for path in SOURCE.glob("*/case.json")) if SOURCE.is_dir() else []
    if not cases:
        print(f"[ERROR] 束がありません: {SOURCE}（先に python tools/gyosei/build_public.py）")
        return 1

    config = deploy_module.load_config(args.config_file)
    deploy_module.prepare_ssh_key_from_config(config)
    destination = f"{config['dest_dir']}/{REMOTE_RELATIVE}"
    print(f"同期元: {SOURCE}（{len(cases)} 案件）")
    print(f"同期先: {config['user']}@{config['host']}:~/{destination}")

    deploy_module.ssh_exec(config, f"mkdir -p {destination} && chmod 2755 {destination}")
    dry_flag = " --dry-run" if args.dry_run else ""
    command = (
        f"rsync -rtvz --delete{dry_flag} "
        f"--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r "
        f"-e \"{rsync_ssh_base(config)}\" "
        f"{rsync_path_text(SOURCE)}/ {config['user']}@{config['host']}:{destination}/"
    )
    deploy_module.run_command(command, capture_output=False)
    if args.dry_run:
        print("（--dry-run のため転送していません）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
