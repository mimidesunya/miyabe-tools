#!/usr/bin/env python3
"""事務事業評価の評価表・報告書を取得して保存する。

台帳 `data/municipalities/hyoka_system_urls.tsv` の入口ページ（と、その下の
年度別ページ・部局別ページ）から、事務事業評価の**評価表・調書・結果報告**の
ファイル（PDF・Excel・Word、ZIP はそのまま）を探して取り、`work/hyoka/<slug>/`
に置く。サイトでは AI による評価をせず、利用者が自分の AI で確かめられるよう、
原典をそろえて検索できるようにするための第一段（構造化・索引は後の段）。

    work/hyoka/<slug>/
        manifest.json               文書ごとの原典 URL・見つけたページ・年度・種別・SHA・取得日時
        source/<doc_id>.<拡張子>    取得したファイルそのもの
        text/<doc_id>.txt.gz        抜き出した本文（頭に題名・年度・出典の行）

**原典の URL と、それを見つけたページを必ず残す。** 検索に載せたとき、
利用者が元の評価表へ戻れるようにするため（会議録・例規と同じ方針）。

種別（`kind`）:

- `project_evaluation`: 事務事業評価の評価表・調書・結果報告（取る対象の本体）
- `policy_evaluation`: 施策評価・政策評価
- `external_review`: 外部評価・行政評価委員会などの意見・答申・会議録
- `overview`: 評価の概要・一覧表

取らないもの（manifest と実行結果に理由ごとの件数を残す）は `EXCLUSION_RULES`
に並べた。教育委員会の点検・評価（地方教育行政法 26 条）、公社・出資法人の
経営評価、交付金事業の評価（電源立地など）、総合戦略・地方創生の効果検証、
指定管理者の評価、人事評価、環境影響評価、入札・契約、公共事業評価、様式・
要綱、委員会の名簿・日程など。事業の名前（「空き家対策事業」「介護予防事業」）
は評価表の題名として普通に出るので、**分野の言葉ではなく制度の言葉で落とす**。

取得元への配慮（包括外部監査の取得と同じ部品 `tools/document_crawl.py`）:

- 1 件ごとに間隔を空ける（既定 1.5 秒）。`--workers` で並べるのは自治体（ホスト）
  をまたぐときだけで、同じホストは 1 本の流れで順に取る。
- 一度取った文書は、既定で 30 日は問い合わせ直さない。問い合わせるときも
  ETag / Last-Modified で条件付きにし、変わっていなければ保存し直さない。
- 同じホストで接続を続けて切られたら、そのホストを数時間休む。
- 1 回に取るファイルは 1 自治体 `--max-docs` 本まで（新しい年度から）。
  残りは manifest に `pending` で残り、次の回に取る。

取得元から消えた文書は「消えた」（`gone`）と記録し、保存物は消さない。

    python tools/hyoka/scrape_hyoka_docs.py --code 01100 --dry-run
    python tools/hyoka/scrape_hyoka_docs.py --all --limit 5 --out-dir /tmp/hyoka
    python tools/hyoka/scrape_hyoka_docs.py --all --include-review --workers 6
"""

from __future__ import annotations

import argparse
import gzip
import heapq
import html as html_entities
import logging
import re
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = WORKSPACE_ROOT / "data" / "municipalities" / "hyoka_system_urls.tsv"
DEFAULT_WORK_ROOT = WORKSPACE_ROOT / "work" / "hyoka"
sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))

from discover_hyoka_urls import HUB_WORDS, SHEET_WORDS, STRONG_WORDS, has_evaluation_table  # noqa: E402
from document_crawl import (  # noqa: E402
    DEFAULT_DELAY_SECONDS,
    DEFAULT_REFRESH_DAYS,
    DOCUMENT_EXTENSIONS,
    JST,
    MIN_TEXT_CHARS,
    Fetcher,
    HostBlocked,
    HostGuard,
    Link,
    Target,
    clean_label,
    decode_csv,
    doc_id_for,
    extract_docx_text,
    extract_pdf_text,
    extract_xlsx_text,
    fetch_document,
    fiscal_year_source,
    load_manifest,
    load_targets,
    mark_missing,
    note_failure,
    note_fetched,
    now_text,
    page_links,
    page_title,
    same_organization,
    sniff_document_type,
    text_document,
    write_bytes,
    write_json,
    zip_member_names,
)

MANIFEST_VERSION = 1

# 1 自治体で開くページの上限。札幌市は 入口 → 評価結果 → 年度 → 調書の一覧 →
# 局ごとのページ、と 4 段あり、局が 24 ある（15 年度で 400 頁近い）。辿る順は
# 毎回同じなので、上限で切った先の年度は次の回にも見えない。ページは 1 頁
# 1.5 秒で済むので、ファイルより先に上限を緩くしておく。評価の手掛かりの
# あるリンクしか辿らないので、小さな自治体は 10〜20 頁で終わる。
DEFAULT_MAX_PAGES = 200
DEFAULT_MAX_DEPTH = 4
# 1 回に取るファイルの上限（1 自治体）。事業ごとに 1 ファイルを置く自治体は
# 1 年で数百本になる。上限を超えた分は pending で残し、次の回に取る。
DEFAULT_MAX_DOCS = 400
# 1 ファイルの上限。評価結果を 1 冊にまとめた PDF は数十 MB になることがある。
MAX_DOCUMENT_BYTES = 120 * 1024 * 1024
# 取らなかったリンクの例を、理由ごとにいくつ残すか（取り違えの点検用）。
EXCLUDED_EXAMPLES = 5

KINDS = ("project_evaluation", "policy_evaluation", "external_review", "overview")

# ─────────────────────────────────────
# 添付ファイルのリンク
# ─────────────────────────────────────

FILE_URL_RE = re.compile(r"\.(pdf|xlsx|xlsm|xls|docx|doc|zip|csv)(?:$|[?#])", re.I)
ASSET_RE = re.compile(r"\.(jpe?g|png|gif|svg|css|js|ico|mp4|mp3|pptx?|txt|html?|php|aspx?)(?:$|[?#])", re.I)
DOWNLOAD_SCRIPT_RE = re.compile(r"/(?:dl|download|file|files|fileoutput|attach(?:ment)?)\.(?:php|aspx?|cgi|do|jsp)$", re.I)
DOWNLOAD_DIRECTORY_RE = re.compile(r"/(?:dl|download|file|files|fileoutput|attach(?:ment)?)/?$", re.I)
# リンク文字の末尾に付く「（PDF：1.2MB）」「(エクセル 30KB)」などの注記。
FILE_NOTE_RE = re.compile(
    r"[（(\[［【]\s*(?:PDF|ＰＤＦ|Excel|ＥＸＣＥＬ|エクセル|xlsx?|xlsm|Word|ワード|docx?|ZIP|CSV)[^）)\]］】]*[）)\]］】]"
    r"|(?:PDF|ＰＤＦ|Excel|エクセル|Word|ワード|ZIP)\s*(?:ファイル|形式)?\s*[：:／/]?\s*[\d.,]+\s*[KMG]?B"
    r"|[（(]\s*[\d.,]+\s*(?:キロバイト|メガバイト|KB|MB)\s*[）)]",
    re.I,
)
NOTE_TYPES = (
    ("pdf", re.compile(r"PDF|ＰＤＦ", re.I)),
    ("xlsx", re.compile(r"Excel|ＥＸＣＥＬ|エクセル|xlsx?|xlsm", re.I)),
    ("docx", re.compile(r"Word|ワード|docx?", re.I)),
    ("zip", re.compile(r"ZIP", re.I)),
    ("csv", re.compile(r"CSV", re.I)),
)


SIZE_NOTE_RE = re.compile(r"([\d.,]+)\s*(KB|MB|GB|キロバイト|メガバイト|ギガバイト)", re.I)
SIZE_UNITS = {
    "kb": 1024, "キロバイト": 1024, "mb": 1024 ** 2, "メガバイト": 1024 ** 2, "gb": 1024 ** 3, "ギガバイト": 1024 ** 3,
}


def size_hint(label: str) -> int:
    """リンク文字の「（PDF：616KB）」から見込みのバイト数。書いていなければ 0。

    取る前に全体の量を見積もるため（--dry-run の要約に足す）。
    """
    note = FILE_NOTE_RE.search(label)
    found = SIZE_NOTE_RE.search(note.group(0) if note else "")
    if not found:
        return 0
    try:
        number = float(found.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(number * SIZE_UNITS[found.group(2).lower()])


def unescape_label(text: str) -> str:
    """「1&nbsp; 安心の…」のような文字参照を戻し、空白を揃える。

    共通部品の `clean_label` は包括外部監査の取得と共有しているので、そちらの
    振る舞いは変えず、ここで戻す。
    """
    return re.sub(r"\s+", " ", html_entities.unescape(text or "")).strip()


def strip_file_note(label: str) -> str:
    return clean_label(FILE_NOTE_RE.sub("", label)).strip(" 　・-")


def link_file_type(url: str, label: str) -> str | None:
    """リンクが添付ファイルなら、見込みの形式（pdf・xlsx・docx など）。ページなら None。

    拡張子で見る。拡張子の無い配信口（東京都の `/documents/d/...` や
    `download.php?id=`）は、リンク文字の「（PDF 1MB）」の注記で見る。
    実体は取ったところで確かめる（`sniff_document_type`）。
    """
    parts = urlsplit(url)
    found = FILE_URL_RE.search(parts.path)
    if found:
        return found.group(1).lower()
    endpoint = bool(DOWNLOAD_SCRIPT_RE.search(parts.path)) or (
        bool(parts.query) and bool(DOWNLOAD_DIRECTORY_RE.search(parts.path))
    )
    if ASSET_RE.search(parts.path) and not endpoint:
        return None
    note = FILE_NOTE_RE.search(label)
    if not note and not endpoint:
        return None
    text = note.group(0) if note else label
    for kind, pattern in NOTE_TYPES:
        if pattern.search(text):
            return kind
    return None


# ファイルだけを外部の保管庫に置く CMS がある（大町市は `*.isk01.sakurastorage.jp`）。
# 自治体のページに張られたファイルに限り、これらのホストからも取る。ページは辿らない。
FILE_STORAGE_HOST_RE = re.compile(
    r"(?:^|\.)(?:sakurastorage\.jp|amazonaws\.com|cloudfront\.net|blob\.core\.windows\.net|storage\.googleapis\.com)$"
)


def file_host_allowed(entry_host: str, link_host: str) -> bool:
    return same_organization(entry_host, link_host) or bool(FILE_STORAGE_HOST_RE.search(link_host))


def check_document_response(response, raw: bytes) -> None:
    """取った応答が評価表のファイルでなければ ValueError（「見つかりません」の HTML を保存しない）。"""
    headers = response.headers
    if sniff_document_type(raw, getattr(response, "url", "") or "", headers.get("Content-Type", ""),
                           headers.get("Content-Disposition", "")) is None:
        raise ValueError(f"評価表のファイルではない応答: {headers.get('Content-Type', '')!r}")


# ─────────────────────────────────────
# 判定
# ─────────────────────────────────────

# 評価の手掛かり。リンク自体がこれを持てば、置かれたページを問わず候補にする。
EVALUATION_RE = re.compile(r"評価|点検|事務事業|仕分|マネジメントシート|事業シート")
# 評価表の URL によくある綴り（訓令式も。泉大津市・光市の zimuzigyou）。
STRONG_URL_RE = re.compile(r"jimu_?jigyo|zimu_?zigyo|jigyou?_?hyou?ka|zigyou?_?hyou?ka", re.I)
EVALUATION_URL_RE = re.compile(r"gyou?sei_?hyou?ka|hyou?ka|tenken", re.I)
RESULT_RE = re.compile(r"評価結果|評価の結果|結果報告|評価報告|結果の公表|評価を公表|評価の公表")
# 委員会の開催ページ。資料に意見が入ることはあるが、評価表より後に見る。
COMMITTEE_RE = re.compile(r"委員会|審議会|懇談会|懇話会|部会|会議")
# 調書の一覧から部局のページへ降りる手掛かり（札幌市「危機管理局」「教育委員会」）。
ORG_LABEL_RE = re.compile(r"(?:局|部|課|室|所|委員会|事務局|センター|区役所|支所|総局)$")

# 種別の手掛かり。
EXTERNAL_RE = re.compile(
    r"外部評価|外部委員|外部有識者|評価委員|審議会|懇談会|懇話会|市民評価|区民評価|町民評価|村民評価|"
    r"県民評価|府民評価|都民評価|道民評価|住民評価|市民委員|事業仕分|仕分け|公開評価|公開ヒアリング|"
    r"意見書|答申|提言|建議|会議録|議事録|議事要旨|議事概要|会議結果|会議の結果|委員会資料|委員会報告|"
    r"委員の意見|委員会の意見|有識者|具申"
)
# 「ごみ収集計画費」の「集計」に当たらないよう、集計は表・結果と組にする。
OVERVIEW_RE = re.compile(r"概要|一覧|総括|集計表|集計結果|ダイジェスト|要約|サマリー|まとめ")
POLICY_RE = re.compile(
    r"施策(?:事前|事後)?評価|政策(?:事前|事後)?評価|施策の評価|政策の評価|^施策\s*[0-9０-９]+|"
    r"施策マネジメント|政策マネジメント|施策シート|政策シート|基本事業評価|施策別|政策別|プログラム評価|"
    r"施策の検証|政策の検証"
)
JIMU_RE = re.compile(r"事務事業|事業評価|事業シート|事業調書|個別事業")

# 取らないもの。上から順に見る。
#
# 事業ごとに 1 ファイルを置く自治体（札幌市は 1 年で 600 本）では、リンク文字が
# 「広報誌等発行費」「橋りょう長寿命化修繕費」「（公社）シルバー人材センター運営
# 補助金」「鳥獣被害防止対策推進費」のような**事業の名前**になる。分野の言葉で
# 落とすとこれを取りこぼすので、規則は 2 段に分ける。
#
# - `scheme`: 別の制度・別の書類の名前そのもの（人事評価・公共事業の再評価・
#   様式・要綱など）。見つかれば落とす
# - `topic`: 別の制度の対象を指す言葉（指定管理・公社・総合戦略・長寿命化など）。
#   **直後（12 文字以内）に評価・検証・報告などが続くときだけ**落とす
#   （「指定管理者評価シート」「総合戦略の効果検証」は落とし、
#   「評価シート（橋りょう長寿命化修繕事業）」「地方創生推進費」は取る）
#
# 三つ目の値が True の規則は、「事務事業」と書いてあれば落とさない
# （「事務事業評価（地方創生関連事業）」「事務事業評価（教育委員会所管分）」）。
#
# 間に課・室・局・係が挟まるときは部署の名前なので落とさない（岐阜市「監査委員
# 事務局事業評価シート」、「地方創生推進課（事業評価シート）」）。
TOPIC_FOLLOWER = r"[^課室局係]{0,12}?(?:評価|点検|検証|報告|結果|状況|シート|調書)"


def _scheme(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _topic(pattern: str) -> re.Pattern:
    return re.compile(f"(?:{pattern}){TOPIC_FOLLOWER}")


EXCLUSION_RULES: tuple[tuple[str, re.Pattern, bool], ...] = (
    # 地方教育行政法 26 条の点検・評価。札幌市の調書一覧の「教育委員会」は
    # 部局の名前で、そこにあるのは事務事業評価なので、素の「教育委員会」では落とさない。
    ("education_board", _scheme(
        r"教育に関する事務|教育委員会(?:の|事務局の)?(?:事務の)?(?:点検|自己点検|活動状況)|教育行政(?:の)?点検|"
        r"点検(?:・|及び|および)評価.{0,20}教育|教育.{0,20}点検(?:・|及び|および)評価"), True),
    ("personnel", _scheme(
        r"人事評価|勤務評定|職員(?:の)?評価|給与・定員管理|定員管理計画|定員適正化|給与(?:等)?の状況|人事行政の運営"), False),
    ("environment_assessment", _scheme(r"環境影響評価|環境アセスメント"), False),
    # 「入札・契約事務」は事業の名前としてありうるので、入札は制度の言葉と組のときだけ。
    ("procurement", _scheme(
        r"総合評価落札|総合評価方式|総合評価一般競争|入札(?:結果|監視|公告|参加資格|情報)|契約監視|工事成績|"
        r"成績評定|業者評価"), False),
    # 公共事業の再評価・事前評価・事後評価。行政評価の「事後評価シート」は本物
    # なので（三条市・大月市）、事後評価だけでは落とさない。
    # 岡山県の台帳の入口「大規模施設建設事業評価」、福岡県の「公共事業新規事業評価」もここ。
    ("public_works", _scheme(
        r"(?:公共事業|建設事業).{0,8}評価|新規事業(?:採択)?評価|事業採択評価|"
        r"(?:再評価|事前評価|事後評価).{0,20}(?:公共事業|補助事業|工事|施設整備|大規模事業|地区|道路|河川|港湾|"
        r"林道|農道|都市再生|社会資本)|"
        r"(?:公共事業|補助事業|工事|施設整備|大規模事業|道路|河川|港湾|林道|農道|都市再生|社会資本).{0,20}"
        r"(?:再評価|事前評価|事後評価)"), False),
    ("public_works", _topic(r"都市再生整備|社会資本総合整備|まちづくり交付金"), False),
    ("grant_project", _scheme(r"交付金(?:事業)?(?:の)?(?:評価|効果検証|事業評価)"), False),
    ("grant_project", _topic(
        r"電源立地|電源地域|地域再生計画|鳥獣被害|防衛施設|周辺環境整備|高度無線|エネルギー構造|理解促進|過疎|"
        r"森林環境(?:譲与)?税"), False),
    # 試験研究機関の研究開発の評価（福岡県の行政評価の報告に並ぶ）。
    ("research_evaluation", _scheme(r"研究開発(?:事業)?(?:の)?評価|研究評価|試験研究(?:機関)?(?:の)?評価"), False),
    ("regional_strategy", _topic(r"総合戦略|地方創生|まち・ひと・しごと|まちひとしごと|デジタル田園|KPI"), True),
    ("public_corporation", _scheme(r"経営評価|出資団体評価|公社等評価"), False),
    ("public_corporation", _topic(r"公社|外郭団体|出資法人|出資団体|第三セクター|独立行政法人"), False),
    ("designated_manager", _topic(r"指定管理|モニタリング"), True),
    # 公営企業の局の「事業評価」（札幌市の水道局）。調書一覧の部局リンク「水道局」は落とさない。
    ("public_enterprise", _scheme(r"(?:水道|下水道|交通|病院|企業|ガス)局.{0,10}評価"), False),
    ("school_welfare", _scheme(r"学校評価|授業評価|教員評価|第三者評価|福祉サービス(?:第三者)?評価"), False),
    ("tax_assessment", _scheme(r"固定資産評価|評価額|路線価|評価替|土地評価|家屋評価"), False),
    ("audit", _topic(r"外部監査|内部統制|定期監査|行政監査"), False),
    ("accessibility", _topic(r"アクセシビリティ"), False),
    ("public_comment", _scheme(r"パブリックコメント|意見募集|意見公募|案の公表|意見を募集"), False),
    # 個別計画の点検・評価（川崎市の台帳の入口「子ども・若者の未来応援プラン」の点検・評価結果）。
    ("plan_progress", _scheme(
        r"進行管理|計画の進捗|計画の推進状況|進捗状況報告|"
        r"(?:計画|プラン|ビジョン|戦略)[」』]?(?:の)?(?:点検|進捗|推進状況|実施状況)"), True),
    ("plan_progress", _topic(r"歴史的風致|維持向上計画|個別施設計画|長寿命化"), True),
    # 決算の主要施策成果説明書は自治法 233 条 5 項の別の書類（含めるかは方針の決めどころ）。
    ("settlement_report", _scheme(r"主要施策の成果|主要な施策の成果|成果説明書|成果に関する説明書"), True),
    ("settlement_report", _topic(r"決算"), True),
    ("form", _scheme(r"様式|記入例|記入要領|書き方|記載例|テンプレート|意見用紙|応募用紙|申込書|申請書"), False),
    # 市民アンケートの結果。施策評価の指標に使われるが、評価表ではない（座間市）。
    ("survey", _scheme(r"アンケート|意識調査"), False),
    ("organization_chart", _scheme(r"組織機構図|組織図|機構図"), False),
    ("system_document", _scheme(r"要綱|要領|手引|マニュアル|ガイドライン|実施方針|基本方針|規程|規則|条例|指針|見方"), False),
    # 「選挙人名簿管理費」は事業の名前。委員の名簿・会議の案内だけを落とす。
    ("meeting_admin", _scheme(
        r"(?:委員|職員)(?:会)?名簿|開催案内|開催通知|開催のお知らせ|傍聴|会議日程|開催日程|スケジュール|会議次第|"
        r"^次第|座席表|委嘱"),
        False),
    ("publicity", _scheme(r"広報(?:誌|紙)?\s*[0-9０-９]+月号|議会だより|お知らせ版|チラシ|リーフレット"), False),
)
# 計画書そのもの（「第6次総合計画」「行政改革大綱」）。評価の語が無く、名前が
# 計画で終わるときだけ落とす。「文化芸術基本計画推進費」は事業の名前なので取る。
OTHER_PLAN_RE = re.compile(
    r"総合計画|基本計画|実施計画|中期計画|長期計画|行政改革大綱|行革大綱|行政改革(?:推進)?(?:計画|プラン)|"
    r"集中改革プラン|財政計画|中期財政|公共施設等総合管理計画|定員(?:管理|適正化)|人口ビジョン"
)
PLAN_TITLE_END_RE = re.compile(r"(?:計画|大綱|プラン|計画書|概要版|ビジョン)$")
# 予算書そのもの。江東区の行政評価の報告書の章「新たな取り組み等（平成27年当初予算）」は取る。
BUDGET_TITLE_END_RE = re.compile(r"(?:予算書|予算案|予算の概要|予算概要|当初予算|補正予算|予算説明書)$")
# 個別計画の点検でも、行政評価・施策評価として行っているなら取る。
EVALUATION_SYSTEM_RE = re.compile(r"行政評価|施策評価|政策評価")
# 様式・要綱の類でも、評価の結果そのものなら取る（「事業評価調書の見方」は要綱扱い）。
RESULT_DOCUMENT_RE = re.compile(r"結果|シート|調書|評価表|評価書")
SOFT_OVERRIDE_REASONS = {"system_document"}
# 見出し・ページの題名に出ても、その下のファイルを落とす理由にしないもの
# （「スケジュール」の見出しの下に評価結果が続く、など）。
# 三田市は「行政評価（内部評価）の結果に対する市民意見を募集しています」の頁に
# 評価調書を置いている。意見募集の頁・見出しの下の評価文書は取る。
CONTEXT_IGNORED_REASONS = {
    "form", "system_document", "meeting_admin", "publicity", "other_plan", "budget", "public_comment",
}


def has_strong(text: str) -> bool:
    return any(word in text for word in STRONG_WORDS)


def names_jimu_jigyo(text: str) -> bool:
    """「事務事業」と書いてあるか。別制度の言葉が混じっても取る根拠にする。

    「事業評価シート」は事務事業評価の強い語だが、普代村の「総合戦略事業評価
    シート」のように別制度にも付くので、除外を覆す根拠にはしない。
    """
    return "事務事業" in text


def names_evaluation(text: str) -> bool:
    """題名・見出しが評価のページ・区画を名乗るか。"""
    return has_strong(text) or any(word in text for word in HUB_WORDS + SHEET_WORDS) or bool(RESULT_RE.search(text))


def exclusion_reason(text: str, title: str = "") -> str:
    """取らない理由。取ってよければ ""。

    「事務事業」と書いてあるときは、総合戦略・決算・教育委員会などの
    言葉が混じっても取る（「事務事業評価（地方創生関連事業）」）。
    人事評価・公共事業評価・交付金事業の評価などは、書いてあっても取らない。
    `title` はリンク文字だけ（ファイル名を足す前）。計画書かどうかはこれで見る。
    """
    strong = names_jimu_jigyo(text)
    for reason, pattern, strong_overrides in EXCLUSION_RULES:
        if not pattern.search(text):
            continue
        if strong_overrides and strong:
            continue
        if reason == "plan_progress" and EVALUATION_SYSTEM_RE.search(text):
            continue
        if reason in SOFT_OVERRIDE_REASONS and RESULT_DOCUMENT_RE.search(text) and not re.search(r"見方|記入", text):
            continue
        return reason
    if EVALUATION_RE.search(text):
        return ""
    # 計画書・予算書そのもの。括弧書きと、末尾の年・版（「北区基本計画2024」
    # 「（令和6年度～8年度）」）を外した名前の終わりで見る。
    name = strip_file_note(title or text)
    for _ in range(2):
        name = re.sub(r"[（(][^（）()]*[）)]", "", name)
    name = re.sub(r"[\s0-9０-９年度版改訂～〜・\-－]+$", "", name).strip()
    if OTHER_PLAN_RE.search(text) and PLAN_TITLE_END_RE.search(name):
        return "other_plan"
    if BUDGET_TITLE_END_RE.search(name):
        return "budget"
    return ""


def url_words(url: str) -> str:
    """URL の最後のファイル名（%エンコードを戻したもの）。日本語のファイル名がある。"""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def classify_file_link(label: str, url: str, heading: str, *, page_title_text: str = "",
                       page_is_evaluation: bool = False, page_exclusion: str = "") -> tuple[str, str]:
    """添付リンクを仕分ける。戻り値は (判定, 理由か種別)。

    判定: `take`（取る。二つ目は種別）/ `excluded`（取らない。二つ目は理由）/
    `no_signal`（評価の手掛かりが無い）。

    リンクの文字とファイル名を先に見て、手掛かりが無ければ置かれた見出し、
    それも無ければページの文脈（入口か、評価のリンクから来たページか）で決める。
    津島市の「1 健康づくり(PDF:119KB)」のように、評価表が分野の名前だけで
    並ぶ形があるので、評価のページの上では素のリンク文字でも取る。
    """
    own = f"{strip_file_note(label)} {url_words(url)}"
    reason = exclusion_reason(own, strip_file_note(label))
    if reason:
        return "excluded", reason
    if not (names_jimu_jigyo(own) or STRONG_URL_RE.search(url)):
        # 見出し・ページが別制度の区画なら、リンクに「評価」とあっても取らない
        # （板橋区の指定管理の頁の「サービス水準評価結果」）。
        heading_reason = exclusion_reason(heading) if heading else ""
        if heading_reason and heading_reason not in CONTEXT_IGNORED_REASONS:
            return "excluded", heading_reason
        if page_exclusion:
            return "excluded", page_exclusion
    if has_strong(own) or EVALUATION_RE.search(own) or names_evaluation(heading) or page_is_evaluation:
        return "take", document_kind(label, heading, page_title_text)
    return "no_signal", ""


def _kind_from(text: str, *, allow_overview: bool) -> str:
    if EXTERNAL_RE.search(text):
        return "external_review"
    if allow_overview and OVERVIEW_RE.search(text):
        return "overview"
    if POLICY_RE.search(text) and not JIMU_RE.search(text.replace("施策評価", "").replace("政策評価", "")):
        return "policy_evaluation"
    if has_strong(text) or JIMU_RE.search(text) or any(word in text for word in SHEET_WORDS):
        return "project_evaluation"
    return ""


def document_kind(label: str, heading: str = "", page_title_text: str = "") -> str:
    """事務事業評価・施策評価・外部評価・概要のどれか。

    リンク文字を先に見る。手掛かりが無いときは見出し、ページの題名の順に見る
    （阿見町は「第1章 ふれあい…」の PDF が「施策評価を実施しています」の頁に並ぶ）。
    概要・一覧はリンク文字でだけ決める。北区は「令和8年度事務事業評価の概要」の
    頁に評価の本体 1 冊を置いていた。
    """
    for text, allow_overview in ((strip_file_note(label), True), (heading, False), (page_title_text, False)):
        kind = _kind_from(text, allow_overview=allow_overview) if text else ""
        if kind:
            return kind
    return "project_evaluation"


YEAR_TEXT_RE = re.compile(r"(?:令和|平成)\s*[元\d０-９]{1,2}\s*年\s*[)）]?\s*度?|(?:19|20)\d{2}\s*年\s*度?")
# 年度リンクに付いてよい言葉（「令和7年度」「令和7年度分」「2025年度版」）。
YEAR_LINK_REST_RE = re.compile(r"^[\s（）()【】\[\]・:：\-－の分版度実施結果]*$")


def is_year_link(label: str) -> bool:
    """年度だけのリンクか。「令和8年度阿見町組織機構図」のような年つきの別物は除く。"""
    rest = YEAR_TEXT_RE.sub("", label)
    return rest != label and bool(YEAR_LINK_REST_RE.match(rest))


# URL の中のローマ字の年度（堺市 `jimujigyo_r6/`、`h30_hyoka.pdf`、`/2024/`）。
ROMAJI_YEAR_RE = re.compile(
    r"(?:^|[/_\-.])(?:(?P<era>[rRhH])(?P<num>0?[1-9]|[1-3][0-9])|(?P<west>20[0-4][0-9]))(?=[/_\-.]|$)"
)


def romaji_year(url: str) -> tuple[int | None, str]:
    """URL のパスから年度を読む。最後に出てくるものを採る（ファイル名に近い方）。"""
    found = None
    for found in ROMAJI_YEAR_RE.finditer(urlsplit(url).path):
        pass
    if found is None:
        return None, ""
    if found.group("west"):
        return int(found.group("west")), f"{found.group('west')}年度"
    number = int(found.group("num"))
    if found.group("era").lower() == "r":
        return 2018 + number, f"令和{number}年度"
    return 1988 + number, f"平成{number}年度"


def descend_priority(link: Link, page_is_evaluation: bool, sheet_index: bool = False) -> int:
    """年度別・部局別のページへ降りる優先度。0 は降りない。

    評価表に近いものから見る（評価表・調書の語 → 評価結果 → 年度つきの評価 →
    評価の入口）。委員会の開催ページは数が多いので後回しにする（札幌市・北海道）。
    評価のページの上では、見出しの下の年度リンクと、調書一覧の部局リンクも辿る。
    """
    own = f"{link.label} {url_words(link.url)}"
    if not link.label:
        return 0
    if exclusion_reason(own, link.label):
        return 0
    strong = has_strong(own) or bool(STRONG_URL_RE.search(link.url))
    if link.heading and not names_jimu_jigyo(own):
        heading_reason = exclusion_reason(link.heading)
        if heading_reason and heading_reason not in CONTEXT_IGNORED_REASONS:
            return 0
    committee = bool(COMMITTEE_RE.search(link.label)) and not RESULT_RE.search(link.label) and not any(
        word in link.label for word in SHEET_WORDS
    )
    year, _label, _index = fiscal_year_source(link.label)
    if strong:
        return 40 if committee else 100
    if any(word in link.label for word in SHEET_WORDS):
        return 80
    if RESULT_RE.search(link.label) and EVALUATION_RE.search(link.label):
        return 70
    if any(word in link.label for word in HUB_WORDS):
        if committee:
            return 15
        return 60 if year is not None else 50
    if not page_is_evaluation:
        return 0
    # URL の綴りは評価の語だけを見る。北区は行政運営のページを全部
    # `/evaluation/` の下に置いており（内部統制など）、それでは辿らない。
    evaluation_heading = names_evaluation(link.heading)
    if EVALUATION_URL_RE.search(urlsplit(link.url).path) and not committee and (
        EVALUATION_RE.search(link.label) or evaluation_heading
    ):
        return 40
    if committee:
        return 15 if EVALUATION_RE.search(link.label) else 0
    # 年度だけのリンク、または評価の見出しの下の年度つきリンク
    # （栃木県「政策評価」の下の「令和8(2026)年度重点戦略マネジメント」）。
    if year is not None and ((link.heading or sheet_index) and is_year_link(link.label) or evaluation_heading):
        return 30
    if sheet_index and len(link.label) <= 24 and ORG_LABEL_RE.search(link.label):
        return 25
    if EVALUATION_RE.search(link.label):
        return 20
    return 0


# ─────────────────────────────────────
# 収集
# ─────────────────────────────────────

@dataclass
class Candidate:
    url: str
    label: str
    page_url: str
    heading: str
    page_title: str
    file_type: str
    kind: str
    path_year: int = 0
    order: int = 0


@dataclass
class RunResult:
    slug: str
    pages: int = 0
    found: int = 0
    downloaded: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_recent: int = 0
    failed: int = 0
    pending: int = 0
    needs_ocr: int = 0
    missing: int = 0
    no_signal: int = 0
    offsite_files: int = 0
    html_tables: int = 0
    bytes_downloaded: int = 0
    size_hint_bytes: int = 0
    size_hint_count: int = 0
    entry_opened: bool = False
    entry_excluded: str = ""
    year_min: int | None = None
    year_max: int | None = None
    kinds: dict[str, int] = field(default_factory=dict)
    file_types: dict[str, int] = field(default_factory=dict)
    excluded: dict[str, int] = field(default_factory=dict)
    excluded_examples: dict[str, list[dict]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    # 開いたページ（深さ, URL, 題名）。--dry-run --verbose で辿った道筋を見る。
    page_log: list[tuple[int, str, str]] = field(default_factory=list)
    status: str = "ok"

    @property
    def html_only(self) -> bool:
        """ファイルは無く、HTML の表で評価を載せている（表の読み取りは後の段）。"""
        return self.entry_opened and self.found == 0 and self.html_tables > 0

    def note_excluded(self, reason: str, label: str, url: str, page_url: str) -> None:
        self.excluded[reason] = self.excluded.get(reason, 0) + 1
        examples = self.excluded_examples.setdefault(reason, [])
        if len(examples) < EXCLUDED_EXAMPLES:
            examples.append({"label": strip_file_note(label)[:120], "url": url, "page_url": page_url})


def candidate_year(candidate: Candidate) -> tuple[int | None, str, str]:
    """(年度, 読んだ文字, どこから読んだか)。

    リンク文字 → 見出し → ページの題名 → ファイル名の順。どれにも無ければ、
    URL のローマ字（堺市 `jimujigyo_r6/`）、そのページへ来たリンクの年度
    （`path_year`）の順に使う。年度は書かれたとおりで、
    「令和6年度事務事業評価（令和5年度実施事業）」なら評価した年度の 2024 になる。
    """
    sources = ("link_text", "heading", "page_title", "file_name")
    year, label, index = fiscal_year_source(
        candidate.label, candidate.heading, candidate.page_title, url_words(candidate.url),
    )
    if year is not None:
        return year, label, sources[index]
    for url, source in ((candidate.url, "url"), (candidate.page_url, "page_url")):
        year, label = romaji_year(url)
        if year is not None:
            return year, label, source
    if candidate.path_year:
        return candidate.path_year, f"{candidate.path_year}年度", "parent_link"
    return None, "", ""


def collect_candidates(fetcher: Fetcher, target: Target, *, max_pages: int, max_depth: int,
                       result: RunResult) -> list[Candidate]:
    """入口から辿って、取るファイルの候補を集める。取らないものは理由ごとに数える。"""
    entry_host = (urlsplit(target.url).hostname or "").lower()
    order = 0
    # (-優先度, -年度, 順番, URL, 深さ, 年度)。同じ優先度なら新しい年度から開く。
    queue: list[tuple[int, int, int, str, int, int]] = [(-1000, 0, 0, target.url, 0, 0)]
    queued: set[str] = {target.url}
    visited: set[str] = set()
    candidates: dict[str, Candidate] = {}
    while queue and result.pages < max_pages:
        _priority, _year_key, _order, url, depth, path_year = heapq.heappop(queue)
        if url in visited:
            continue
        visited.add(url)
        try:
            fetched = fetcher.html(url)
        except HostBlocked:
            raise
        except requests.RequestException as exc:
            result.pages += 1
            prefix = "入口を開けません" if depth == 0 else "page"
            result.errors.append(f"{prefix} {url}: {type(exc).__name__}")
            continue
        result.pages += 1
        if fetched is None:
            if depth == 0:
                result.errors.append(f"入口を開けません: {url}")
            continue
        if depth == 0:
            result.entry_opened = True
        final_url, html = fetched
        visited.add(final_url)
        title = unescape_label(page_title(html))
        result.page_log.append((depth, final_url, title))
        # ページの題名（と URL の綴り）が別制度を名乗るなら、その頁のファイルは取らない。
        page_exclusion = exclusion_reason(title)
        if not page_exclusion and "shiteikanri" in final_url.lower():
            page_exclusion = "designated_manager"
        if page_exclusion in CONTEXT_IGNORED_REASONS:
            page_exclusion = ""
        if depth == 0:
            # 入口は台帳で確かめた事務事業評価の入口。題名が別制度を名乗るなら
            # 台帳の取り違えを疑う印を残す（ファイルは理由つきで数えるだけ）。
            result.entry_excluded = page_exclusion
            page_is_evaluation = not page_exclusion
        else:
            if page_exclusion:
                continue
            page_is_evaluation = True
        sheet_index = has_strong(title) or any(word in title for word in SHEET_WORDS)
        if has_evaluation_table(html):
            result.html_tables += 1
        page_year = fiscal_year_source(title)[0] or romaji_year(final_url)[0] or path_year
        for raw_link in page_links(final_url, html):
            link = Link(url=raw_link.url, label=unescape_label(raw_link.label), heading=unescape_label(raw_link.heading))
            link_host = (urlsplit(link.url).hostname or "").lower()
            file_type = link_file_type(link.url, link.label)
            if file_type and not file_host_allowed(entry_host, link_host):
                result.offsite_files += 1
                continue
            if not file_type and not same_organization(entry_host, link_host):
                continue
            if file_type:
                if link.url in candidates:
                    continue
                verdict, detail = classify_file_link(
                    link.label, link.url, link.heading, page_title_text=title,
                    page_is_evaluation=page_is_evaluation, page_exclusion=page_exclusion,
                )
                if verdict == "take":
                    hint = size_hint(link.label)
                    if hint:
                        result.size_hint_bytes += hint
                        result.size_hint_count += 1
                    order += 1
                    candidates[link.url] = Candidate(
                        url=link.url, label=link.label, page_url=final_url, heading=link.heading,
                        page_title=title, file_type=file_type, kind=detail, path_year=page_year or 0,
                        order=order,
                    )
                elif verdict == "excluded":
                    result.note_excluded(detail, link.label, link.url, final_url)
                else:
                    result.no_signal += 1
                continue
            if depth >= max_depth or link.url in visited or link.url in queued:
                continue
            priority = descend_priority(link, page_is_evaluation, sheet_index)
            if priority <= 0:
                continue
            link_year = fiscal_year_source(link.label)[0] or page_year or 0
            order += 1
            queued.add(link.url)
            heapq.heappush(queue, (-priority, -link_year, order, link.url, depth + 1, link_year))
    return list(candidates.values())


# ─────────────────────────────────────
# 保存
# ─────────────────────────────────────

def extract_document_text(file_type: str, raw: bytes) -> tuple[str, dict]:
    """形式ごとに本文を抜き出す。(本文, entry に足す項目)。"""
    if file_type == "pdf":
        body, pages = extract_pdf_text(raw)
        return body, {"pages": pages}
    if file_type in ("xlsx", "xlsm"):
        body, sheets = extract_xlsx_text(raw)
        return body, {"sheets": sheets}
    if file_type == "docx":
        return extract_docx_text(raw), {}
    if file_type == "csv":
        return decode_csv(raw), {}
    if file_type == "zip":
        return "", {"archive_members": zip_member_names(raw)}
    return "", {}


def text_status_for(file_type: str, body: str, extra: dict) -> str:
    if file_type == "pdf":
        if len(body) >= MIN_TEXT_CHARS:
            return "ok"
        # スキャンだけの PDF。本文は OCR の掃き取りに任せる。
        return "needs_ocr" if extra.get("pages") else "unreadable"
    if file_type in ("xlsx", "xlsm", "docx", "csv"):
        return "ok" if body.strip() else "unreadable"
    if file_type == "zip":
        return "archive"
    # xls・doc・pptx・ods・odt は、取得イメージに読む部品が無い。
    return "unsupported"


def set_status(entry: dict) -> None:
    """文書の状態をひとことで表す（ok / pending / error / gone）。"""
    if entry.get("missing_since"):
        entry["status"] = "gone"
        entry["status_reason"] = f"{entry['missing_since']} から取得元のページに見当たらない"
    elif entry.get("file"):
        entry["status"] = "ok"
        entry["status_reason"] = (
            f"取り直しに失敗（保存済みの版を残している）: {entry['last_error']}" if entry.get("last_error") else ""
        )
    elif entry.get("last_error"):
        entry["status"] = "error"
        entry["status_reason"] = str(entry["last_error"])
    else:
        entry["status"] = "pending"
        entry["status_reason"] = "まだ取っていない（1 回の上限 --max-docs を超えた分か、ホストを休んだ回）"


def scrape_one(target: Target, fetcher: Fetcher, work_root: Path, *, max_pages: int = DEFAULT_MAX_PAGES,
               max_depth: int = DEFAULT_MAX_DEPTH, max_docs: int = DEFAULT_MAX_DOCS,
               refresh_days: int = DEFAULT_REFRESH_DAYS, dry_run: bool = False,
               verbose: bool = False) -> RunResult:
    work_dir = work_root / target.slug
    result = RunResult(slug=target.slug)
    started_at = now_text()
    manifest = {} if dry_run else load_manifest(work_dir)
    documents: dict[str, dict] = dict(manifest.get("documents") or {})
    try:
        candidates = collect_candidates(fetcher, target, max_pages=max_pages, max_depth=max_depth, result=result)
    except HostBlocked as exc:
        result.status = "host_blocked"
        result.errors.append(f"ホストを休止中: {exc}")
        candidates = []
    result.found = len(candidates)

    # 新しい年度から扱う。1 回の上限で切ったときに最新の評価表が残るように。
    years = {c.url: candidate_year(c) for c in candidates}
    candidates.sort(key=lambda c: (-(years[c.url][0] or 0), c.order))
    for candidate in candidates:
        year = years[candidate.url][0]
        if year is not None:
            result.year_min = year if result.year_min is None else min(result.year_min, year)
            result.year_max = year if result.year_max is None else max(result.year_max, year)
        result.kinds[candidate.kind] = result.kinds.get(candidate.kind, 0) + 1
        result.file_types[candidate.file_type] = result.file_types.get(candidate.file_type, 0) + 1
    listed_ids = {doc_id_for(c.url) for c in candidates}

    if dry_run:
        shown = candidates if verbose else candidates[:30]
        for candidate in shown:
            year = years[candidate.url][0]
            result.lines.append(
                f"  [{year or '----'}] {candidate.kind:<18} {candidate.file_type:<4} "
                f"{strip_file_note(candidate.label)[:60]} <{candidate.url}>"
            )
        if len(candidates) > len(shown):
            result.lines.append(f"  … ほか {len(candidates) - len(shown)} 件（--verbose で全部）")
        for reason, examples in sorted(result.excluded_examples.items()):
            limit = EXCLUDED_EXAMPLES if verbose else 2
            for example in examples[:limit]:
                result.lines.append(f"  除外[{reason}] {example['label'][:60]} <{example['url']}>")
        if verbose:
            for depth, url, title in result.page_log:
                result.lines.append(f"  頁{'  ' * depth}{depth} {title[:50]} <{url}>")
        return result

    now = datetime.now(JST)
    attempts = 0
    for candidate in candidates:
        doc_id = doc_id_for(candidate.url)
        entry = dict(documents.get(doc_id) or {})
        year, year_label, year_source = years[candidate.url]
        title = strip_file_note(candidate.label) or strip_file_note(candidate.heading) or candidate.page_title
        entry.update({
            "source_url": candidate.url,
            "page_url": candidate.page_url,
            "page_title": candidate.page_title,
            "heading": candidate.heading,
            "link_text": candidate.label,
            "title": title,
            "fiscal_year": year,
            "year_label": year_label,
            "year_source": year_source,
            "kind": candidate.kind,
            "link_file_type": candidate.file_type,
        })
        entry.setdefault("first_seen_at", now_text())
        entry.pop("missing_since", None)
        documents[doc_id] = entry
        if result.status == "host_blocked":
            continue

        if attempts >= max_docs:
            # 1 回の上限を超えた。次の回に取る（保存済みのものは取り直しを次の回へ回すだけ）。
            if not (entry.get("file") and (work_dir / str(entry["file"])).is_file()):
                result.pending += 1
            continue
        try:
            fetched = fetch_document(
                fetcher, candidate.url, entry, work_dir, now=now, refresh_days=refresh_days,
                max_bytes=MAX_DOCUMENT_BYTES, check=check_document_response,
            )
        except HostBlocked as exc:
            result.status = "host_blocked"
            result.errors.append(f"ホストを休止中: {exc}")
            continue
        if fetched.state == "recent":
            result.skipped_recent += 1
            continue
        attempts += 1
        if fetched.state == "not_modified":
            entry["checked_at"] = now_text()
            result.unchanged += 1
            continue
        if fetched.state == "failed":
            result.failed += 1
            note_failure(entry, fetched.error)
            result.errors.append(f"{candidate.url}: {type(fetched.error).__name__}")
            continue

        raw = fetched.raw
        sha256, same = note_fetched(entry, fetched)
        if same:
            result.unchanged += 1
            continue
        headers = fetched.headers or {}
        file_type = sniff_document_type(
            raw, candidate.url, headers.get("Content-Type", ""), headers.get("Content-Disposition", ""),
            hint=candidate.file_type,
        ) or candidate.file_type
        had_previous = bool(fetched.file_ok and entry.get("sha256"))
        previous_file = str(entry.get("file") or "")
        relative_file = f"source/{doc_id}{DOCUMENT_EXTENSIONS.get(file_type, '.bin')}"
        relative_text = f"text/{doc_id}.txt.gz"
        write_bytes(work_dir / relative_file, raw)
        if previous_file and previous_file != relative_file and (work_dir / previous_file).is_file():
            # 形式が変わった（xls → xlsx など）。古い版は脇へ残す。
            (work_dir / previous_file).replace(work_dir / f"{previous_file}.replaced-{sha256[:8]}")
        body, extra = extract_document_text(file_type, raw)
        for key in ("pages", "sheets", "archive_members"):
            entry.pop(key, None)
        entry.update({
            "file": relative_file,
            "file_type": file_type,
            "sha256": sha256,
            "bytes": len(raw),
            "content_type": headers.get("Content-Type", ""),
            "fetched_at": now_text(),
            "text_chars": len(body),
            **extra,
        })
        result.bytes_downloaded += len(raw)
        entry["text_status"] = text_status_for(file_type, body, extra)
        if entry["text_status"] == "ok":
            write_bytes(work_dir / relative_text, gzip.compress(text_document(entry, body).encode("utf-8")))
            entry["text_file"] = relative_text
        else:
            entry["text_file"] = ""
            if entry["text_status"] in ("needs_ocr", "unreadable") and file_type == "pdf":
                result.needs_ocr += 1
        if had_previous:
            result.updated += 1
        else:
            result.downloaded += 1

    # 見つからなくなった文書。保存物は残し、消えたことだけ記録する。
    # 入口が開けなかった回・ホストを休んだ回は判断しない。
    if result.status == "ok" and result.entry_opened:
        result.missing += len(mark_missing(documents, listed_ids))
    for entry in documents.values():
        set_status(entry)

    if not result.entry_opened and result.status == "ok":
        result.status = "entry_unreachable"
    elif result.failed and result.status == "ok":
        result.status = "partial"

    write_json(work_dir / "manifest.json", {
        "version": MANIFEST_VERSION,
        "slug": target.slug,
        "jis_code": target.code,
        "name": target.name,
        "entry_url": target.url,
        "registry_status": target.crawl_status,
        "updated_at": now_text(),
        "last_run": run_summary(result, started_at),
        "excluded_examples": result.excluded_examples,
        "documents": dict(sorted(documents.items())),
    })
    return result


def run_summary(result: RunResult, started_at: str = "") -> dict:
    return {
        "started_at": started_at,
        "finished_at": now_text(),
        "status": result.status,
        "pages": result.pages,
        "found": result.found,
        "kinds": dict(sorted(result.kinds.items())),
        "file_types": dict(sorted(result.file_types.items())),
        "excluded": dict(sorted(result.excluded.items())),
        "no_signal_links": result.no_signal,
        "offsite_files": result.offsite_files,
        "html_tables": result.html_tables,
        "html_only": result.html_only,
        "entry_excluded": result.entry_excluded,
        "year_min": result.year_min,
        "year_max": result.year_max,
        "downloaded": result.downloaded,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "skipped_recent": result.skipped_recent,
        "failed": result.failed,
        "pending": result.pending,
        "needs_ocr": result.needs_ocr,
        "missing": result.missing,
        "bytes_downloaded": result.bytes_downloaded,
        # 候補のうちリンク文字に大きさの注記があったものの合計（見積もり用）。
        "size_hint_bytes": result.size_hint_bytes,
        "size_hint_count": result.size_hint_count,
        "errors": result.errors[:50],
    }


# ─────────────────────────────────────
# CLI
# ─────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="事務事業評価の評価表・報告書を取得して保存する。")
    pick = parser.add_argument_group("対象")
    pick.add_argument("--slug", action="append", default=[], help="自治体 slug（複数可）")
    pick.add_argument("--code", action="append", default=[], help="自治体コード（複数可）")
    pick.add_argument("--all", action="store_true", help="台帳の enabled をすべて")
    pick.add_argument("--include-review", action="store_true", help="review_required も対象にする")
    pick.add_argument("--limit", type=int, default=0, help="対象の自治体数の上限")
    parser.add_argument("--out-dir", default="", help=f"保存先（既定 {DEFAULT_WORK_ROOT}）")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="1 件ごとの間隔（秒、1 以上）")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="1 自治体で開くページの上限")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="入口から降りる階層")
    parser.add_argument("--max-docs", type=int, default=DEFAULT_MAX_DOCS,
                        help="1 自治体で 1 回に取るファイルの上限（新しい年度から。残りは次の回）")
    parser.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS, help="取得済みを問い合わせ直すまでの日数")
    parser.add_argument("--workers", type=int, default=1,
                        help="同時に扱う自治体の数（ホストごとにまとめるので、同じホストは並ばない）")
    parser.add_argument("--dry-run", action="store_true", help="候補を表示するだけで取らない")
    parser.add_argument("--verbose", action="store_true", help="--dry-run で候補と除外の例を全部表示する")
    parser.add_argument("--summary-json", default="", help="実行結果の要約を書き出す先")
    return parser


def select_targets(args: argparse.Namespace) -> list[Target]:
    targets = load_targets(REGISTRY, include_review=args.include_review or bool(args.slug or args.code))
    if args.slug or args.code:
        wanted_slugs = set(args.slug)
        wanted_codes = {code.strip() for code in args.code}
        targets = [t for t in targets if t.slug in wanted_slugs or t.code in wanted_codes]
    elif not args.all:
        return []
    if args.limit > 0:
        targets = targets[: args.limit]
    return targets


def host_groups(targets: list[Target]) -> list[list[Target]]:
    """入口のホストごとにまとめる。同じホストの自治体は 1 本の流れで順に扱う。"""
    groups: dict[str, list[Target]] = defaultdict(list)
    for target in targets:
        host = (urlsplit(target.url).hostname or "").lower().removeprefix("www.")
        groups[host].append(target)
    return list(groups.values())


def result_line(result: RunResult) -> str:
    kinds = " ".join(f"{kind}:{result.kinds[kind]}" for kind in KINDS if result.kinds.get(kind))
    excluded = sum(result.excluded.values())
    marks = []
    if result.size_hint_count:
        marks.append(f"注記の大きさ{result.size_hint_bytes / 1048576:.1f}MB/{result.size_hint_count}件")
    if result.html_only:
        marks.append("HTMLの表だけ")
    if result.entry_excluded:
        marks.append(f"入口が別制度?({result.entry_excluded})")
    return (
        f"  -> {result.status} ページ{result.pages} 候補{result.found} [{kinds or '-'}] "
        f"除外{excluded} 手掛かり無し{result.no_signal} 取得{result.downloaded} 更新{result.updated} "
        f"変化なし{result.unchanged} 最近確認済み{result.skipped_recent} 失敗{result.failed} "
        f"保留{result.pending} OCR待ち{result.needs_ocr} 消えた{result.missing} "
        f"{result.bytes_downloaded / 1048576:.1f}MB" + (f" {' '.join(marks)}" if marks else "")
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = select_targets(args)
    if not targets:
        print("対象がありません。--slug / --code / --all のどれかを指定してください。", file=sys.stderr)
        return 2
    # pypdf は日本語の字形表（UniJIS-UTF16-H など）でページごとに警告を出す。
    # 本文は読めているので、実行記録が埋まらないよう黙らせる。
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    work_root = Path(args.out_dir) if args.out_dir else DEFAULT_WORK_ROOT
    work_root.mkdir(parents=True, exist_ok=True)
    delay = max(1.0, float(args.delay))
    print_lock = threading.Lock()
    summary: list[dict] = []
    fetchers: list[Fetcher] = []
    counter = {"done": 0}

    def run_group(group: list[Target]) -> None:
        fetcher = Fetcher(session=requests.Session(), guard=HostGuard(work_root), delay=delay)
        fetchers.append(fetcher)
        for target in group:
            try:
                result = scrape_one(
                    target, fetcher, work_root,
                    max_pages=args.max_pages, max_depth=args.max_depth, max_docs=args.max_docs,
                    refresh_days=args.refresh_days, dry_run=args.dry_run, verbose=args.verbose,
                )
            except Exception as exc:  # 1 自治体の失敗で全体を止めない
                result = RunResult(slug=target.slug, status="error", errors=[f"{type(exc).__name__}: {exc}"])
            with print_lock:
                counter["done"] += 1
                print(f"[{counter['done']}/{len(targets)}] {target.slug} {target.name} <{target.url}>", flush=True)
                for line in result.lines:
                    print(line, flush=True)
                print(result_line(result), flush=True)
                for error in result.errors[:5]:
                    print(f"     {error}", flush=True)
                summary.append({
                    "code": target.code, "slug": target.slug, "name": target.name, "entry_url": target.url,
                    "registry_status": target.crawl_status, **run_summary(result),
                })

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(run_group, host_groups(targets)))

    summary.sort(key=lambda row: row["code"])
    html_only = [row for row in summary if row["html_only"]]
    entry_excluded = [row for row in summary if row["entry_excluded"]]
    no_files = [row for row in summary if row["found"] == 0 and not row["html_only"]]
    totals: dict[str, int] = defaultdict(int)
    excluded_totals: dict[str, int] = defaultdict(int)
    for row in summary:
        for kind, count in row["kinds"].items():
            totals[kind] += count
        for reason, count in row["excluded"].items():
            excluded_totals[reason] += count
    print(f"[DONE] 自治体 {len(targets)} / HTTP 要求 {sum(f.requests_made for f in fetchers)} / "
          f"候補 {sum(row['found'] for row in summary)} / "
          f"取得 {sum(row['bytes_downloaded'] for row in summary) / 1048576:.1f}MB", flush=True)
    print("  種別: " + " ".join(f"{kind}:{totals[kind]}" for kind in KINDS), flush=True)
    print("  除外: " + " ".join(f"{reason}:{count}" for reason, count in sorted(excluded_totals.items())), flush=True)
    if html_only:
        print("  HTML の表だけで載せている（ファイル無し。表の読み取りは後の段）: "
              + "、".join(f"{row['code']} {row['name']}" for row in html_only), flush=True)
    if entry_excluded:
        print("  入口の題名が別制度を名乗る（台帳の確認が要る）: "
              + "、".join(f"{row['code']} {row['name']}（{row['entry_excluded']}）" for row in entry_excluded),
              flush=True)
    if no_files:
        print("  候補が 0 件: " + "、".join(f"{row['code']} {row['name']}" for row in no_files), flush=True)
    if args.summary_json:
        write_json(Path(args.summary_json), {"generated_at": now_text(), "dry_run": args.dry_run, "results": summary})
    return 0 if all(row["status"] == "ok" for row in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
