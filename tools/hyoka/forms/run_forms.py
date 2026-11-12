#!/usr/bin/env python3
"""団体ごとの読み取りプログラムを Codex に書かせる。

作業の置き場に団体ごとのフォルダを作り（升目、原本、道具、依頼書、調査の記録）、Codex に `reader.py` を書かせる。
書いたプログラムは `check.py` で確かめ、同じ様式の文書を AI なしで全部読む。依頼書は `brief.md`。

    python tools/hyoka/forms/run_forms.py fetch --source DIR --slug 40348-hisayama-cho            # 本番から升目と見本の原本を写す
    python tools/hyoka/forms/run_forms.py prepare --work WORK --slug 40348-hisayama-cho --source DIR --survey survey_final.jsonl
    python tools/hyoka/forms/run_forms.py run --work WORK --parallel 3 --model gpt-6.1-sol --effort medium --max-quota 80
    python tools/hyoka/forms/run_forms.py report --work WORK
    python tools/hyoka/forms/run_forms.py apply --work WORK --layouts-root /data/hyoka2-ai --out /data/hyoka2-records   # 本番で全文書に当てる

`--source` の下は `<slug>/layouts/<doc_id>.json.gz` と `<slug>/pdf/<doc_id>.pdf`（`run_ai.py grids` の置き場と取得の置き場）。
Codex に渡す升目は見本（年度ごとに散らして最大 80 件）で、書いた読み取りは `apply` で本番の全文書に当てる。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "survey"))

import run_survey  # noqa: E402

TOOLS = {"cells.py": HERE / "cells.py", "check.py": HERE / "check.py", "score.py": HERE.parent / "format2" / "score.py",
         "extract.py": HERE.parent / "format2" / "extract.py", "example_reader.py": HERE / "readers" / "14130-kawasaki-shi.py",
         "brief.md": HERE / "brief.md"}
TRUTH = HERE.parent / "format2" / "truth"
PROMPT = "作業フォルダの brief.md を読み、その指示どおりに最後まで実行してください。"
TIMEOUT = 3 * 3600


def survey_record(path: Path, code: str) -> dict:
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["jis_code"] == code:
                return {key: row.get(key) for key in ("jis_code", "name", "verdict", "forms", "samples", "year_pages", "collection_notes")}
    return {}


def cmd_prepare(args: argparse.Namespace) -> None:
    for slug in args.slug:
        prepare_one(args, slug)


def prepare_one(args: argparse.Namespace, slug: str) -> None:
    folder = Path(args.work) / slug
    (folder / "layouts").mkdir(parents=True, exist_ok=True)
    source = Path(args.source) / slug
    layouts = sorted((source / "layouts").glob("*.json.gz"))
    for path in layouts:
        shutil.copyfile(path, folder / "layouts" / path.name)
    pdfs = source / "pdf"
    if pdfs.is_dir():
        (folder / "pdf").mkdir(exist_ok=True)
        for path in pdfs.iterdir():
            shutil.copyfile(path, folder / "pdf" / path.name)
    for name, path in TOOLS.items():
        shutil.copyfile(path, folder / name)
    record = survey_record(Path(args.survey), slug[:5])
    (folder / "survey.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{slug}: 升目 {len(layouts)} 文書")


def folders(work: Path) -> list[Path]:
    return sorted(path for path in work.iterdir() if path.is_dir() and (path / "brief.md").is_file())


def cmd_run(args: argparse.Namespace) -> None:
    codex = run_survey.find_codex(args.codex)
    pending = [folder for folder in folders(Path(args.work)) if not (folder / "notes.md").is_file()]
    stopped: list[str] = []

    def work_on(folder: Path) -> None:
        if stopped:
            return
        # 週の枠を使い切るとクレジット（有料）から引かれるので、その手前で止める
        used = run_survey.quota_percent(run_survey.account_limits(codex))
        if used is None or used >= args.max_quota:
            stopped.append(f"週の枠 {used}%")
            return
        command = [codex, "exec", "--skip-git-repo-check", "-c", f"model_reasoning_effort={args.effort}", "--approve-for-me",
                   "-m", args.model, "-C", str(folder), "-o", str(folder / "final.txt"), PROMPT]
        started = time.time()
        with open(folder / "codex.log", "a", encoding="utf-8") as log:
            try:
                code = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=TIMEOUT, check=False).returncode
            except subprocess.TimeoutExpired:
                code = -1
        print(f"{folder.name} 終了 {code}、{(time.time() - started) / 60:.0f} 分", flush=True)

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        list(pool.map(work_on, pending))
    if stopped:
        raise SystemExit(f"止めた理由: {stopped}")


REMOTE = "miyabe@risu.miya.be"
REMOTE_TOOLS = "/mnt/big/miyabe-tools/work/hyoka2_tools"
SSH = ["ssh", "-i", str(Path.home() / ".ssh" / "to-server-ed25519"), "-o", "BatchMode=yes", REMOTE]


def cmd_fetch(args: argparse.Namespace) -> None:
    """本番で升目と見本の原本を選んで写す。升目づくりの容器（PyMuPDF 不要、python3 だけ）で選ぶ。"""
    slugs = args.slug or [line.strip() for line in Path(args.slugs).read_text(encoding="utf-8").split() if line.strip()]
    stage = "/tmp/hyoka_forms_stage"
    subprocess.run(["scp", "-q", "-i", SSH[2], str(HERE / "remote_pick.py"), f"{REMOTE}:{REMOTE_TOOLS}/tools/hyoka/forms/remote_pick.py"], check=True)
    picks = " ".join(f"--slug {slug}" for slug in slugs)
    if args.include_truth:  # 正解のある文書を必ず見本に入れる（report で突き合わせられるように）
        for path in sorted(TRUTH.glob("*.json")):
            source = json.loads(path.read_text(encoding="utf-8")).get("source") or {}
            if source.get("slug") in slugs and source.get("doc_id"):
                picks += f" --include {source['doc_id']}"
    script = (f"set -e; rm -rf {stage}; python3 {REMOTE_TOOLS}/tools/hyoka/forms/remote_pick.py --work-root /mnt/big/miyabe-tools/work/hyoka2 "
              f"--layouts-root /mnt/big/miyabe-tools/work/hyoka2-ai --stage {stage} {picks}; tar czf {stage}.tgz -C {stage} .; rm -rf {stage}")
    subprocess.run(SSH + [script], check=True)
    Path(args.source).mkdir(parents=True, exist_ok=True)
    local = Path(args.source) / "_fetch.tgz"
    subprocess.run(["scp", "-q", "-i", SSH[2], f"{REMOTE}:{stage}.tgz", str(local)], check=True)
    subprocess.run(SSH + [f"rm -f {stage}.tgz"], check=True)
    subprocess.run(["tar", "xzf", local.name], cwd=args.source, check=True)
    local.unlink()


def cmd_apply(args: argparse.Namespace) -> None:
    """書いた読み取りを全文書に当て、団体ごとに記録と確かめの結果を書く。読み取りの置き場は WORK/<slug>/reader.py。"""
    for folder in folders(Path(args.work)):
        reader = folder / "reader.py"
        layouts = Path(args.layouts_root) / folder.name / "layouts"
        if not reader.is_file() or not layouts.is_dir():
            continue
        out = Path(args.out) / folder.name
        out.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(HERE / "check.py"), "--layouts", str(layouts), "--reader", str(reader),
                   "--out", str(out / "records.jsonl"), "--show", "20"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
        (out / "check.txt").write_text(result.stdout + result.stderr[-4000:], encoding="utf-8")
        print(f"{folder.name}: {result.stdout.splitlines()[0] if result.stdout else result.stderr[-200:]}", flush=True)


def cmd_report(args: argparse.Namespace) -> None:
    for folder in folders(Path(args.work)):
        reader = folder / "reader.py"
        if not reader.is_file():
            print(f"== {folder.name}: reader.py が無い")
            continue
        command = [sys.executable, str(folder / "check.py"), "--layouts", str(folder / "layouts"), "--reader", str(reader),
                   "--truth", str(TRUTH), "--show", str(args.show)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", cwd=folder, check=False)
        print(f"== {folder.name}")
        print(result.stdout.strip() or result.stderr.strip()[-800:])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["fetch", "prepare", "run", "report", "apply"])
    parser.add_argument("--work", default="")
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--slugs", default="", help="fetch: 団体の slug を並べたファイル")
    parser.add_argument("--include-truth", action="store_true", help="fetch: 正解のある文書を必ず見本に入れる")
    parser.add_argument("--layouts-root", default="", help="apply: 升目の置き場（<slug>/layouts）")
    parser.add_argument("--out", default="", help="apply: 記録の置き場")
    parser.add_argument("--source", default="")
    parser.add_argument("--survey", default="")
    parser.add_argument("--codex", default="")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--max-quota", type=float, default=80.0, help="週の枠がこの %% に達したら新しい団体を始めない")
    parser.add_argument("--show", type=int, default=3)
    args = parser.parse_args()
    {"fetch": cmd_fetch, "prepare": cmd_prepare, "run": cmd_run, "report": cmd_report, "apply": cmd_apply}[args.command](args)


if __name__ == "__main__":
    main()
