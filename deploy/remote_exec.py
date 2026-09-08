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
        "--script-file",
        help="リモートへ流すローカルのスクリプトファイル。command の代わりに指定する。",
    )
    parser.add_argument(
        "--python",
        action="store_true",
        help="--script-file を、リモートの python3 に標準入力から渡して実行する。",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="リモートで実行する shell コマンド",
    )
    return parser


# リモートでは sh -s に流し込むので、python は here-document で渡す。
# 本文に現れない綴りを終端に使う。
PYTHON_HEREDOC_MARKER = "MIYABE_REMOTE_PY_EOF"


def main() -> int:
    args = build_parser().parse_args()
    command_parts = list(args.command)
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    command = " ".join(command_parts).strip()

    if args.script_file:
        if command != "":
            print("Error: --script-file と command は同時に指定できません。", file=sys.stderr)
            return 2
        script = Path(args.script_file).read_text(encoding="utf-8")
        if args.python:
            if PYTHON_HEREDOC_MARKER in script:
                print(f"Error: スクリプトに {PYTHON_HEREDOC_MARKER} を含められません。", file=sys.stderr)
                return 2
            command = (
                f"python3 - <<'{PYTHON_HEREDOC_MARKER}'\n"
                f"{script}\n"
                f"{PYTHON_HEREDOC_MARKER}\n"
            )
        else:
            command = script
    elif args.python:
        print("Error: --python は --script-file と一緒に使ってください。", file=sys.stderr)
        return 2

    if command.strip() == "":
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
