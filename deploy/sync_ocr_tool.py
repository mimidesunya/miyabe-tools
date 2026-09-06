#!/usr/bin/env python3
"""OCR ツール（NDLOCR-Lite）をリモートへ同期する。

文字情報のない PDF を本文にするために使う。国立国会図書館が CC BY 4.0 で
公開しているもので、GPU を必要とせず、モデル（ONNX 4 本・157MB）を同梱して
いるので実行時のダウンロードも API キーも要らない。

**リポジトリには入れない。** モデルを含めて 200MB あり、git にも Docker の
build context にも入れたくない。サーバーの `vendor/ndlocr-lite` へ直接置く。
スクレイパは `.` を `/workspace` へ mount しているので、そのまま見える。

    python deploy/sync_ocr_tool.py deploy.json
    python deploy/sync_ocr_tool.py deploy.json --source /path/to/ndlocr-lite --dry-run

入手元: https://github.com/ndl-lab/ndlocr-lite
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(WORKSPACE_ROOT))
sys.path.append(str(WORKSPACE_ROOT / "deploy"))

import deploy as deploy_module  # noqa: E402

# 手元の既定の置き場所。mimi-ocr が下請けとして持っているものを使う。
DEFAULT_SOURCES = (
    WORKSPACE_ROOT.parent / "mimi-ocr" / ".mimi-tools" / "ndlocr-lite",
    WORKSPACE_ROOT / "vendor" / "ndlocr-lite",
)
REMOTE_RELATIVE = "vendor/ndlocr-lite"
# 実行に要らないもの。見本画像だけで 9.6MB、GUI と学習用も要らない。
EXCLUDES = ("__pycache__/", "*.pyc", "resource/", "train/", "ndlocr-lite-gui/", ".git/")


def find_source(configured: str) -> Path | None:
    if configured:
        path = Path(configured)
        return path if (path / "src" / "ocr.py").is_file() else None
    for candidate in DEFAULT_SOURCES:
        if (candidate / "src" / "ocr.py").is_file():
            return candidate
    return None


def rsync_path_text(path: Path) -> str:
    """rsync（cygwin 版）へ渡せる形にする。

    Windows の `F:\dev\...` をそのまま渡すとホスト指定と誤読される。
    deploy.py が鍵のパスでやっているのと同じ変換を、同期元にも当てる。
    """
    text = str(path).replace("\\", "/")
    if os.name == "nt" and len(text) >= 3 and text[1:3] == ":/":
        return f"/cygdrive/{text[0].lower()}/{text[3:]}"
    return text


def rsync_ssh_base(config: dict) -> str:
    key_path = str(config["key_path"]).replace("\\", "/")
    if os.name == "nt" and len(key_path) >= 3 and key_path[1:3] == ":/":
        key_path = f"/cygdrive/{key_path[0].lower()}/{key_path[3:]}"
    ssh_binary = "/usr/bin/ssh" if os.name == "nt" else "ssh"
    return f"{ssh_binary} -i {key_path} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NDLOCR-Lite をリモートへ同期する。")
    parser.add_argument("config_file", help="deploy.json のパス")
    parser.add_argument("--source", default="", help="手元の ndlocr-lite の場所")
    parser.add_argument("--dry-run", action="store_true", help="転送せず対象だけ表示する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = find_source(str(args.source).strip())
    if source is None:
        print("[ERROR] NDLOCR-Lite が見つかりません。--source で場所を指定してください。")
        print("        入手元: https://github.com/ndl-lab/ndlocr-lite")
        return 1

    config = deploy_module.load_config(args.config_file)
    deploy_module.prepare_ssh_key_from_config(config)
    destination = f"{config['dest_dir']}/{REMOTE_RELATIVE}"

    models = sorted((source / "src" / "model").glob("*.onnx"))
    print(f"同期元: {source}")
    print(f"同期先: {config['user']}@{config['host']}:~/{destination}")
    print(f"モデル: {len(models)}本 / {sum(m.stat().st_size for m in models) // (1024 * 1024)}MB")

    deploy_module.ssh_exec(config, f"mkdir -p {destination}")
    excludes = "".join(f" --exclude='{pattern}'" for pattern in EXCLUDES)
    dry_flag = " --dry-run" if args.dry_run else ""
    command = (
        f"rsync -avz --delete{dry_flag}{excludes} "
        f"--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r "
        f"-e \"{rsync_ssh_base(config)}\" "
        f"{rsync_path_text(source)}/ {config['user']}@{config['host']}:{destination}/"
    )
    deploy_module.run_command(command, capture_output=False)
    if args.dry_run:
        print("（--dry-run のため転送していません）")
        return 0
    print("完了しました。スクレイパを再起動すると OCR が使えます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
