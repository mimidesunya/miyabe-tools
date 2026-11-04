#!/usr/bin/env python3
"""自治体ごとに事務事業評価の実施と公開の実物を確かめる調査を、Codex に束で頼む。

以前の `discover_hyoka_urls.py` はページの言葉で機械的に判定して、取り違えが多かった。
この調査は担当が公式サイトの実物のシートを開いて判定する。

順番は総務省の個票（`data/municipalities/hyoka_census_mic.tsv`）で決める。個票は自己申告で
絶対ではないので、飛ばす団体は作らず、北方領土の 6 村の他は全部調べる。
調査の定義と結果の形は `brief.md`。

    python tools/hyoka/survey/run_survey.py plan --work WORK --batch 8
    python tools/hyoka/survey/run_survey.py run --work WORK --parallel 3 --limit 2
    python tools/hyoka/survey/run_survey.py status --work WORK
    python tools/hyoka/survey/run_survey.py verify --work WORK
    python tools/hyoka/survey/run_survey.py merge --work WORK --out survey.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "municipalities"
BRIEF = Path(__file__).with_name("brief.md")

TARGET_COLUMNS = ["jis_code", "name", "mic_introduction_r7", "mic_publication_r7", "old_ledger_status", "old_ledger_url", "homepage"]
PROMPT = "作業フォルダの brief.md を読み、その指示どおりに最後まで実行してください。"
BATCH_TIMEOUT = 4 * 3600
LIMITS_TIMEOUT = 60
USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be/)"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # 自治体のサイトは証明書の不備が多い。開けるかだけを見る

CONDUCTS = {"yes", "no", "abolished", "unknown"}
PUBLISHES = {"all_projects", "target_all", "excerpt", "none", "unknown"}
# 事業ごとの情報が読める判定。これらは入口と実物の確認が要る
PROJECT_VERDICTS = {"project_sheets", "project_table", "project_rows_in_policy"}
VERDICTS = PROJECT_VERDICTS | {"summary_only", "policy_level_only", "not_evaluation", "not_found"}
CONFIDENCE = {"high", "medium", "low"}
REQUIRED = ["jis_code", "name", "conducts", "publishes", "verdict", "entry_url", "year_pages", "forms", "samples", "other_sources", "confidence"]


def read_tsv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def read_homepages(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8", newline="") as stream:
        return {row["jis_code"]: row["url"] for row in csv.DictReader(stream)}


def priority(row: dict[str, str]) -> int:
    """公開の見込みが高い順。収集済みの団体の見直しも早めに置く。"""
    intro = row["mic_introduction_r7"]
    publication = row["mic_publication_r7"]
    if intro == "導入済み" and publication in ("全て公表", "一部公表"):
        return 0
    if row["ledger_status"] == "enabled":
        return 1
    if intro == "導入済み":
        return 2
    if intro in ("試行中", "検討中（時期決定）", "検討中（時期未定）"):
        return 3
    if intro == "廃止":
        return 4
    return 5


def build_targets() -> list[dict[str, str]]:
    census = read_tsv(DATA / "hyoka_census_mic.tsv")
    ledger = {row["jis_code"]: row for row in read_tsv(DATA / "hyoka_system_urls.tsv")}
    homepages = read_homepages(DATA / "municipality_homepages.csv")
    kind_order = {"都道府県": 0, "指定都市": 1, "市区町村": 2}
    rows = [row for row in census if row["kind"] in kind_order]
    rows.sort(key=lambda row: (priority(row), kind_order[row["kind"]], row["jis_code"]))
    return [
        {
            "jis_code": row["jis_code"],
            "name": row["name"],
            "mic_introduction_r7": row["mic_introduction_r7"],
            "mic_publication_r7": row["mic_publication_r7"],
            "old_ledger_status": row["ledger_status"],
            "old_ledger_url": ledger.get(row["jis_code"], {}).get("url", ""),
            "homepage": homepages.get(row["jis_code"], ""),
        }
        for row in rows
    ]


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TARGET_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def batch_dirs(work: Path) -> list[Path]:
    return sorted(path for path in (work / "batches").glob("[0-9]*") if path.is_dir())


def read_jsonl(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    return {row["jis_code"]: row for row in rows}


def cmd_plan(args: argparse.Namespace) -> None:
    """束にしていない団体を、続きの番号の束にする。調べ直しは --codes で団体を絞り、--previous で前回の結果を添える。"""
    work = Path(args.work)
    folders = batch_dirs(work)
    planned = {row["jis_code"] for folder in folders for row in read_tsv(folder / "all_targets.tsv")}
    targets = [row for row in build_targets() if row["jis_code"] not in planned]
    if args.codes:
        codes = set(Path(args.codes).read_text(encoding="utf-8").split())
        targets = [row for row in targets if row["jis_code"] in codes]
    previous = read_jsonl(Path(args.previous)) if args.previous else {}
    first = int(folders[-1].name) if folders else 0
    for number, start in enumerate(range(0, len(targets), args.batch), first + 1):
        folder = work / "batches" / f"{number:04d}"
        folder.mkdir(parents=True)
        batch = targets[start : start + args.batch]
        write_tsv(folder / "all_targets.tsv", batch)
        if previous:
            lines = [json.dumps(previous[row["jis_code"]], ensure_ascii=False) for row in batch if row["jis_code"] in previous]
            (folder / "previous.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(targets)} 団体を {len(batch_dirs(work)) - len(folders)} 束にしました")


def problems(row: dict, codes: set[str]) -> list[str]:
    """結果の 1 行の形を確かめる。開いたという URL の実在は verify で見る。"""
    found = [f"{key} がない" for key in REQUIRED if key not in row]
    if found:
        return found
    if row["jis_code"] not in codes:
        found.append("対象でない団体")
    if row["conducts"] not in CONDUCTS:
        found.append(f"conducts: {row['conducts']}")
    if row["publishes"] not in PUBLISHES:
        found.append(f"publishes: {row['publishes']}")
    if row["verdict"] not in VERDICTS:
        found.append(f"verdict: {row['verdict']}")
    if row["confidence"] not in CONFIDENCE:
        found.append(f"confidence: {row['confidence']}")
    if row["verdict"] in PROJECT_VERDICTS:
        for key in ("entry_url", "year_pages", "forms", "samples"):
            if not row[key]:
                found.append(f"事業の情報ありなのに {key} が空")
    return found


def read_results(folder: Path) -> tuple[dict[str, dict], list[str]]:
    """束の結果を読む。形の合う行だけを済みとする。"""
    path = folder / "results.jsonl"
    if not path.exists():
        return {}, []
    codes = {row["jis_code"] for row in read_tsv(folder / "all_targets.tsv")}
    good: dict[str, dict] = {}
    bad: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            bad.append(f"{path}:{number}: {error}")
            continue
        found = problems(row, codes)
        if found:
            bad.append(f"{path}:{number}: {'、'.join(found)}")
            continue
        good[row["jis_code"]] = row
    return good, bad


def remaining(folder: Path) -> list[dict[str, str]]:
    done, _ = read_results(folder)
    return [row for row in read_tsv(folder / "all_targets.tsv") if row["jis_code"] not in done]


def read_reply(stream, request_id: int) -> dict | None:
    """app-server の出力から、その要求への返事を探す。"""
    for line in stream:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == request_id:
            return message.get("result")
    return None


def account_limits(codex: str) -> dict | None:
    """Codex の app-server に今の枠を尋ねる。記録のファイルはリセットの前の値が残るので使わない。"""
    process = subprocess.Popen(
        [codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace"
    )
    watchdog = threading.Timer(LIMITS_TIMEOUT, process.kill)
    watchdog.start()

    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    try:
        send({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "hyoka-survey", "title": "hyoka-survey", "version": "0.0.1"}}})
        read_reply(process.stdout, 0)
        send({"method": "initialized"})
        send({"id": 1, "method": "account/rateLimits/read", "params": {"excludeResetCreditDetails": True}})
        return read_reply(process.stdout, 1)
    except OSError:
        return None
    finally:
        watchdog.cancel()
        process.stdin.close()
        process.terminate()


def quota_percent(limits: dict | None) -> float | None:
    """週の枠の使用率。通常の利用が止まっていれば 100。読めなければ None。"""
    if not limits:
        return None
    if limits.get("ordinaryUsageAllowed") is False:
        return 100.0
    primary = (limits.get("rateLimits") or {}).get("primary") or {}
    return float(primary["usedPercent"]) if "usedPercent" in primary else None


def find_codex(given: str) -> str:
    codex = given or shutil.which("codex")
    if not codex:
        raise SystemExit("codex が見つかりません。--codex で場所を指定してください")
    return codex


def run_batch(folder: Path, codex: str, effort: str, model: str = "", preface: str = "") -> int:
    """残りの団体を targets.tsv に書いて Codex に頼む。preface は依頼書の前に足す調べ直しの指示。"""
    write_tsv(folder / "targets.tsv", remaining(folder))
    (folder / "brief.md").write_text(preface + BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    command = [
        codex, "--search", "exec", "--skip-git-repo-check",
        "-c", "sandbox_workspace_write.network_access=true", "-c", f"model_reasoning_effort={effort}", "--approve-for-me",
        *(["-m", model] if model else []),
        "-C", str(folder), "-o", str(folder / "final.txt"), PROMPT,
    ]
    with open(folder / "codex.log", "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.flush()
        try:
            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=BATCH_TIMEOUT, check=False).returncode
        except subprocess.TimeoutExpired:
            return -1


def cmd_run(args: argparse.Namespace) -> None:
    codex = find_codex(args.codex)
    pending = [folder for folder in batch_dirs(Path(args.work)) if remaining(folder)]
    if args.limit:
        pending = pending[: args.limit]
    preface = Path(args.preface).read_text(encoding="utf-8") if args.preface else ""
    stopped: list[str] = []
    failures = 0

    def work_on(folder: Path) -> None:
        nonlocal failures
        if stopped:
            return
        # 週の枠を使い切るとクレジット（有料）から引かれるので、その手前で止める
        used = quota_percent(account_limits(codex))
        if used is None or used >= args.max_quota:
            stopped.append(f"週の枠 {used}%")
            return
        started = time.time()
        code = run_batch(folder, codex, args.effort, args.model, preface)
        print(f"{folder.name} 終了 {code}、{(time.time() - started) / 60:.0f} 分、残り {len(remaining(folder))}", flush=True)
        # 1 回の失敗では止めない。続けて失敗するなら回数の上限などで、流しても無駄になる
        failures = failures + 1 if code != 0 else 0
        if failures >= args.max_failures:
            stopped.append(f"{folder.name} まで {failures} 回続けて失敗")

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        list(pool.map(work_on, pending))
    if stopped:
        raise SystemExit(f"止めた理由: {stopped}")


def tokens_used(folder: Path) -> int:
    path = folder / "codex.log"
    if not path.exists():
        return 0
    found = re.findall(r"tokens used\s+([\d,]+)", path.read_text(encoding="utf-8", errors="replace"))
    return sum(int(value.replace(",", "")) for value in found)


def cmd_status(args: argparse.Namespace) -> None:
    folders = batch_dirs(Path(args.work))
    total = done = tokens = 0
    verdicts: dict[str, int] = {}
    for folder in folders:
        results, bad = read_results(folder)
        total += len(read_tsv(folder / "all_targets.tsv"))
        done += len(results)
        tokens += tokens_used(folder)
        for row in results.values():
            verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        for line in bad:
            print(line)
    print(f"束 {len(folders)}、団体 {done}/{total}、トークン {tokens:,}、判定 {verdicts}")


def ascii_url(url: str) -> str:
    """日本語を含む URL を要求に使える形にする。符号化済みの部分はそのまま。"""
    return urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~-._")


def fetch_status(url: str) -> str:
    """URL が開けるかを見る。本文は読まない。収集と同じ requests で開く（urllib は愛知県のサイトで 302 と 403 になる）。"""
    try:
        with requests.get(ascii_url(url), headers={"User-Agent": USER_AGENT}, timeout=30, stream=True, verify=False) as response:
            return str(response.status_code)
    except requests.RequestException as error:  # 自治体のサイトは様々に失敗する。結果に残して人が見る
        return type(error).__name__


def urls_by_host(work: Path) -> dict[str, list[tuple[str, str, str]]]:
    """結果にある入口、年度の頁、見本の URL をホストごとにまとめる。"""
    hosts: dict[str, list[tuple[str, str, str]]] = {}
    for folder in batch_dirs(work):
        for code, row in read_results(folder)[0].items():
            urls = [("entry", row["entry_url"])]
            urls += [("year", page.get("url", "")) for page in row["year_pages"]]
            urls += [("sample", sample.get("url", "")) for sample in row["samples"]]
            for role, url in urls:
                if url:
                    hosts.setdefault(urllib.parse.urlsplit(url).netloc, []).append((code, role, url))
    return hosts


def check_host(urls: list[tuple[str, str, str]], sleep: float) -> list[list[str]]:
    """1 つのサイトの URL を間を空けて順に開く。"""
    rows = []
    for code, role, url in urls:
        rows.append([code, role, fetch_status(url), url])
        time.sleep(sleep)
    return rows


def cmd_verify(args: argparse.Namespace) -> None:
    """結果にある入口とシートの URL が本当に開けるかを確かめる。サイトの間だけ並列にする。"""
    report = Path(args.work) / "url_check.tsv"
    hosts = urls_by_host(Path(args.work))
    with open(report, "w", encoding="utf-8", newline="") as stream, ThreadPoolExecutor(max_workers=args.parallel) as pool:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["jis_code", "role", "status", "url"])
        failed = checked = 0
        for rows in pool.map(lambda urls: check_host(urls, args.sleep), hosts.values()):
            writer.writerows(rows)
            stream.flush()
            checked += len(rows)
            failed += sum(row[2] != "200" for row in rows)
    print(f"{checked} 件のうち 200 でないもの {failed} 件: {report}")


def cmd_merge(args: argparse.Namespace) -> None:
    rows = [row for folder in batch_dirs(Path(args.work)) for row in read_results(folder)[0].values()]
    rows.sort(key=lambda row: row["jis_code"])
    with open(args.out, "w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} 団体を {args.out} へ")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "run", "status", "verify", "merge"])
    parser.add_argument("--work", required=True, help="作業の置き場")
    parser.add_argument("--batch", type=int, default=8, help="1 束の団体の数")
    parser.add_argument("--codes", default="", help="plan: この団体だけを束にする（JIS コードを並べたファイル）")
    parser.add_argument("--previous", default="", help="plan: 前回の merge の結果。束に previous.jsonl として添える")
    parser.add_argument("--preface", default="", help="run: 依頼書の前に足す調べ直しの指示のファイル")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--codex", default="", help="codex の実行ファイル")
    parser.add_argument("--effort", default="medium", help="Codex の reasoning effort")
    parser.add_argument("--model", default="", help="Codex のモデル。空なら config.toml の既定")
    parser.add_argument("--max-quota", type=float, default=90.0, help="週の枠がこの % に達したら新しい束を始めない")
    parser.add_argument("--max-failures", type=int, default=3, help="束がこの回数続けて失敗したら新しい束を始めない")
    parser.add_argument("--limit", type=int, default=0, help="今回流す束の数")
    parser.add_argument("--sleep", type=float, default=1.0, help="verify の間隔（秒）")
    parser.add_argument("--out", default="survey.jsonl")
    args = parser.parse_args()
    {"plan": cmd_plan, "run": cmd_run, "status": cmd_status, "verify": cmd_verify, "merge": cmd_merge}[args.command](args)


if __name__ == "__main__":
    main()
