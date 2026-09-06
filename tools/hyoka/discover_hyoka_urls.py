#!/usr/bin/env python3
"""事務事業評価を公開している自治体を探す。

事務事業評価は、自治体が個々の事業について目的・成果・費用・今後の方向を
書いた評価表である。会議録や例規と違って**共通のベンダ系システムが無く**、
各自治体が自前のページに PDF や Excel で載せている。だから URL の形からは
判別できず、ページの言葉で見分けるしかない。

公開の形は下見でおおむね次のとおりだった。

    ホームページ → 市政/行政情報 → 行政評価 → 事務事業評価 → 評価表(PDF/Excel)

「行政評価」が入口で、その下に「事務事業評価」が入る（静岡市・京都市・
川越市）。札幌市のように入口が「評価結果」だけのこともある。一方
「行政改革」は改革大綱の話で評価ではない（那覇市）。**行政改革だけを
根拠に公開ありとしない。**

確信度:

- `high`: リンク文字か題名に「事務事業評価」がある
- `medium`: 行政評価・施策評価・評価結果のページで、本文に「事務事業評価」がある
- `low`: 評価らしいページは見つかったが、事務事業評価とは言い切れない
- `none`: 見つからない

    python tools/hyoka/discover_hyoka_urls.py --limit 20
    python tools/hyoka/discover_hyoka_urls.py --codes 22100 26100 --verbose
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data" / "municipalities"
USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be/)"

# 事務事業評価そのものを指す言葉。これがあれば確定に近い。
STRONG_WORDS = (
    "事務事業評価", "事務事業の評価", "事務事業点検", "事務事業総点検",
    "事業評価シート", "事務事業マネジメント",
)
# 評価の入口によく使われる言葉。ここから 1 段降りると事務事業評価がある。
# 「事業評価」は横浜市のように事務事業評価をこの名前で出す自治体がある
# 一方、公共事業評価（建設事業の再評価）も同じ言葉なので、負の語で分ける。
HUB_WORDS = (
    "行政評価", "施策評価", "政策評価", "評価結果", "外部評価",
    "事業仕分", "事業評価", "行政経営", "事業点検",
)
# 市政のハブ。評価の入口はたいていこの下にある。
SECTION_WORDS = (
    "市政", "町政", "村政", "区政", "県政", "行政情報", "行財政", "財政",
    "計画", "施策", "行政改革", "まちづくり", "情報公開",
    # 大きな自治体は 2 段目が細かい。ここを通らないと評価の入口へ届かない。
    "行政運営", "行政経営", "監査", "行革", "総務", "企画", "政策",
    "予算", "組織", "の取組", "取り組み",
)
# 評価ではないもの。同じ言葉を含むので、先に落とす。
# 公共事業評価は建設事業の再評価で、事務事業評価とは別の制度。
NEGATIVE_WORDS = (
    "入札", "指名", "契約", "落札", "総合評価落札", "成績評定",
    "人事評価", "勤務評定", "介護", "要介護", "認定",
    "環境影響評価", "アセスメント", "不動産", "固定資産", "評価額", "路線価",
    "耐震", "健康", "検診", "学校評価", "授業評価", "教員評価", "第三者評価",
    "指定管理者", "外部監査", "包括外部", "公共事業評価", "公共事業の評価",
    "再評価", "事後評価",
)
# 「組織から探す」で部署ごとに分ける site が多い。評価の入口が
# 部署ページの下にしか無いことがある（金沢市は デジタル行政戦略課 の下）。
# 部署を全部辿ると際限が無いので、評価を所管しそうな名前だけに絞る。
ORG_INDEX_WORDS = ("組織", "部署", "所属", "課から", "組織から")
DEPT_WORDS = (
    "企画", "政策", "行政", "経営", "総務", "財政", "行革", "改革",
    "戦略", "評価", "秘書", "計画", "財務",
    # 部署ページの中はさらに「業務案内」で仕切られていることが多い。
    "業務案内", "事業案内", "しごと", "業務内容", "所管",
)
# 本文が事務事業評価らしいことの、二番目の手掛かり。評価表そのものを指す。
SHEET_WORDS = ("評価シート", "評価調書", "評価表", "評価書", "評価結果一覧", "点検票")
# 添付として置かれる評価表。あれば「実際に公開している」証拠になる。
ATTACHMENT_RE = re.compile(r"\.(pdf|xlsx?|docx?|csv)(?:$|\?)", re.I)
ASSET_RE = re.compile(r"\.(jpg|jpeg|png|gif|svg|css|js|zip|ico|mp4|mp3)(?:$|\?)", re.I)


@dataclass
class Finding:
    jis_code: str
    name: str = ""
    pref: str = ""
    homepage: str = ""
    url: str = ""
    title: str = ""
    confidence: str = "none"
    evidence: str = ""
    attachments: int = 0
    pages_fetched: int = 0
    note: str = ""
    candidates: list[str] = field(default_factory=list)


def load_master() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with io.open(DATA_ROOT / "municipality_master.tsv", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = str(row.get("jis_code", "")).strip()
            if code:
                rows[code] = row
    return rows


def load_homepages() -> dict[str, str]:
    pages: dict[str, str] = {}
    with io.open(DATA_ROOT / "municipality_homepages.csv", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("jis_code", "")).strip()
            url = str(row.get("url", "")).strip()
            if code and url and code not in pages:
                pages[code] = url
    return pages


def clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


def page_title(html: str) -> str:
    found = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return clean_label(found.group(1))[:120] if found else ""


def visible_text(html: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def looks_negative(text: str) -> bool:
    return any(word in text for word in NEGATIVE_WORDS)


def link_priority(label: str, url: str) -> int:
    """辿る価値。大きいほど先に見る。0 以下は辿らない。"""
    haystack = f"{label} {url}"
    if any(word in label for word in STRONG_WORDS):
        return 100
    if re.search(r"jimujigyo|jigyouhyouka|jigyohyoka|jimu_jigyou", url, re.I):
        return 90
    if any(word in label for word in HUB_WORDS):
        # 「人事評価」「公共事業評価」などを先に落とす。同じ言葉を含むが
        # 制度が違うので、辿ってもページ数を使うだけになる。
        return 0 if looks_negative(label) else 60
    if re.search(r"gyousei-?hyouka|gyoseihyoka|hyouka|hyoka|evaluation", url, re.I):
        return 40
    # 部署ページ。評価を所管しそうな名前だけを辿る。全部辿ると際限が無い。
    if re.search(r"soshiki|busho|section|organization", url, re.I):
        if any(word in label for word in DEPT_WORDS) and not looks_negative(label):
            return 15
        if any(word in label for word in ORG_INDEX_WORDS):
            return 12
        return 0
    if any(word in label for word in SECTION_WORDS):
        return 10
    if any(word in label for word in ORG_INDEX_WORDS):
        return 12
    return 0


def fetch(session: requests.Session, url: str, timeout: float) -> tuple[str, str] | None:
    try:
        response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
    except Exception:
        return None
    content_type = str(response.headers.get("Content-Type", "")).lower()
    if content_type and "html" not in content_type:
        return None
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.url, response.text


def iter_links(base_url: str, html: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for href, label in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = urljoin(base_url, href).split("#", 1)[0]
        if ASSET_RE.search(urlsplit(target).path):
            continue
        found.append((target, clean_label(label)))
    return found


def count_attachments(base_url: str, html: str) -> int:
    seen = set()
    for href, _label in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        target = urljoin(base_url, href)
        if ATTACHMENT_RE.search(urlsplit(target).path):
            seen.add(target)
    return len(seen)


def score_page(url: str, html: str, label: str) -> tuple[str, str]:
    """このページが事務事業評価かを判定する。返すのは (確信度, 根拠)。"""
    title = page_title(html)
    text = visible_text(html)
    head = text[:4000]
    if any(word in label for word in STRONG_WORDS):
        return "high", f"リンク文字『{label[:24]}』"
    if any(word in title for word in STRONG_WORDS):
        return "high", f"題名『{title[:24]}』"
    if any(word in head for word in STRONG_WORDS):
        # 入口ページが下位を紹介しているだけのこともあるが、
        # 少なくとも事務事業評価を公開していることの根拠にはなる。
        return "medium", "本文に『事務事業評価』"
    hub_hit = next((word for word in HUB_WORDS if word in title or word in label), "")
    if hub_hit and not looks_negative(title + label):
        # 「事業評価」だけでは公共事業評価と区別が付かない。評価表を指す
        # 言葉が本文にあれば、事務事業の評価を出していると見てよい。
        sheet_hit = next((word for word in SHEET_WORDS if word in head), "")
        if sheet_hit:
            return "medium", f"『{hub_hit}』と『{sheet_hit}』"
        return "low", f"評価の入口『{(title or label)[:24]}』"
    return "none", ""


def discover_one(
    session: requests.Session,
    code: str,
    name: str,
    pref: str,
    homepage: str,
    *,
    max_pages: int,
    max_depth: int,
    timeout: float,
    page_delay: float,
    budget_seconds: float = 180.0,
) -> Finding:
    result = Finding(jis_code=code, name=name, pref=pref, homepage=homepage)
    if not homepage:
        result.note = "公式ホームページ URL が無い"
        return result
    deadline = time.monotonic() + max(30.0, budget_seconds)

    host = urlsplit(homepage).netloc.lower()
    queue: list[tuple[str, int, str]] = [(homepage, 0, "")]
    visited: set[str] = set()
    best_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}

    while queue and result.pages_fetched < max_pages:
        if time.monotonic() > deadline:
            # 応答の遅い site に張り付くと、全体の足を引っ張る。
            # 見つかった分はそのまま返し、残りは次の機会に回す。
            result.note = (result.note + " 時間切れで打ち切り").strip()
            break
        queue.sort(key=lambda row: -link_priority(row[2], row[0]))
        url, depth, label = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        fetched = fetch(session, url, timeout)
        result.pages_fetched += 1
        if page_delay:
            time.sleep(page_delay)
        if fetched is None:
            continue
        final_url, html = fetched

        confidence, evidence = score_page(final_url, html, label)
        if best_rank[confidence] > best_rank[result.confidence]:
            result.confidence = confidence
            result.url = final_url
            result.title = page_title(html)
            result.evidence = evidence
            result.attachments = count_attachments(final_url, html)
            if confidence == "high":
                # これ以上探しても確信度は上がらない。相手の負担を増やさない。
                break

        if depth >= max_depth:
            continue
        for target, target_label in iter_links(final_url, html):
            if urlsplit(target).netloc.lower() != host or target in visited:
                continue
            priority = link_priority(target_label, target)
            if priority <= 0:
                continue
            # ハブや部署は 3 段目まで。ホーム → 組織から探す → 企画課 →
            # 業務案内 → 行政評価 という並びの site がある（金沢市）。
            # それより深くは評価の語があるリンクだけを辿る。
            if priority <= 15 and depth >= 3:
                continue
            queue.append((target, depth + 1, target_label))
            if target_label and priority >= 60 and len(result.candidates) < 6:
                result.candidates.append(f"{target_label[:24]}|{target}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="事務事業評価を公開している自治体を探す。")
    parser.add_argument("--codes", nargs="*", default=None, help="対象 jis_code（省略時は全件）")
    parser.add_argument("--limit", type=int, default=0, help="対象件数の上限（0 は無制限）")
    parser.add_argument("--offset", type=int, default=0, help="先頭から飛ばす件数")
    parser.add_argument("--max-pages", type=int, default=32, help="1 自治体あたりの最大ページ数")
    parser.add_argument("--max-depth", type=int, default=4, help="ホームページからの最大深さ")
    parser.add_argument("--timeout", type=float, default=15.0, help="1 リクエストのタイムアウト秒")
    parser.add_argument("--page-delay", type=float, default=0.6, help="ページ取得の間隔秒")
    parser.add_argument("--budget-seconds", type=float, default=180.0,
                        help="1 自治体に使う時間の上限")
    parser.add_argument("--workers", type=int, default=6, help="同時に見る自治体数（別ホストなので分散する）")
    parser.add_argument("--save-out", default="", help="結果 CSV の出力先")
    parser.add_argument("--from-csv", default="",
                        help="前回の結果 CSV。ここから対象を選び直す")
    parser.add_argument("--only", default="",
                        help="--from-csv と併用。拾う confidence のカンマ区切り（例 none,low）")
    parser.add_argument("--verbose", action="store_true", help="候補リンクも表示する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    master = load_master()
    homepages = load_homepages()

    if args.from_csv:
        # 見つからなかった自治体だけを、条件を広げて追いかけ直すための入口。
        # 全国をもう一度回すのは相手にも時間にも無駄が大きい。
        wanted = {value.strip() for value in str(args.only or "none").split(",") if value.strip()}
        with io.open(args.from_csv, encoding="utf-8-sig", newline="") as handle:
            codes = [
                str(row.get("jis_code", "")).strip()
                for row in csv.DictReader(handle)
                if str(row.get("confidence", "")).strip() in wanted
            ]
        codes = [code for code in codes if code]
        print(f"前回の結果から {len(codes)} 自治体を選び直しました（{'/'.join(sorted(wanted))}）", flush=True)
    elif args.codes:
        codes = list(args.codes)
    else:
        codes = sorted(master)
    codes = codes[args.offset:]
    if args.limit > 0:
        codes = codes[: args.limit]

    out_path = Path(args.save_out) if args.save_out else (
        WORKSPACE_ROOT / "work" / "hyoka" / f"discovery_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"対象 {len(codes)} 自治体 / 出力 {out_path}", flush=True)

    def probe(code: str) -> Finding:
        row = master.get(code, {})
        session = requests.Session()
        try:
            return discover_one(
                session,
                code,
                str(row.get("name", "")).strip(),
                str(row.get("pref_name", "")).strip(),
                homepages.get(code, ""),
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                timeout=args.timeout,
                page_delay=args.page_delay,
                budget_seconds=args.budget_seconds,
            )
        except Exception as error:
            return Finding(jis_code=code, name=str(row.get("name", "")).strip(),
                           pref=str(row.get("pref_name", "")).strip(),
                           homepage=homepages.get(code, ""), note=f"error: {error}")
        finally:
            session.close()

    results: list[Finding] = []
    counts = {"high": 0, "medium": 0, "low": 0, "none": 0}
    marks = {"high": "◎", "medium": "○", "low": "△", "none": "×"}
    # 全国 1,794 自治体で数時間かかる。途中で落ちても結果が残るよう、
    # 1 件ずつ書き出す。最後にまとめて書くと、落ちた分が全部消える。
    with io.open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jis_code", "pref_name", "name", "confidence", "url", "title",
                         "attachments", "evidence", "homepage", "pages_fetched", "note"])
        handle.flush()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(probe, code): code for code in codes}
            for index, future in enumerate(as_completed(futures), 1):
                found = future.result()
                results.append(found)
                counts[found.confidence] += 1
                writer.writerow([found.jis_code, found.pref, found.name, found.confidence,
                                 found.url, found.title, found.attachments, found.evidence,
                                 found.homepage, found.pages_fetched, found.note])
                handle.flush()
                print(
                    f"[{index}/{len(codes)}] {marks[found.confidence]} {found.jis_code} "
                    f"{found.pref}{found.name} 添付{found.attachments:3d} "
                    f"{found.url or found.note}",
                    flush=True,
                )
                if args.verbose and found.candidates:
                    for candidate in found.candidates:
                        print(f"        候補 {candidate}", flush=True)

    print(
        f"\n完了: {len(results)}件  "
        f"確実={counts['high']} 有力={counts['medium']} 弱={counts['low']} 不明={counts['none']}",
        flush=True,
    )
    print(f"結果: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
