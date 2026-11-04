#!/usr/bin/env python3
"""候補の一覧を読んで、集める文書を選ぶ。

`collect_from_survey.py --list-out` の一覧には、頁にあるファイルが全部並ぶ。年度の頁が決算や審議会の頁だと
決算書や議事録も混ざる。以前はリンクの言葉で選んで取り違えたので、ここでは Codex に調査の記録と一覧を
読ませて役割を決める。Web は使わない。

役割: `sheet`（事業ごとのシート、事業ごとの行がある一覧や冊子）、`report`（年度のまとめ、概要）、
`external`（外部評価、委員会の意見）、`other`（集めない）。

    python tools/hyoka/select_documents.py plan --lists lists --survey survey_final.jsonl --labels labels.jsonl --work select
    python tools/hyoka/select_documents.py run --work select --model gpt-6.1-sol --effort low --parallel 8 --max-quota 80
    python tools/hyoka/select_documents.py merge --work select --lists lists --out selected.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent / "survey"))

import collect_from_survey as collect  # noqa: E402
import run_survey  # noqa: E402

ROLES = ("sheet", "report", "external", "other")
BATCH_LINES = 1500
BATCH_TIMEOUT = 3600
CANDIDATE_COLUMNS = ["jis_code", "n", "year", "origin", "type", "page_title", "heading", "label", "file_name"]
PROMPT = "作業フォルダの brief.md を読み、その指示どおりに最後まで実行してください。"

BRIEF = """# 集める文書を選ぶ

自治体の事務事業評価（個々の事業について費用、実績、評価、今後の方向を点検した記録）を集めている。目的は、税金の無駄遣いを市民が検証できるようにすることである。

担当が公式サイトの実物を開いて、どの頁に何があるかを記録した。その頁にあるファイルのリンクを機械で全部書き出したのが `candidates.tsv` である。年度の頁が決算や審議会の頁のこともあり、関係のない文書も並ぶ。どれを集めるかを選ぶ。**Web は使わない。記録だけで判断する。**

## ファイル

- `targets.jsonl`: 団体ごとの調査の記録。`source_kind`（根拠の種類）、`forms`（様式と年度）、`samples`（担当が開いて確かめた文書）、`year_pages`（年度の頁と、担当が数えた文書の数 `documents`）。
- `candidates.tsv`: タブ区切り。列は jis_code, n（団体の中の通し番号）, year, origin（listed は調査の頁、followed は機械が降りた先）, type（pdf などの形式。html はファイルではなく頁そのもの）, page_title, heading, label（リンクの文字）, file_name。

## 役割

- `sheet`: 事業ごとのシート、事業ごとの行がある一覧や冊子。`forms` と `samples` が示す本体がこれ。根拠が成果報告書や総合計画の評価書なら、その報告書が `sheet` である。部局ごとの分冊も `sheet`。
- `report`: 年度の評価結果のまとめ、概要、集計で、個々の事業は読めないもの。
- `external`: 外部評価、行政評価委員会の意見や答申、事業仕分けの結果。
- `other`: それ以外。決算書、予算書、財務書類、議事録、次第、名簿、要綱、様式、記入例、総合計画の本文、案内、広報、教育委員会の点検評価、森林環境税など分野を限った評価。

## 選び方

- まず団体の `samples` と `forms` を読み、本体がどんな文書かをつかむ。同じ種類の文書を他の年度でも探す。
- `samples` の URL と同じ文書は必ず `sheet` か `external` にする。
- 言葉だけで決めない。同じ頁の並び、見出し、題名を見て判断する。頁の全部が同じ役割とは限らない。
- `type` が html の行は、頁の本文に事業ごとの評価があるなら `sheet`。一覧、目次、案内だけの頁は `other`。
- 迷ったら落とさず `sheet` にする。あとで中身を読むので、本物を落とす方が害が大きい。
- 件数は `documents` の目安と比べる。大きく違うなら選び方を見直す。

## 結果

`roles.jsonl` に 1 行 1 団体の JSON で書く。全団体の全番号をどれか 1 つの役割に入れる。番号は `1-40,45` の形で書く。

{"jis_code": "14130", "sheet": "1-40,45", "report": "41-44", "external": "", "other": "46-60"}

他のファイルを作らない。途中で質問しない。最後に、全団体の全番号が 1 回ずつ入っていることを確かめる。
"""


def file_name(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def compact_record(row: dict, label: dict) -> dict:
    """選ぶのに要る調査の記録だけを渡す。"""
    return {
        "jis_code": row["jis_code"],
        "name": row["name"],
        "verdict": row["verdict"],
        "source_kind": label["source_kind"],
        "reason": label.get("reason", ""),
        "forms": [{key: form.get(key) for key in ("years", "type", "granularity", "coverage")} for form in row["forms"]],
        "samples": [{key: sample.get(key) for key in ("fiscal_year", "business_name", "what_i_saw", "url")} for sample in row["samples"][:6]],
        "year_pages": [{key: page.get(key) for key in ("fiscal_year", "documents", "note")} for page in row["year_pages"]],
    }


def pack(sizes: list[tuple[str, int]], limit: int) -> list[list[str]]:
    """団体を、1 束の行数が limit を超えないように順に詰める。大きな団体は 1 束に 1 団体。"""
    batches: list[list[str]] = []
    current: list[str] = []
    total = 0
    for code, size in sizes:
        if current and total + size > limit:
            batches.append(current)
            current, total = [], 0
        current.append(code)
        total += size
    if current:
        batches.append(current)
    return batches


def parse_numbers(text: str) -> set[int]:
    """`1-40,45` を番号の集まりにする。形が違えば ValueError。"""
    numbers: set[int] = set()
    for part in filter(None, re.split(r"[,\s]+", str(text or "").strip())):
        found = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not found:
            raise ValueError(part)
        first = int(found.group(1))
        numbers.update(range(first, int(found.group(2) or first) + 1))
    return numbers


def row_roles(row: dict, count: int) -> dict[int, str] | None:
    """1 団体の結果を番号→役割にする。全番号が 1 回ずつでなければ None。"""
    roles: dict[int, str] = {}
    try:
        for role in ROLES:
            for number in parse_numbers(row.get(role, "")):
                if number in roles:
                    return None
                roles[number] = role
    except ValueError:
        return None
    return roles if set(roles) == set(range(1, count + 1)) else None


def batch_counts(folder: Path) -> dict[str, int]:
    with open(folder / "candidates.tsv", encoding="utf-8", newline="") as stream:
        return dict(Counter(row["jis_code"] for row in csv.DictReader(stream, delimiter="\t")))


def batch_roles(folder: Path) -> dict[str, dict[int, str]]:
    """束の結果のうち、形の合う団体だけを返す。"""
    counts = batch_counts(folder)
    path = folder / "roles.jsonl"
    done: dict[str, dict[int, str]] = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = row.get("jis_code")
        if code in counts:
            roles = row_roles(row, counts[code])
            if roles is not None:
                done[code] = roles
    return done


def folders(work: Path) -> list[Path]:
    return sorted(path for path in (work / "batches").glob("[0-9]*") if path.is_dir())


def cmd_plan(args: argparse.Namespace) -> None:
    rows = collect.read_jsonl(Path(args.survey))
    labels = collect.read_jsonl(Path(args.labels))
    lists = Path(args.lists)
    candidates = {}
    for target in collect.load_survey(Path(args.survey), Path(args.labels)):
        path = lists / f"{target.slug}.jsonl"
        if path.exists():
            found = collect.read_candidates(path)
            if found:
                candidates[target.code] = found
    sizes = sorted((len(found), code) for code, found in candidates.items())
    batches = pack([(code, size) for size, code in sizes], args.lines)
    for number, codes in enumerate(batches, 1):
        folder = Path(args.work) / "batches" / f"{number:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "brief.md").write_text(BRIEF, encoding="utf-8")
        with open(folder / "targets.jsonl", "w", encoding="utf-8", newline="\n") as stream:
            for code in codes:
                stream.write(json.dumps(compact_record(rows[code], labels[code]), ensure_ascii=False) + "\n")
        with open(folder / "candidates.tsv", "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(CANDIDATE_COLUMNS)
            for code in codes:
                for n, item in enumerate(candidates[code], 1):
                    writer.writerow([code, n, item.path_year or "", item.origin, item.file_type, item.page_title[:60], item.heading[:60], item.label[:80], file_name(item.url)[:80]])
    print(f"{len(candidates)} 団体、候補 {sum(len(found) for found in candidates.values())} 件を {len(batches)} 束に")


def run_batch(folder: Path, codex: str, effort: str, model: str) -> int:
    command = [
        codex, "exec", "--skip-git-repo-check", "-c", f"model_reasoning_effort={effort}", "--approve-for-me",
        "-m", model, "-C", str(folder), "-o", str(folder / "final.txt"), PROMPT,
    ]
    with open(folder / "codex.log", "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.flush()
        try:
            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=BATCH_TIMEOUT, check=False).returncode
        except subprocess.TimeoutExpired:
            return -1


def cmd_run(args: argparse.Namespace) -> None:
    codex = run_survey.find_codex(args.codex)
    pending = [folder for folder in folders(Path(args.work)) if len(batch_roles(folder)) < len(batch_counts(folder))]
    if args.limit:
        pending = pending[: args.limit]
    stopped: list[str] = []

    def work_on(folder: Path) -> None:
        if stopped:
            return
        # 週の枠を使い切るとクレジット（有料）から引かれるので、その手前で止める
        used = run_survey.quota_percent(run_survey.account_limits(codex))
        if used is None or used >= args.max_quota:
            stopped.append(f"週の枠 {used}%")
            return
        code = run_batch(folder, codex, args.effort, args.model)
        print(f"{folder.name} 終了 {code}、読めた {len(batch_roles(folder))}/{len(batch_counts(folder))}", flush=True)

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        list(pool.map(work_on, pending))
    if stopped:
        raise SystemExit(f"止めた理由: {stopped}")


def cmd_merge(args: argparse.Namespace) -> None:
    targets = {target.code: target for target in collect.load_survey(Path(args.survey), Path(args.labels))}
    totals: Counter[str] = Counter()
    missing = 0
    with open(args.out, "w", encoding="utf-8", newline="\n") as stream:
        for folder in folders(Path(args.work)):
            done = batch_roles(folder)
            missing += len(batch_counts(folder)) - len(done)
            for code, roles in sorted(done.items()):
                found = collect.read_candidates(Path(args.lists) / f"{targets[code].slug}.jsonl")
                by_url = {found[number - 1].url: role for number, role in sorted(roles.items())}
                # 担当が開いて確かめた文書は落とさない
                for sample in targets[code].samples:
                    if by_url.get(sample.get("url")) in ("report", "other"):
                        by_url[sample["url"]] = "sheet"
                totals.update(by_url.values())
                stream.write(json.dumps({"jis_code": code, "roles": by_url}, ensure_ascii=False) + "\n")
    print(f"役割 {dict(totals)}、残りの団体 {missing}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "run", "merge"])
    parser.add_argument("--work", required=True, help="作業の置き場")
    parser.add_argument("--lists", default="", help="候補の一覧の置き場")
    parser.add_argument("--survey", default="", help="調査の台帳")
    parser.add_argument("--labels", default="", help="根拠の種類の分類")
    parser.add_argument("--lines", type=int, default=BATCH_LINES, help="1 束の候補の行数の上限")
    parser.add_argument("--codex", default="", help="codex の実行ファイル")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--parallel", type=int, default=8)
    parser.add_argument("--max-quota", type=float, default=80.0, help="週の枠がこの % に達したら新しい束を始めない")
    parser.add_argument("--limit", type=int, default=0, help="今回流す束の数")
    parser.add_argument("--out", default="selected.jsonl")
    args = parser.parse_args()
    {"plan": cmd_plan, "run": cmd_run, "merge": cmd_merge}[args.command](args)


if __name__ == "__main__":
    main()
