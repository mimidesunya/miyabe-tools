#!/usr/bin/env python3
"""包括外部監査の報告書を公開している自治体を探す。

包括外部監査は、監査委員とは別の外部の監査人（公認会計士・弁護士など）が
毎年テーマを選んで行う監査で、結果は「包括外部監査結果報告書」として公開
される。**都道府県・政令市・中核市の 129 団体には地方自治法 252 条の 36 で
義務付けられている**ので、この 129 には必ずある。ほかの市町村は条例で導入
できる（任意）。

会議録や例規と違って共通のベンダ系システムは無い。事務事業評価と同じく、
各自治体が自前のページに PDF を置くので、ページの言葉で見分けるしかない。

公開の形はおおむね次のとおり。

    ホームページ → 市政/県政情報 → 監査委員（監査事務局）→ 外部監査
    → 包括外部監査結果報告書（年度ごとの PDF）

確信度:

- `high`: リンク文字か題名に「包括外部監査」がある
- `medium`: 外部監査・監査結果のページの本文に「包括外部監査」がある
- `low`: 監査のページは見つかったが、包括外部監査とは言い切れない
- `none`: 見つからない

**個別外部監査は別の制度**（住民の請求などを受けて個別の事項を監査する）。
包括と取り違えないよう、個別しか見つからない場合は `low` に留めて印を残す。

    python tools/kansa/discover_kansa_urls.py --mandatory --limit 10
    python tools/kansa/discover_kansa_urls.py --codes 13000 14100 --verbose
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
sys.path.append(str(Path(__file__).resolve().parent))

from core_cities import mandatory_codes, mandatory_kind  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be/)"

# 包括外部監査そのものを指す言葉。これがあれば確定に近い。
STRONG_WORDS = (
    "包括外部監査", "包括的外部監査", "包括外部監査人", "包括外部監査結果",
)
# 外部監査の入口によく使われる言葉。ここから 1 段降りると包括外部監査がある。
HUB_WORDS = (
    "外部監査", "監査結果", "監査委員", "監査事務局", "監査公表", "監査報告",
    # 札幌市は監査の入口のリンク文字が「監査」だけ。ここを辿らないと
    # その先の包括外部監査へ届かない。定期監査などは負の語で落とす。
    "監査",
)
# 県政・市政のハブ。監査の入口はたいていこの下にある。
SECTION_WORDS = (
    "市政", "町政", "村政", "区政", "県政", "府政", "道政", "行政情報",
    "行財政", "財政", "情報公開", "組織", "各課", "委員会", "行政委員会",
    "審議会", "公表", "計画", "予算", "決算", "総務",
)
# **包括外部監査ではない**もの。同じ「監査」を含むので、先に落とす。
# これらだけを根拠に「公開あり」と数えない。
NEGATIVE_WORDS = (
    # 「定期監査」と書く自治体がある（札幌市）。「定例監査」だけでは落とせない。
    "定例監査", "定期監査", "財務監査", "例月出納検査", "例月現金出納検査",
    "決算審査", "決算意見",
    "住民監査請求", "財政健全化", "健全化判断比率", "内部統制", "行政監査",
    "工事監査", "財政援助団体等監査", "随時監査", "指定管理者監査",
    "監査基準", "監査委員の選任", "公益通報", "入札", "契約監視",
    # 福祉施設などへの「指導監査」は所管課の仕事で、包括外部監査ではない。
    # 前橋市・岐阜市・愛知県・広島市はここを掴んで探索の予算を使い切っていた。
    "指導監査", "実地指導", "監査指導", "指導・監査",
)
# 個別外部監査は制度が別。見つけたら印だけ残す。
INDIVIDUAL_WORDS = ("個別外部監査",)
# 報告書そのものを指す言葉。あれば「実際に公開している」証拠になる。
REPORT_WORDS = (
    "結果報告書", "報告書", "監査の結果", "監査結果報告", "意見", "措置状況",
)
# 添付として置かれる報告書。
ATTACHMENT_RE = re.compile(r"\.(pdf|xlsx?|docx?)(?:$|\?)", re.I)
ASSET_RE = re.compile(r"\.(jpg|jpeg|png|gif|svg|css|js|zip|ico|mp4|mp3)(?:$|\?)", re.I)
# URL に出る綴り。日本語が読めない site でもここで拾える。
URL_STRONG_RE = re.compile(r"houkatsu|hokatsu|包括", re.I)
URL_HUB_RE = re.compile(r"gaibu[-_]?kansa|gaibukansa|kansa|kansa|audit|外部監査|監査", re.I)


@dataclass
class Finding:
    jis_code: str
    name: str = ""
    pref: str = ""
    kind: str = ""
    homepage: str = ""
    url: str = ""
    title: str = ""
    confidence: str = "none"
    evidence: str = ""
    attachments: int = 0
    individual_only: bool = False
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
    """監査ではあるが包括外部監査ではない、と分かる言葉を含むか。

    「包括外部監査」を含むときは落とさない。監査委員のページが定例監査と
    包括外部監査を並べて載せていることがあり、そこで落とすと入口ごと失う。
    """
    if any(word in text for word in STRONG_WORDS):
        return False
    return any(word in text for word in NEGATIVE_WORDS)


def link_priority(label: str, url: str) -> int:
    """辿る価値。大きいほど先に見る。0 以下は辿らない。"""
    if any(word in label for word in STRONG_WORDS):
        return 100
    if URL_STRONG_RE.search(url) and URL_HUB_RE.search(url):
        return 90
    if any(word in label for word in HUB_WORDS):
        return 0 if looks_negative(label) else 60
    if any(word in label for word in INDIVIDUAL_WORDS):
        # 個別外部監査のページから包括へのリンクが出ていることがある。
        return 40
    if re.search(r"gaibu[-_]?kansa|gaibukansa|kansa[-_]?iin|audit", url, re.I):
        return 40
    # 監査の区画そのもの（/kansa/）。リンク文字が「監査」だけの site がある。
    if re.search(r"/kansa(?:[-_/]|$)", urlsplit(url).path, re.I):
        return 30
    if any(word in label for word in SECTION_WORDS) and not looks_negative(label):
        return 10
    # 市政・県政の区画。トップの案内が暮らしの入口だけで、市政のリンクが
    # 本文に無い site がある（盛岡市はメニューが JavaScript）。URL の形で拾う。
    if re.search(r"/(shisei|sisei|kensei|kenseijoho|fusei|dosei|gyosei|soshiki|shiseijoho)(?:[-_/]|$)",
                 urlsplit(url).path, re.I):
        return 8
    return 0


# トップから辿れないときに直接試す区画。監査の入口はたいていこの下にある。
# さいたま市・高松市・久留米市・佐世保市はトップの案内が JavaScript で、
# 本文のリンクからは監査へ降りられなかった。
FALLBACK_PATHS = (
    "/kansa/", "/shisei/kansa/", "/kensei/kansa/", "/soshiki/kansa/",
    "/gaibukansa/", "/shisei/", "/kensei/",
)


def same_organization(host: str, other: str) -> bool:
    """同じ自治体のホストか。監査事務局が別ホストに居ることがある。

    東京都は `www.metro.tokyo.lg.jp` に対して監査事務局が
    `kansa.metro.tokyo.lg.jp`、神奈川県・愛知県も同じ形。`www.` を外した
    ドメインを共有していれば同じ組織と見なす。外部のまったく別のサイトへは
    出ない（`.lg.jp` の別自治体はドメインが違うので混ざらない）。
    """
    left = host.lower().removeprefix("www.")
    right = other.lower().removeprefix("www.")
    if left == right:
        return True
    return left.endswith("." + right) or right.endswith("." + left)


def homepage_variants(homepage: str) -> list[str]:
    """登録のホームページが開けないときに試す綴り。

    台帳の URL が古くなっている自治体がある（鳥取市・下関市は 404）。
    www の有無と http/https を入れ替えた形を試す。
    """
    parts = urlsplit(homepage)
    host = parts.netloc
    hosts = [host]
    if host.startswith("www."):
        hosts.append(host[4:])
    else:
        hosts.append("www." + host)
    found: list[str] = []
    for candidate_host in hosts:
        for scheme in ("https", "http"):
            url = f"{scheme}://{candidate_host}/"
            if url not in found and url != homepage:
                found.append(url)
    return found


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


def score_page(url: str, html: str, label: str) -> tuple[str, str, bool]:
    """このページが包括外部監査かを判定する。

    返すのは (確信度, 根拠, 個別外部監査しか無いか)。
    """
    title = page_title(html)
    text = visible_text(html)
    head = text[:6000]
    if any(word in label for word in STRONG_WORDS):
        return "high", f"リンク文字『{label[:24]}』", False
    if any(word in title for word in STRONG_WORDS):
        return "high", f"題名『{title[:24]}』", False
    if any(word in head for word in STRONG_WORDS):
        # 入口が下位を紹介しているだけのこともあるが、包括外部監査を
        # 公開していることの根拠にはなる。
        return "medium", "本文に『包括外部監査』", False
    individual = any(word in head or word in title or word in label for word in INDIVIDUAL_WORDS)
    hub_hit = next((word for word in HUB_WORDS if word in title or word in label), "")
    if hub_hit and not looks_negative(title + label):
        if individual:
            # 個別外部監査は別制度。包括があるとは限らない。
            return "low", f"『{hub_hit}』だが個別外部監査のみ", True
        report_hit = next((word for word in REPORT_WORDS if word in head), "")
        if report_hit:
            return "low", f"『{hub_hit}』と『{report_hit}』", False
        return "low", f"監査の入口『{(title or label)[:24]}』", False
    if individual:
        return "low", "個別外部監査のみ", True
    return "none", "", False


def discover_one(
    session: requests.Session,
    code: str,
    name: str,
    pref: str,
    kind: str,
    homepage: str,
    *,
    max_pages: int,
    max_depth: int,
    timeout: float,
    page_delay: float,
    budget_seconds: float = 180.0,
) -> Finding:
    result = Finding(jis_code=code, name=name, pref=pref, kind=kind, homepage=homepage)
    if not homepage:
        result.note = "公式ホームページ URL が無い"
        return result
    deadline = time.monotonic() + max(30.0, budget_seconds)

    host = urlsplit(homepage).netloc.lower()
    entry = homepage
    probe = fetch(session, homepage, timeout)
    result.pages_fetched += 1
    if probe is None:
        # 台帳の URL が古い。綴りを変えて入口を探し直す。
        for candidate in homepage_variants(homepage):
            if time.monotonic() > deadline:
                break
            retried = fetch(session, candidate, timeout)
            result.pages_fetched += 1
            if retried is not None:
                entry = retried[0]
                host = urlsplit(entry).netloc.lower()
                result.note = (result.note + f" 入口を {candidate} へ読み替え").strip()
                break
        else:
            result.note = (result.note + " ホームページを開けない").strip()
            return result

    queue: list[tuple[str, int, str]] = [(entry, 0, "")]
    visited: set[str] = set()
    best_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}

    while queue and result.pages_fetched < max_pages:
        if time.monotonic() > deadline:
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

        confidence, evidence, individual = score_page(final_url, html, label)
        if best_rank[confidence] > best_rank[result.confidence]:
            result.confidence = confidence
            result.url = final_url
            result.title = page_title(html)
            result.evidence = evidence
            result.attachments = count_attachments(final_url, html)
            result.individual_only = individual
            if confidence == "high":
                # これ以上探しても確信度は上がらない。相手の負担を増やさない。
                break

        if depth >= max_depth:
            continue
        for target, target_label in iter_links(final_url, html):
            target_host = urlsplit(target).netloc.lower()
            if target in visited:
                continue
            priority = link_priority(target_label, target)
            if priority <= 0:
                continue
            if target_host != host:
                # 別ホストは、同じ自治体の中で監査らしいものだけ辿る。
                if not same_organization(host, target_host) or priority < 40:
                    continue
            # 市政・県政のハブは 3 段目まで。それより深くは監査の語がある
            # リンクだけを辿る。監査委員の下に年度別の頁が並ぶ site がある。
            if priority <= 10 and depth >= 3:
                continue
            queue.append((target, depth + 1, target_label))
            if target_label and priority >= 60 and len(result.candidates) < 6:
                result.candidates.append(f"{target_label[:24]}|{target}")

        if not queue and result.confidence in ("none", "low"):
            # 本文のリンクから監査へ降りられなかった。区画を直接叩く。
            origin = f"{urlsplit(entry).scheme}://{urlsplit(entry).netloc}"
            for path in FALLBACK_PATHS:
                candidate = origin + path
                if candidate not in visited:
                    queue.append((candidate, 1, ""))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="包括外部監査を公開している自治体を探す。")
    parser.add_argument("--codes", nargs="*", default=None, help="対象 jis_code（省略時は義務付けの 129 団体）")
    parser.add_argument("--mandatory", action="store_true",
                        help="義務付けの都道府県・政令市・中核市だけを見る（既定）")
    parser.add_argument("--all", action="store_true", help="全自治体を見る（任意導入を探す）")
    parser.add_argument("--limit", type=int, default=0, help="対象件数の上限（0 は無制限）")
    parser.add_argument("--offset", type=int, default=0, help="先頭から飛ばす件数")
    parser.add_argument("--max-pages", type=int, default=28, help="1 自治体あたりの最大ページ数")
    parser.add_argument("--max-depth", type=int, default=4, help="ホームページからの最大深さ")
    parser.add_argument("--timeout", type=float, default=15.0, help="1 リクエストのタイムアウト秒")
    parser.add_argument("--page-delay", type=float, default=1.0, help="ページ取得の間隔秒")
    parser.add_argument("--budget-seconds", type=float, default=180.0, help="1 自治体に使う時間の上限")
    parser.add_argument("--workers", type=int, default=6, help="同時に見る自治体数（別ホストなので分散する）")
    parser.add_argument("--save-out", default="", help="結果 CSV の出力先")
    parser.add_argument("--from-csv", default="", help="前回の結果 CSV。ここから対象を選び直す")
    parser.add_argument("--only", default="", help="--from-csv と併用。拾う confidence のカンマ区切り")
    parser.add_argument("--verbose", action="store_true", help="候補リンクも表示する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    master = load_master()
    homepages = load_homepages()

    if args.from_csv:
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
    elif args.all:
        codes = sorted(master)
    else:
        codes = mandatory_codes(master)
    codes = codes[args.offset:]
    if args.limit > 0:
        codes = codes[: args.limit]

    out_path = Path(args.save_out) if args.save_out else (
        WORKSPACE_ROOT / "work" / "kansa" / f"discovery_{time.strftime('%Y%m%d_%H%M%S')}.csv"
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
                mandatory_kind(code, master),
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
                           kind=mandatory_kind(code, master),
                           homepage=homepages.get(code, ""), note=f"error: {error}")
        finally:
            session.close()

    results: list[Finding] = []
    counts = {"high": 0, "medium": 0, "low": 0, "none": 0}
    marks = {"high": "◎", "medium": "○", "low": "△", "none": "×"}
    with io.open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jis_code", "pref_name", "name", "kind", "confidence", "url", "title",
                         "attachments", "individual_only", "evidence", "homepage",
                         "pages_fetched", "note"])
        handle.flush()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(probe, code): code for code in codes}
            for index, future in enumerate(as_completed(futures), 1):
                found = future.result()
                results.append(found)
                counts[found.confidence] += 1
                writer.writerow([found.jis_code, found.pref, found.name, found.kind,
                                 found.confidence, found.url, found.title, found.attachments,
                                 "1" if found.individual_only else "", found.evidence,
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
