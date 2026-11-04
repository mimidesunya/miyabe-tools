#!/usr/bin/env python3
"""調査の台帳から事務事業評価の文書を集める。

`scrape_hyoka_docs.py` の入口からの収集は、リンクの言葉で文書を選ぶので取り違えが多かった。
ここでは担当が実物を開いて確かめた年度の頁（`tools/hyoka/survey`）を入口にし、3 段で集める。

1. **候補の一覧**（`--list-out`）: 頁だけを開いてファイルのリンクを全部書き出す。文書は取らない。
   - 年度の頁、入口、見本の頁のファイル（`origin: listed`）。年度の頁が URL 直のファイルならそのまま
   - 他の年度と部局の頁へ `--max-depth` 段まで降りた先のファイル（`origin: followed`）
   - 評価を HTML で公開する団体は、年度の頁の下を辿って頁そのものも候補にする（`file_type: html`）
     評価の表がある頁と見本の頁は、どの団体でも候補にする
2. **選ぶ**（`select_documents.py`）: 一覧と調査の記録をモデルが読んで文書の役割を決める。
   年度の頁が決算や審議会の頁だと、決算書や議事録も並ぶので、言葉では選ばない。
3. **取る**（`--selected`）: 選ばれた文書だけを取る。保存、改訂の扱い、取得元への配慮は `scrape_one` と同じ。

根拠の種類（`source_kind`）と点検の別（`project_evaluation`）は manifest の `survey` に残す。
以前の収集に同じ URL の保存物があれば `--reuse-from` で使い回し、取得元への要求を減らす。

    python tools/hyoka/collect_from_survey.py --survey survey_final.jsonl --labels labels.jsonl --all --list-out lists --workers 16
    python tools/hyoka/select_documents.py plan --lists lists --survey survey_final.jsonl --labels labels.jsonl --work select
    python tools/hyoka/collect_from_survey.py --survey survey_final.jsonl --labels labels.jsonl --all --list-out lists \\
        --selected selected.jsonl --out-dir work/hyoka2 --reuse-from work/hyoka --workers 8
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))

import scrape_hyoka_docs as scrape  # noqa: E402
from document_crawl import (  # noqa: E402
    DEFAULT_DELAY_SECONDS,
    DEFAULT_REFRESH_DAYS,
    Fetcher,
    HostBlocked,
    HostGuard,
    Link,
    Target,
    fiscal_year_source,
    load_manifest,
    load_master,
    page_links,
    page_title,
    same_organization,
    slug_for,
    write_json,
)

DEFAULT_LEVELS = ("yes", "partial")
# 札幌市は局ごとに頁があり、15 年度で 400 頁近い
DEFAULT_MAX_PAGES = 600
DEFAULT_MAX_DEPTH = 2
# 評価を HTML で公開する団体を辿るときの上限。三郷市は 1 年 600 事業ほどのシートが 4 年分ある
HTML_WALK_PAGES = 6000
HTML_WALK_DEPTH = 8
DEFAULT_MAX_DOCS = 20000
# select_documents.py の役割のうち、取る文書
TAKEN_ROLES = ("sheet", "report", "external")
REUSED_FIELDS = (
    "file", "file_type", "sha256", "bytes", "content_type", "fetched_at", "checked_at", "etag", "last_modified",
    "text_chars", "text_status", "text_file", "pages", "sheets", "archive_members",
)


@dataclass
class SurveyTarget(Target):
    year_pages: list[dict] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)
    survey: dict = field(default_factory=dict)
    html_sheets: bool = False

    @property
    def expected_documents(self) -> int:
        """調査の担当が年度の頁で数えた文書の数。"""
        return sum(page["documents"] for page in self.year_pages if isinstance(page.get("documents"), (int, float)))


def read_jsonl(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    return {row["jis_code"]: row for row in rows}


def load_survey(survey: Path, labels: Path, levels: tuple[str, ...] = DEFAULT_LEVELS) -> list[SurveyTarget]:
    """調査の台帳から、集める団体を作る。分類の無い団体（事業の情報なし）は集めない。"""
    rows = read_jsonl(survey)
    master = load_master()
    targets = []
    for code, label in sorted(read_jsonl(labels).items()):
        row = rows[code]
        if label["project_evaluation"] not in levels:
            continue
        master_row = master.get(code, {})
        targets.append(SurveyTarget(
            code=code,
            slug=slug_for(code, master_row),
            name=str(master_row.get("full_name") or row["name"]),
            url=row["entry_url"],
            crawl_status="survey",
            year_pages=row["year_pages"],
            samples=row["samples"],
            html_sheets=any("HTML" in str(form.get("file_format", "")).upper() for form in row["forms"]),
            survey={
                "verdict": row["verdict"],
                "source_kind": label["source_kind"],
                "project_evaluation": label["project_evaluation"],
                "source_run": row.get("source_run", ""),
                "collection_notes": row.get("collection_notes", ""),
            },
        ))
    return targets


def survey_year(page: dict) -> int:
    return fiscal_year_source(str(page.get("fiscal_year", "")))[0] or 0


def walk_prefix(url: str) -> str:
    """HTML のシートを辿る範囲。年度の頁の 2 段上のディレクトリ。

    三郷市は一覧が `/section/disclosure/r6/2024/npm/0/T1.html`、シートが `.../2024/sheet/proj/` にある。
    階層の浅いサイトで辿ると全体へ広がるので、4 段に満たなければ空にして辿らない。
    """
    directories = urlsplit(url).path.split("/")[1:-1]
    return "/" + "/".join(directories[:-2]) + "/" if len(directories) >= 4 else ""


def collect_from_survey(fetcher: Fetcher, target: SurveyTarget, *, max_pages: int, max_depth: int,
                        result: scrape.RunResult) -> list[scrape.Candidate]:
    """調査で確かめた頁から文書を集める。頁にあるファイルは言葉で選ばない。

    評価を HTML で公開する団体は、辿った頁そのものも候補にする。評価の表がある頁は団体を問わず候補にする。
    """
    candidates: dict[str, scrape.Candidate] = {}
    if target.html_sheets:
        max_pages = max(max_pages, HTML_WALK_PAGES)
    sample_urls = {sample.get("url", "") for sample in target.samples}

    def add(url: str, label: str, page_url: str, heading: str, title: str, file_type: str, year: int, origin: str) -> None:
        if url in candidates:
            return
        candidates[url] = scrape.Candidate(
            url=url, label=label, page_url=page_url, heading=heading, page_title=title, file_type=file_type,
            kind=scrape.document_kind(label, heading, title), path_year=year, order=len(candidates) + 1, origin=origin,
        )

    queue: deque[tuple[str, int, int, str, str]] = deque()
    pages = [(page.get("url", ""), survey_year(page), 0) for page in target.year_pages]
    pages.append((target.url, 0, 0))
    # 見本の頁は担当が開いた実物の頁。そこのファイルは取るが、その先へは降りない
    pages += [(sample.get("url", ""), survey_year(sample), max_depth) for sample in target.samples]
    for url, year, depth in pages:
        file_type = scrape.link_file_type(url, "") if url else None
        if file_type:
            add(url, "", target.url, "", "", file_type, year, "listed")
        elif url:
            queue.append((url, year, depth, "listed", walk_prefix(url) if target.html_sheets and depth == 0 else ""))

    visited: set[str] = set()
    while queue and result.pages < max_pages:
        url, year, depth, origin, prefix = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        result.pages += 1
        try:
            fetched = fetcher.html(url)
        except HostBlocked:
            raise
        except requests.RequestException as exc:
            result.errors.append(f"page {url}: {type(exc).__name__}")
            continue
        if fetched is None:
            result.errors.append(f"頁を開けません: {url}")
            continue
        result.entry_opened = True
        final_url, html = fetched
        visited.add(final_url)
        title = scrape.unescape_label(page_title(html))
        result.page_log.append((depth, final_url, title))
        if scrape.has_evaluation_table(html):
            result.html_tables += 1
        page_year = fiscal_year_source(title)[0] or year
        host = (urlsplit(final_url).hostname or "").lower()
        if target.html_sheets or url in sample_urls or scrape.has_evaluation_table(html):
            add(final_url, title, final_url, "", title, "html", page_year, origin)
        for raw_link in page_links(final_url, html):
            link = Link(url=raw_link.url, label=scrape.unescape_label(raw_link.label), heading=scrape.unescape_label(raw_link.heading))
            link_host = (urlsplit(link.url).hostname or "").lower()
            file_type = scrape.link_file_type(link.url, link.label)
            if file_type:
                if scrape.file_host_allowed(host, link_host):
                    add(link.url, link.label, final_url, link.heading, title, file_type, page_year, origin)
                else:
                    result.offsite_files += 1
                continue
            if not same_organization(host, link_host) or link.url in visited:
                continue
            link_year = fiscal_year_source(link.label)[0] or page_year
            if prefix and depth < HTML_WALK_DEPTH and urlsplit(link.url).path.startswith(prefix):
                queue.append((link.url, link_year, depth + 1, "followed", prefix))
                continue
            if depth >= max_depth:
                continue
            # 部局のリンクは評価の頁の上でだけ辿る。決算の頁や課の頁から降りると別の文書が混ざる
            evaluation_page = scrape.has_strong(title) or scrape.names_evaluation(title)
            if scrape.descend_priority(link, evaluation_page, evaluation_page) > 0:
                queue.append((link.url, link_year, depth + 1, "followed", ""))
    return list(candidates.values())


def write_candidates(path: Path, candidates: list[scrape.Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        for candidate in candidates:
            stream.write(json.dumps(asdict(candidate), ensure_ascii=False) + "\n")


def read_candidates(path: Path) -> list[scrape.Candidate]:
    with open(path, encoding="utf-8") as stream:
        return [scrape.Candidate(**json.loads(line)) for line in stream if line.strip()]


def selected_collector(list_dir: Path, selected: dict[str, dict]) -> Callable[..., list[scrape.Candidate]]:
    """候補の一覧のうち、選ばれた文書だけを返す。頁は開き直さない。"""

    def collect(_fetcher: Fetcher, target: SurveyTarget, *, max_pages: int, max_depth: int,
                result: scrape.RunResult) -> list[scrape.Candidate]:
        result.entry_opened = True
        roles = selected.get(target.code, {}).get("roles", {})
        found = []
        for candidate in read_candidates(list_dir / f"{target.slug}.jsonl"):
            role = roles.get(candidate.url, "")
            if role in TAKEN_ROLES:
                candidate.origin = f"{candidate.origin}:{role}"
                found.append(candidate)
        return found

    return collect


class PreviousCollection:
    """以前の収集の保存物を使い回す。同じ URL なら取得元へ取りに行かない。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.documents: dict[str, dict] = {}

    def previous(self, slug: str) -> dict:
        if slug not in self.documents:
            self.documents[slug] = dict(load_manifest(self.root / slug).get("documents") or {})
        return self.documents[slug]

    def __call__(self, doc_id: str, entry: dict, work_dir: Path) -> None:
        old = self.previous(work_dir.name).get(doc_id)
        if entry.get("file") or not old or not old.get("file"):
            return
        source_dir = self.root / work_dir.name
        if not (source_dir / str(old["file"])).is_file():
            return
        for name in filter(None, (old.get("file"), old.get("text_file"))):
            destination = work_dir / str(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_dir / str(name), destination)
        entry.update({name: old[name] for name in REUSED_FIELDS if name in old})
        entry["reused_from"] = str(self.root)


def shard_groups(groups: list[list[Target]], shard: str) -> list[list[Target]]:
    """ホストの組を i/n に分ける。本文の抜き出しは Python の 1 本の流れに詰まるので、プロセスを分けて流すため。

    同じホストは同じ組にしか入らないので、別のプロセスが同じサイトへ同時に行くことはない。
    """
    index, count = (int(part) for part in shard.split("/"))
    ordered = sorted(groups, key=lambda group: group[0].code)
    return ordered[index::count]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="調査の台帳から事務事業評価の文書を集める。")
    parser.add_argument("--survey", required=True, help="調査の台帳（survey_final.jsonl）")
    parser.add_argument("--labels", required=True, help="根拠の種類の分類（labels.jsonl）")
    parser.add_argument("--levels", default=",".join(DEFAULT_LEVELS), help="集める点検の別")
    parser.add_argument("--code", action="append", default=[], help="自治体コード（複数可）")
    parser.add_argument("--all", action="store_true", help="分類の対象をすべて")
    parser.add_argument("--limit", type=int, default=0, help="対象の自治体数の上限")
    parser.add_argument("--list-out", default="", help="候補の一覧の置き場。--selected が無ければ一覧を作るだけで文書は取らない")
    parser.add_argument("--selected", default="", help="select_documents.py の結果。選ばれた文書だけを取る")
    parser.add_argument("--out-dir", default="", help="保存先。--list-out だけのときは要らない")
    parser.add_argument("--reuse-from", default="", help="以前の収集の置き場（同じ URL の保存物を使い回す）")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="1 件ごとの間隔（秒、1 以上）")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="1 自治体で開くページの上限")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="年度の頁から降りる階層")
    parser.add_argument("--max-docs", type=int, default=DEFAULT_MAX_DOCS, help="1 自治体で 1 回に取るファイルの上限")
    parser.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS, help="取得済みを問い合わせ直すまでの日数")
    parser.add_argument("--workers", type=int, default=1, help="同時に扱う自治体の数（同じホストは並ばない）")
    parser.add_argument("--shard", default="", help="ホストの組を i/n に分けたうちの i 番目だけを扱う（0 始まり）")
    parser.add_argument("--ca-bundle", default="",
                        help="証明書の束。中間証明書を送らないサイト（多可町、直方市）のために、発行元から補ったものを足して渡す")
    parser.add_argument("--dry-run", action="store_true", help="候補を表示するだけで取らない")
    parser.add_argument("--verbose", action="store_true", help="--dry-run で候補を全部表示する")
    parser.add_argument("--summary-json", default="", help="実行結果の要約を書き出す先")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    levels = tuple(level.strip() for level in args.levels.split(",") if level.strip())
    targets = load_survey(Path(args.survey), Path(args.labels), levels)
    if args.code:
        wanted = {code.strip() for code in args.code}
        targets = [target for target in targets if target.code in wanted]
    elif not args.all:
        targets = []
    if args.limit > 0:
        targets = targets[: args.limit]
    if not targets:
        print("対象がありません。--code か --all を指定してください。", file=sys.stderr)
        return 2
    list_only = bool(args.list_out) and not args.selected
    if args.selected and not args.list_out:
        print("--selected には --list-out が要ります。", file=sys.stderr)
        return 2
    if not args.dry_run and not list_only and not args.out_dir:
        print("--out-dir を指定してください。", file=sys.stderr)
        return 2
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    work_root = Path(args.list_out if list_only else args.out_dir) if (list_only or args.out_dir) else scrape.DEFAULT_WORK_ROOT
    work_root.mkdir(parents=True, exist_ok=True)
    delay = max(1.0, float(args.delay))
    reuse = PreviousCollection(Path(args.reuse_from)) if args.reuse_from else None
    collector = selected_collector(Path(args.list_out), read_jsonl(Path(args.selected))) if args.selected else collect_from_survey
    print_lock = threading.Lock()
    summary: list[dict] = []
    counter = {"done": 0}

    def list_one(target: SurveyTarget, fetcher: Fetcher) -> scrape.RunResult:
        """候補の一覧を書く。済んだ団体は開き直さない。"""
        path = Path(args.list_out) / f"{target.slug}.jsonl"
        result = scrape.RunResult(slug=target.slug)
        if path.exists():
            result.found = len(read_candidates(path))
            return result
        try:
            found = collect_from_survey(fetcher, target, max_pages=args.max_pages, max_depth=args.max_depth, result=result)
        except HostBlocked as exc:
            result.status = "host_blocked"
            result.errors.append(f"ホストを休止中: {exc}")
            return result
        result.found = len(found)
        write_candidates(path, found)
        return result

    def run_group(group: list[Target]) -> None:
        session = requests.Session()
        if args.ca_bundle:
            session.verify = args.ca_bundle
        fetcher = Fetcher(session=session, guard=HostGuard(work_root), delay=delay)
        for target in group:
            try:
                result = list_one(target, fetcher) if list_only else scrape.scrape_one(
                    target, fetcher, work_root, max_pages=args.max_pages, max_depth=args.max_depth, max_docs=args.max_docs,
                    refresh_days=args.refresh_days, dry_run=args.dry_run, verbose=args.verbose,
                    collector=collector, reuse=reuse, manifest_extra={"survey": target.survey},
                )
            except Exception as exc:  # 1 自治体の失敗で全体を止めない
                result = scrape.RunResult(slug=target.slug, status="error", errors=[f"{type(exc).__name__}: {exc}"])
            with print_lock:
                counter["done"] += 1
                print(f"[{counter['done']}/{len(targets)}] {target.slug} {target.name} 調査の文書数 {target.expected_documents}", flush=True)
                for line in result.lines:
                    print(line, flush=True)
                print(scrape.result_line(result), flush=True)
                for error in result.errors[:5]:
                    print(f"     {error}", flush=True)
                summary.append({
                    "code": target.code, "slug": target.slug, "name": target.name, "expected_documents": target.expected_documents,
                    **target.survey, **scrape.run_summary(result),
                })

    groups = scrape.host_groups(targets)
    if args.shard:
        groups = shard_groups(groups, args.shard)
        targets = [target for group in groups for target in group]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(run_group, groups))

    if args.summary_json:
        summary.sort(key=lambda row: row["code"])
        write_json(Path(args.summary_json), {"targets": summary})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
