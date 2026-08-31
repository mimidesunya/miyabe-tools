"""会議録として公開してよいかを、題名と本文から判定する。

独自スクレイパは議会サイト上の PDF を選別せず拾う。議案・資料・広報・表紙が
会議録の席を占めると、取れていないのではなく別の文書が正しい名前で出る。
判定をスクレイパに閉じると既に索引された分は残るので、索引側の
`classify_doc_type` からも呼べる純粋関数として置く。
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
WEEKDAY_INDEX = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
# 年ラベルと 2 年以上ずれる開催日は、OCR の「６／７→９」か別文書の期限である。
YEAR_LABEL_GAP = 2

# 表紙・目次・資料は static-kaigiroku-dir が既に落としている。独自スクレイパにも
# 同じ種類の判定が要る。案件N・議会だよりは会議録の題名にはならない。
SKIP_LABEL_RE = re.compile(
    r"^(?:"
    r".*目次(?:\s*[\[［【].*[\]］】])?"
    r"|表紙|扉|奥付|索引"
    r"|資料(?:[①-⑳\d０-９一二三四五六七八九十]+)?"
    r"(?:\s*[（(].*[）)])?"
    # 「案件1」だけでなく「案件1_補足資料」「案件2-2」「報告事項1-5」もある。
    # 番号のあとに枝番や添え名が付いても、会議録ではなく資料である。
    r"|案件\s*[0-9０-９一二三四五六七八九十]+号?(?:[-‐－_.・][^\s]*)?"
    r"|報告(?:事項)?\s*[0-9０-９一二三四五六七八九十]+号?(?:[-‐－_.・][^\s]*)?"
    r"|議案概要"
    # 議会サイトには会議録と並んで、意見書・出席者一覧・議員名簿・
    # 一般質問の一覧が置いてある。題名がそれだけなら会議録ではない。
    # 「意見書について審議した委員会記録」のような長い題名は落とさない。
    r"|(?:.*意見書(?:案)?(?:一覧|集)?)"
    r"|(?:.*出席者?一覧(?:表)?)"
    r"|(?:.*議員名簿)"
    r"|(?:.*一般質問(?:事項)?一覧(?:表)?)"
    r"|(?:.*質問(?:者|事項)一覧(?:表)?)"
    r"|(?:.*議案付託一覧(?:表)?)"
    # 「○○一覧表」は会議の記録ではなく、その会議に付いた資料である。
    # 会議録が「一覧表」で終わる題名になることはない。
    r"|(?:.+一覧表)"
    # 飯塚市を取り直したあとも会議録の席に残っていたもの。会議に付いた
    # 書類であって、会議の記録ではない。
    r"|(?:.*会期日程(?:表)?)"
    r"|(?:.*請願(?:書|文書表))"
    r"|(?:.*陳情(?:書|文書表))"
    r"|(?:.*議案一覧(?:表)?)"
    r"|(?:.*付託表)"
    # 「03月12日－名簿」「議事日程・名簿」「３月定例会議決結果一覧」も
    # 会議の記録ではない。本番で「名簿」で終わる題名が 2,624 件、
    # 「一覧」で終わるものが 746 件あった。
    r"|(?:.*名簿)"
    r"|(?:.*(?:議決|審査|質問|採決)(?:結果)?一覧)"
    r"|(?:.*議事日程(?:・[^\s]*)?)"
    r"|(?:.*請願(?:・|、)?陳情一覧(?:表)?)"
    r")$"
)
COVER_IN_TITLE_RE = re.compile(r"表紙")
NEWSLETTER_RE = re.compile(r"議会だより|議会広報|CS議会広報")
# 「広報広聴委員会会議録」は会議録。題名が広報そのものだけを落とす。
PUBLICITY_ONLY_RE = re.compile(r"^広報(?:紙|誌)?(?:\s|[第0-9０-９]|$)")
DIGIT_ONLY_RE = re.compile(r"^[0-9０-９]+$")
WEAK_TITLE_RE = re.compile(
    r"^(?:"
    r"[0-9０-９]+"
    r"|[0-9０-９]+日"
    r"|[0-9０-９]{1,2}月[0-9０-９]{1,2}日"
    r"|開議|開会|閉議|散会"
    r"|pdf|本文|詳細|会議録|議事録"
    # 保存名がリンク文言のままの取得元。会議の名前になっていないので、
    # 本文先頭が名乗っている会議名へ置き換える。本番で釧路町の「会議録」
    # 224 件、和木町の「初日」「最終日」、舟橋村の「招集告示」があった。
    r"|初日|最終日|中日|最終|議事日程"
    r"|招集告示|告示|会議録署名"
    r"|第[0-9０-９]{1,2}日(?:目)?"
    r")(?:-[0-9a-f]{6,})?$",
    re.IGNORECASE,
)
PDF_NOTE_RE = re.compile(
    r"\s*[\[\(（［【]\s*PDF(?:ファイル)?\s*[^\]\)）］】]*[\]\)）］】]\s*$",
    re.IGNORECASE,
)
PDF_FILE_NOTE_RE = re.compile(
    r"\s*[［\[(（]?\s*PDFファイル\s*[／/：:]\s*[^］\]）)]+[］\]）)]?\s*$",
    re.IGNORECASE,
)
SIZE_NOTE_RE = re.compile(
    r"\s*[（(][\d,.]+\s*(?:kb|mb|kbyte|mbyte|バイト)[）)]\s*$",
    re.IGNORECASE,
)

MINUTES_TITLE_HINTS = ("会議録", "議事録", "委員会記録", "審議録")
MINUTES_BODY_MARKERS = (
    "開議",
    "閉議",
    "散会",
    "出席議員",
    "欠席議員",
    "会議録署名",
    "議事日程",
    "会議に付した事件",
    "これより会議を開",
    # 委員会記録は本会議と語彙が違う。札幌市の常任委員会は「開　会」「閉　会」
    # 「委員長」しか無く、上の語がひとつも出ない。実データで 963 件がこの形で、
    # 本物の委員会記録を議案と読み違えていた。
    "開会",
    "閉会",
    "出席委員",
    "欠席委員",
    "委員長",
    "副委員長",
    "委員会記録",
    "会議録",
    "議事録",
)
# 本文が「PDF をごらんください」だけの会議録がある。gijiroku.com の古い年で、
# 発言そのものは PDF の中にある。会議録として検索に載るが本文が無い。
# 本番で町田市 529 件・岩見沢市 212 件・港区 131 件（本文 120 字未満は全国 1,241）。
PDF_NOTICE_MARKERS = (
    "ＰＤＦファイルをごらんください",
    "PDFファイルをごらんください",
    "ＰＤＦファイルをご覧ください",
    "PDFファイルをご覧ください",
    "ＰＤＦをごらんください",
    "PDFをごらんください",
    # 取得元ごとに言い方が違う。完全一致の並びだけでは足りなかった
    # （岩見沢市 212 件・港区 131 件は別の言い方で残っていた）。
    "ＰＤＦファイルをご覧下さい",
    "PDFファイルをご覧下さい",
    "ＰＤＦファイルを御覧ください",
    "データを添付しています",
    "速記録のデータを添付",
    "添付ファイルをご覧",
    "添付ファイルをごらん",
    "下記のＰＤＦ",
    "下記のPDF",
    "【資料】",
)
# 会議が実際に開かれた手がかり。「会議録」のような文書の名前は、案内文にも
# 出てくるので含めない。
MEETING_HELD_MARKERS = (
    "開議",
    "閉議",
    "散会",
    "出席議員",
    "欠席議員",
    "会議録署名",
    "議事日程",
    "会議に付した事件",
    "これより会議を開",
    "開会",
    "閉会",
    "出席委員",
    "欠席委員",
    "委員長",
)
# 巻末資料・付録は会議の記録ではない。ただし本文に開議があるなら会議録である。
APPENDIX_ONLY_MARKERS = ("巻末資料", "巻末付録")
# 本文がこの長さに満たなければ、案内文や資料の見出しだけとみなす。
NOTICE_BODY_MAX_LENGTH = 400

BILL_OR_MATERIAL_MARKERS = (
    "議案第",
    "報告第",
    "専決処分",
    "提案理由",
    "予算書",
    "決算書",
    "事業計画",
    "別記様式",
    "議案概要",
)

# 取得元は「令和４年（2022年）３月11日」と西暦を併記することがある。
# 「年」の直後に月を求めると、その括弧で切れて開催日が読めない。
_WESTERN_IN_PARENS = r"(?:\s*[（(]\s*(?:19|20)[\d０-９]{2}\s*年?\s*[）)])?"
ERA_DATE_RE = re.compile(
    r"(昭和|平成|令和)\s*([元\d０-９]+)\s*年" + _WESTERN_IN_PARENS + r"\s*"
    r"([\d０-９]{1,2})\s*月\s*"
    r"([\d０-９]{1,2})\s*日"
    r"(?:\s*[（(]?\s*([月火水木金土日])(?:曜日)?\s*[）)]?)?"
)
WESTERN_DATE_RE = re.compile(
    r"(19\d{2}|20\d{2})\s*年\s*"
    r"([\d０-９]{1,2})\s*月\s*"
    r"([\d０-９]{1,2})\s*日"
    r"(?:\s*[（(]?\s*([月火水木金土日])(?:曜日)?\s*[）)]?)?"
)
ISO_DATE_RE = re.compile(r"\b(19\d{2}|20\d{2})-(\d{2})-(\d{2})\b")
YEAR_LABEL_RE = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年")
# 原典ファイル名の R7_09_18 / R060912 は OCR を経由しない開催日の手がかり。
# 元号の頭文字はファイル名でも URL でも使われる。令和だけを見ていたので、
# 平成の `fileName=H160310A` が読めなかった（kensakusystem で 10,546 件）。
FILE_ERA_BASE_YEAR = {"R": ERA_BASE_YEAR["令和"], "H": ERA_BASE_YEAR["平成"], "S": ERA_BASE_YEAR["昭和"]}
ERA_FILE_DATE_RE = re.compile(
    r"(?:^|[=_\-/])([RHS])(\d{1,2})[_\-](\d{2})[_\-](\d{2})(?:[_\-.]|$)",
    re.IGNORECASE,
)
# ファイル名は西暦でも書かれる。`20240610.pdf` `2024-06-10.pdf` `2024.06.10.pdf`。
# 元号の形しか読んでいなかったので、これらでは題名との一致が効かなかった。
WESTERN_FILE_DATE_RE = re.compile(
    r"(?:^|[=_\-/.])((?:19|20)\d{2})[-_./]?(\d{2})[-_./]?(\d{2})(?![0-9])"
)
ERA_FILE_COMPACT_RE = re.compile(
    r"(?:^|[=_\-/])([RHS])(\d{2})(\d{2})(\d{2})(?:[A-Za-z_\-.]|$)",
    re.IGNORECASE,
)


# pypdf は、ToUnicode を持たない CID フォント（日本語 PDF の 90ms-RKSJ-H など）から
# Shift_JIS のバイト列をそのまま 1 バイト 1 文字として返す。保存すると本文が
# 文字化けしたまま索引され、日本語では二度と検索に当たらない。
# 小海町の `平 成 ２ ７ 年` は `½ ¬ Q V N` だった。
_LATIN1_SUPPLEMENT_RE = re.compile(r"[-ÿ]")


def repair_cp932_mojibake(text: str) -> str:
    """1 バイト文字として読まれた Shift_JIS を読み直す。直せないなら元のまま返す。

    保存ファイルは、スクレイパが日本語で `出典:` と題名を足してから PDF 本文を
    繋げている。全体をまとめて読み直そうとすると、日本語が 1 字でもあれば
    latin-1 へ戻せず、化けた本文が残る。**行ごとに見て、化けている行だけ直す。**
    """
    if text == "":
        return text
    lines = text.split("\n")
    repaired_lines: list[str] = []
    repaired_any = False
    for line in lines:
        fixed = _repair_line(line)
        if fixed != line:
            repaired_any = True
        repaired_lines.append(fixed)
    if not repaired_any:
        return text
    return "\n".join(repaired_lines)


def _repair_line(line: str) -> str:
    if line == "":
        return line
    latin1_count = len(_LATIN1_SUPPLEMENT_RE.findall(line))
    # 日本語の本文にラテン補助が 1 割も出ることはない。出るなら復号が誤っている。
    if latin1_count * 10 < len(line):
        return line
    try:
        repaired = line.encode("latin-1").decode("cp932")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return line
    # 直したつもりで壊さない。日本語が増えたときだけ採る。
    if _japanese_ratio(repaired) <= _japanese_ratio(line):
        return line
    return repaired


def _japanese_ratio(text: str) -> float:
    sample = text[:4000]
    if sample == "":
        return 0.0
    japanese = sum(
        1
        for ch in sample
        if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "＀" <= ch <= "￯"
    )
    return japanese / len(sample)


# PDF から取り出した日付に康煕部首が混ざる（`平成20年12⽉4⽇` の `⽉` は
# U+2F49、`⽇` は U+2F47）。日付の正規表現は U+6708 の `月` しか見ないので、
# 開催日が読めない。清里町で 65 件あった。NFKC で普通の字へ寄せる。
def normalize_compatibility_forms(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def normalize_space(value: str) -> str:
    value = html.unescape(str(value or "")).replace("\u200b", "")
    return re.sub(r"[ \t\r\n\u3000]+", " ", value).strip()


def to_ascii_digits(value: str) -> str:
    return str(value or "").translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value).strip()
    if raw == "元":
        return 1
    if raw.isdigit():
        return int(raw)
    return None


def era_to_gregorian(era: str, year_text: str) -> int | None:
    era_year = japanese_year_to_int(year_text)
    base_year = ERA_BASE_YEAR.get(era)
    if era_year is None or base_year is None:
        return None
    return base_year + era_year


def gregorian_year_from_label(year_label: str, source_year: int | None = None) -> int | None:
    if isinstance(source_year, int) and source_year > 0:
        return source_year
    match = YEAR_LABEL_RE.search(normalize_space(year_label))
    if not match:
        return None
    return era_to_gregorian(match.group(1), match.group(2))


def strip_pdf_notes(value: str) -> str:
    """リンク末尾のファイル種別・サイズ注記は題名ではない。"""
    label = normalize_space(value)
    prev = None
    while label and label != prev:
        prev = label
        label = PDF_NOTE_RE.sub("", label).strip()
        label = PDF_FILE_NOTE_RE.sub("", label).strip()
        label = SIZE_NOTE_RE.sub("", label).strip()
        label = re.sub(r"\s*PDFファイル\s*$", "", label, flags=re.I).strip()
    return label


def looks_like_minutes_title(title: str) -> bool:
    return any(hint in title for hint in MINUTES_TITLE_HINTS)


# PDF の題名は「招 集 告 示」のように字間へ空白を入れて組むことがある。
# そのままでは弱い題名にも落とす題名にも当たらない。字間だけの空白は落とす。
def squeeze_letter_spacing(title: str) -> str:
    cleaned = normalize_space(title)
    if cleaned == "":
        return cleaned
    parts = cleaned.split(" ")
    # 1 文字ずつ並んでいるときだけ詰める。語の区切りは残す。
    if len(parts) >= 3 and all(len(part) == 1 for part in parts):
        return "".join(parts)
    return cleaned


def link_title_is_weak(title: str) -> bool:
    """リンク文言だけでは会議を名乗れないとき、本文先頭から題名を読む。"""
    cleaned = strip_pdf_notes(title)
    if cleaned == "":
        return True
    if WEAK_TITLE_RE.fullmatch(cleaned):
        return True
    if WEAK_TITLE_RE.fullmatch(squeeze_letter_spacing(cleaned)):
        return True
    if DIGIT_ONLY_RE.fullmatch(to_ascii_digits(cleaned).replace(" ", "")):
        return True
    return len(cleaned) <= 2


# 「議員名簿（令和8年2月20日現在）（PDFファイル／104KB）」のように、
# 注記を剥いでも括弧が残ると `.*議員名簿$` に当たらない。末尾の括弧も落として
# もう一度みる。中身のある題名まで削らないよう、末尾だけを対象にする。
_TRAILING_PARENS_RE = re.compile(r"(?:[\s　]*[（(][^（()）]*[）)])+$")


def strip_trailing_parens(title: str) -> str:
    stripped = _TRAILING_PARENS_RE.sub("", str(title or "")).strip()
    return stripped or str(title or "").strip()


# 題名が丸ごと括弧に入っていることがある。`（資料）`（出席表）は本番で
# **18,733 件**あり、うち 13,566 件は開催日も無い。会議に付いた資料であって
# 会議の記録ではない。囲みを外してからもう一度みる。
_WRAPPING_PARENS_RE = re.compile(r"^[（(【［\[]\s*(.+?)\s*[）)】］\]]$")


def strip_wrapping_parens(title: str) -> str:
    text = normalize_space(title)
    match = _WRAPPING_PARENS_RE.fullmatch(text)
    return match.group(1) if match else text


def _label_reason(title: str) -> str | None:
    cleaned = strip_pdf_notes(title)
    if cleaned == "":
        return None
    if looks_like_minutes_title(cleaned) and not COVER_IN_TITLE_RE.search(cleaned):
        # 「広報広聴委員会会議録」は広報ではなく会議録。
        if NEWSLETTER_RE.search(cleaned) or PUBLICITY_ONLY_RE.search(cleaned):
            return "non_minutes_label"
        if SKIP_LABEL_RE.match(cleaned):
            return "non_minutes_label"
        return None
    # 字間に空白を入れて組む取得元がある（`議 案 一 覧 表`）。弱い題名の判定では
    # 詰めていたのに、落とす題名の判定では詰めていなかった。同じ形を通す。
    candidates = {
        cleaned,
        strip_trailing_parens(cleaned),
        strip_wrapping_parens(cleaned),
        squeeze_letter_spacing(cleaned),
        strip_wrapping_parens(squeeze_letter_spacing(cleaned)),
    }
    if any(SKIP_LABEL_RE.match(candidate) for candidate in candidates if candidate):
        return "non_minutes_label"
    if COVER_IN_TITLE_RE.search(cleaned):
        return "cover_only"
    if NEWSLETTER_RE.search(cleaned) or PUBLICITY_ONLY_RE.search(cleaned):
        return "non_minutes_label"
    return None


def _head_lines(text: str, limit: int = 20) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = normalize_space(raw)
        if line:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _head_text(text: str, limit: int = 20) -> str:
    return "\n".join(_head_lines(text, limit=limit))


# 会議録は「開　会」のように語の中へ空白を入れて字下げする。素のまま探すと
# 当たらないので、空白（全角を含む）を落としてから照合する。
_MARKER_SPACE_RE = re.compile(r"[\s\u3000]+")


def minutes_marker_count(text: str) -> int:
    head = _head_text(text, limit=40) + "\n" + str(text or "")[:4000]
    squeezed = _MARKER_SPACE_RE.sub("", head)
    return sum(1 for marker in MINUTES_BODY_MARKERS if marker in squeezed)


# PDF の抽出に失敗すると、文字ではなくグリフ名が並ぶ（垂水市の本文 190 万字は
# `/g5140 /g5777 /g14814` で始まる）。ラテン補助は 0 なので文字化けの判定には
# 当たらないが、日本語としては読めない。会議録として載せる意味がない。
_GLYPH_NAME_RE = re.compile(r"/g[0-9]{2,}")


def body_is_unreadable_glyph_names(text: str) -> bool:
    sample = str(text or "")[:4000]
    if sample.strip() == "":
        return False
    if _japanese_ratio(sample) >= 0.05:
        return False
    glyphs = _GLYPH_NAME_RE.findall(sample)
    # 本文の大半がグリフ名で埋まっているときだけ。
    return len("".join(glyphs)) * 2 >= len(sample)


def body_is_only_a_pdf_notice(text: str) -> bool:
    """本文が PDF への案内文や巻末資料の見出しだけかを返す。

    発言が入っていないので、会議録として検索に載せる意味がない。
    長い本文にたまたま案内文が混ざっている場合は落とさない。"""
    body = normalize_space(text)
    if body == "" or len(body) > NOTICE_BODY_MAX_LENGTH:
        return False
    squeezed = _MARKER_SPACE_RE.sub("", body)
    if any(marker in squeezed for marker in MEETING_HELD_MARKERS):
        # 開議・出席議員などがあるなら、短くても会議の記録である。
        # 「会議録」という語そのものは案内文にも出るので、ここでは見ない。
        return False
    if any(marker in body for marker in PDF_NOTICE_MARKERS):
        return True
    return any(marker in body for marker in APPENDIX_ONLY_MARKERS)


def looks_like_bill_or_material(text: str) -> bool:
    head = _head_text(text, limit=30)
    return sum(1 for marker in BILL_OR_MATERIAL_MARKERS if marker in head) >= 1


# PDF の OCR が段組を横に読むと、同じ塊が続けて並ぶ。釧路町では
# 「釧路町議会臨時会会議録」が 4 回繋がった題名が 7 件公開されていた。
# 原文は残し、公開する題名だけ 1 回へ畳む。
def collapse_repeated_run(text: str) -> str:
    cleaned = normalize_space(text)
    length = len(cleaned)
    if length < 6:
        return cleaned
    for unit in range(1, length // 2 + 1):
        if length % unit != 0:
            continue
        head = cleaned[:unit]
        if head * (length // unit) == cleaned and length // unit >= 2:
            return head
    return cleaned


def extract_meeting_title_from_text(text: str) -> str | None:
    """本文先頭が名乗っている会議名を返す。リンク文言よりこちらが会議の名前。"""
    for line in _head_lines(text, limit=20):
        if any(bad in line for bad in ("目次", "表紙", "索引", "出典:", "Source URL")):
            continue
        if not any(hint in line for hint in MINUTES_TITLE_HINTS):
            continue
        cleaned = strip_pdf_notes(line)
        cleaned = re.sub(r"^[\s\-‐－—・]+", "", cleaned).strip()
        cleaned = collapse_repeated_run(cleaned)
        if 8 <= len(cleaned) <= 140:
            return cleaned
    return None


def minutes_display_title(
    link_title: str,
    text: str = "",
    *,
    url: str = "",
    year_label: str = "",
) -> str:
    cleaned = strip_pdf_notes(link_title)
    if cleaned and not link_title_is_weak(cleaned):
        return cleaned
    extracted = extract_meeting_title_from_text(text)
    if extracted:
        label = normalize_space(year_label)
        if (
            label
            and label != "不明"
            and label not in extracted
            and not extracted.startswith(("昭和", "平成", "令和"))
        ):
            extracted = f"{label}{extracted}"
        return extracted
    if url:
        stem = urlsplit(url).path.rsplit("/", 1)[-1]
        stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
        stem = stem.replace("_", " ").strip()
        extracted = extract_meeting_title_from_text(stem)
        if extracted:
            return extracted
    return cleaned or normalize_space(link_title)


def non_minutes_reason(title: str, text: str = "", *, url: str = "") -> str | None:
    """会議録として公開してはいけないとき、理由コードを返す。会議録なら None。

    索引の `classify_doc_type` からも同じ関数を呼ぶこと。スクレイパだけ直すと
    既に保存された議案 PDF が会議録のまま残る。
    """
    display = minutes_display_title(title, text, url=url)
    label_reason = _label_reason(display) or _label_reason(title)
    if label_reason:
        return label_reason
    # 本文が案内文だけのときは、そこに「会議録」と書いてあっても会議の記録ではない。
    # 下の「名乗っているなら会議録」より先に見る。
    if body_is_only_a_pdf_notice(text):
        return "pdf_notice_only"
    if body_is_unreadable_glyph_names(text):
        return "unreadable_glyph_names"
    if looks_like_minutes_title(display) or looks_like_minutes_title(
        extract_meeting_title_from_text(text) or ""
    ):
        # 本文が「○○委員会記録」と名乗っているなら、それは会議録である。
        # 議案を引用していても議案本文ではない。ここで打ち切らないと、
        # 議案第○号に触れた委員会記録がまるごと落ちる。
        return None
    body = str(text or "").strip()
    if body == "":
        # 本文が無い段階では、題名だけでは切れないもの（数字のみ・「開議」）を残す。
        return None
    markers = minutes_marker_count(body)
    if markers >= 2:
        return None
    if markers >= 1 and looks_like_minutes_title(display):
        return None
    if looks_like_bill_or_material(body):
        return "non_minutes_body"
    if markers == 0 and (
        DIGIT_ONLY_RE.fullmatch(to_ascii_digits(strip_pdf_notes(display)))
        or link_title_is_weak(display)
    ):
        # 題名が「61」のままで会議録の手がかりも無い。議案本文がこの形。
        return "non_minutes_body"
    if markers == 0 and len(body) < 400 and COVER_IN_TITLE_RE.search(display + body):
        return "cover_only"
    return None


@dataclass(frozen=True)
class MinutesAdoption:
    accepted: bool
    reason: str | None
    title: str
    held_on: str | None


def adopt_minutes_document(
    link_title: str,
    text: str,
    *,
    url: str = "",
    year_label: str = "",
    source_year: int | None = None,
    today: date | None = None,
) -> MinutesAdoption:
    """PDF を会議録として採用するか、採用直前の題名と本文で決める。"""
    display = minutes_display_title(link_title, text, url=url, year_label=year_label)
    # 題名が議会だより・表紙なら、本文に会議録という言葉があっても会議録ではない。
    reason = non_minutes_reason(link_title, text, url=url)
    if reason:
        return MinutesAdoption(False, reason, display, None)
    held_on = extract_plausible_held_on(
        text,
        title=display,
        year_label=year_label,
        source_year=source_year,
        filename=url,
        today=today,
    )
    return MinutesAdoption(True, None, display, held_on)


def _safe_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _weekday_matches(year: int, month: int, day: int, weekday: str | None) -> bool | None:
    if not weekday:
        return None
    expected = WEEKDAY_INDEX.get(weekday)
    if expected is None:
        return None
    try:
        return date(year, month, day).weekday() == expected
    except ValueError:
        return False


@dataclass(frozen=True)
class _DateCandidate:
    year: int
    month: int
    day: int
    weekday: str | None
    source: str


def _candidates_from_text(raw_text: str) -> list[_DateCandidate]:
    text = normalize_compatibility_forms(raw_text)
    found: list[_DateCandidate] = []
    seen: set[tuple[int, int, int, str]] = set()

    def add(year: int | None, month: int, day: int, weekday: str | None, source: str) -> None:
        if year is None:
            return
        key = (year, month, day, source)
        if key in seen:
            return
        seen.add(key)
        found.append(_DateCandidate(year, month, day, weekday, source))

    # 表紙・目次が長い取得元があるので、行数ではなく文字数で頭を切る。
    # 20 行で切ると、目次のあとに開議行が来る本文で日付が読めなかった。
    haystack = normalize_compatibility_forms(str(text or "")[:6000])
    for match in ERA_DATE_RE.finditer(haystack):
        add(
            era_to_gregorian(match.group(1), match.group(2)),
            int(to_ascii_digits(match.group(3))),
            int(to_ascii_digits(match.group(4))),
            match.group(5),
            "era",
        )
    for match in WESTERN_DATE_RE.finditer(haystack):
        add(
            int(match.group(1)),
            int(to_ascii_digits(match.group(2))),
            int(to_ascii_digits(match.group(3))),
            match.group(4),
            "western",
        )
    for match in ISO_DATE_RE.finditer(haystack):
        add(int(match.group(1)), int(match.group(2)), int(match.group(3)), None, "iso")
    return found


def _candidates_from_filename(filename: str) -> list[_DateCandidate]:
    text = str(filename or "")
    found: list[_DateCandidate] = []
    for match in ERA_FILE_DATE_RE.finditer(text):
        base = FILE_ERA_BASE_YEAR.get(match.group(1).upper())
        if base is None:
            continue
        year = base + int(match.group(2))
        found.append(
            _DateCandidate(year, int(match.group(3)), int(match.group(4)), None, "filename")
        )
    if found:
        return found
    for match in ERA_FILE_COMPACT_RE.finditer(text):
        base = FILE_ERA_BASE_YEAR.get(match.group(1).upper())
        if base is None:
            continue
        year = base + int(match.group(2))
        found.append(
            _DateCandidate(year, int(match.group(3)), int(match.group(4)), None, "filename")
        )
    if found:
        return found
    for match in WESTERN_FILE_DATE_RE.finditer(text):
        month = int(match.group(2))
        day = int(match.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        found.append(
            _DateCandidate(int(match.group(1)), month, day, None, "filename")
        )
    return found


# 題名やファイル名に同じ月日が書かれているかを見る。`6月10日` も `0610` も
# 同じ日を指す。どちらの形でも当たるようにする。
def month_day_in_text(text: str, month: int, day: int) -> bool:
    haystack = to_ascii_digits(normalize_compatibility_forms(text or ""))
    if haystack == "":
        return False
    patterns = (
        rf"(?<![0-9]){month}\s*月\s*{day}\s*日",
        rf"(?<![0-9]){month:02d}\s*月\s*{day:02d}\s*日",
        rf"(?<![0-9]){month:02d}{day:02d}(?![0-9])",
    )
    return any(re.search(pattern, haystack) for pattern in patterns)


# 日付のすぐあとに「開議」「開会」が続く行を探す。会議録はその形で始まる。
# 題名とファイル名は会期の初日を指したままのことがあるので、実際に開いた日は
# 本文のこの行から取る。
_OPENING_MARKERS = ("開議", "開会", "開　議", "開　会", "出席議員", "出席委員")


def _month_days_before_opening(text: str) -> set[tuple[int, int]]:
    # 表紙・目次が長い取得元があるので、行数ではなく文字数で頭を切る。
    head = normalize_compatibility_forms(str(text or "")[:6000])
    found: set[tuple[int, int]] = set()
    for pattern in (ERA_DATE_RE, WESTERN_DATE_RE):
        for match in pattern.finditer(head):
            # 「招集告示 令和6年5月27日」のように、会議の日ではない日付が
            # 開議行の直前に並ぶことがある。手前に告示・招集の語があるなら見ない。
            lead = head[max(0, match.start() - 24) : match.start()]
            if any(word in lead for word in ("招集", "告示", "公示", "通知")):
                continue
            tail = head[match.end() : match.end() + 60]
            squeezed = _MARKER_SPACE_RE.sub("", tail)
            # 「閉議」「散会」が続く日付は、その会議が終わった日である。
            # 連日開催の本文には前日の閉議と当日の開議が並ぶ（前日を採っていた）。
            if any(word in squeezed[:12] for word in ("閉議", "閉会", "散会")):
                continue
            if not any(marker.replace(" ", "").replace("　", "") in squeezed for marker in _OPENING_MARKERS):
                continue
            if pattern is ERA_DATE_RE:
                month = int(to_ascii_digits(match.group(3)))
                day = int(to_ascii_digits(match.group(4)))
            else:
                month = int(to_ascii_digits(match.group(2)))
                day = int(to_ascii_digits(match.group(3)))
            if 1 <= month <= 12 and 1 <= day <= 31:
                found.add((month, day))
    return found


def extract_plausible_held_on(
    text: str,
    *,
    title: str = "",
    year_label: str = "",
    source_year: int | None = None,
    filename: str = "",
    today: date | None = None,
) -> str | None:
    """本文の和暦日付を開催日にする。曜日と年ラベルで OCR 誤読を落とす。

    先頭の「令和９年」は「６／７」の誤読であることがある。年ラベルやファイル名の
    R6/R7 より OCR が勝つと、まだ開かれていない日が新しい順の先頭に来る。
    """
    expected_year = gregorian_year_from_label(year_label, source_year)
    today = today or date.today()
    haystack = "\n".join(
        # 表紙・目次が長い取得元があるので、行数ではなく文字数で頭を切る。
        part for part in (title, str(text or "")[:6000], filename) if part
    )
    candidates = _candidates_from_text(haystack)
    filename_candidates = _candidates_from_filename(filename)
    candidates.extend(filename_candidates)
    # ファイル名（URL を含む）が指す月日。題名と一致するなら、それが会議の日である。
    filename_month_days = {(c.month, c.day) for c in filename_candidates}
    opening_month_days = _month_days_before_opening(text)

    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        year = candidate.year
        weekday_ok = _weekday_matches(year, candidate.month, candidate.day, candidate.weekday)
        if weekday_ok is False:
            # OCR 年が合わないとき、年ラベル側の年で同じ月日・曜日になるならそちらを使う。
            if expected_year is not None:
                alt_ok = _weekday_matches(
                    expected_year, candidate.month, candidate.day, candidate.weekday
                )
                if alt_ok:
                    year = expected_year
                    weekday_ok = True
                else:
                    continue
            else:
                continue
        iso = _safe_iso(year, candidate.month, candidate.day)
        if iso is None:
            continue
        value = date(year, candidate.month, candidate.day)
        if value > today:
            continue
        if expected_year is not None and abs(year - expected_year) >= YEAR_LABEL_GAP:
            continue
        score = 0
        if weekday_ok:
            score += 8
        if expected_year is not None and year == expected_year:
            score += 6
        if candidate.source == "filename":
            score += 5
        if candidate.source == "era":
            score += 2
        # 題名とファイル名（URL を含む）が同じ月日を指しているなら、それが会議の日
        # である。本文の先頭には招集告示の日など別の日付が載ることがあり、
        # 曜日まで揃っているとそちらが勝ってしまう（出雲市の `fileName=H250610A`
        # と題名「第5号 6月10日」に対し、本文の 5月27日 が採られていた）。
        if (
            month_day_in_text(title, candidate.month, candidate.day)
            and (candidate.month, candidate.day) in filename_month_days
        ):
            score += 14
        # 日付のすぐあとに「開議」「開会」が続く行は、その会議が開かれた日である。
        # 題名とファイル名は会期の初日を指したままのことがあり（`第5号` なのに
        # `6月10日開会`）、一致だけで決めると実際の開議日を捨てる。
        if (candidate.month, candidate.day) in opening_month_days:
            score += 18
        scored.append((score, iso))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def held_on_header_lines(held_on: str | None) -> list[str]:
    value = str(held_on or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return []
    return [f"開催日: {value}", f"Held-On: {value}"]
