#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import load_config, prepare_ssh_key_from_config, ssh_exec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="deploy.json の SSH 鍵設定を使ってリモートコマンドを実行します。"
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default="deploy.json",
        help="デプロイ設定 JSON",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="リモートで実行する shell コマンド",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    command_parts = list(args.command)
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    command = " ".join(command_parts).strip()
    if command == "":
        print("Error: remote command is required.", file=sys.stderr)
        return 2

    config = load_config(args.config_file)
    prepare_ssh_key_from_config(config)
    output = ssh_exec(config, command)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
