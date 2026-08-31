"""会議録として公開してよいかを、題名と本文から判定する。

独自スクレイパは議会サイト上の PDF を選別せず拾う。議案・資料・広報・表紙が
会議録の席を占めると、取れていないのではなく別の文書が正しい名前で出る。
判定をスクレイパに閉じると既に索引された分は残るので、索引側の
`classify_doc_type` からも呼べる純粋関数として置く。
"""

from __future__ import annotations

import html
import re
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
REIWA_FILE_DATE_RE = re.compile(
    r"(?:^|[=_\-/])R(\d{1,2})[_\-](\d{2})[_\-](\d{2})(?:[_\-.]|$)",
    re.IGNORECASE,
)
REIWA_FILE_COMPACT_RE = re.compile(
    r"(?:^|[=_\-/])R(\d{2})(\d{2})(\d{2})(?:[A-Za-z_\-.]|$)",
    re.IGNORECASE,
)


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


def link_title_is_weak(title: str) -> bool:
    """リンク文言だけでは会議を名乗れないとき、本文先頭から題名を読む。"""
    cleaned = strip_pdf_notes(title)
    if cleaned == "":
        return True
    if WEAK_TITLE_RE.fullmatch(cleaned):
        return True
    if DIGIT_ONLY_RE.fullmatch(to_ascii_digits(cleaned).replace(" ", "")):
        return True
    return len(cleaned) <= 2


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
    if SKIP_LABEL_RE.match(cleaned):
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


def looks_like_bill_or_material(text: str) -> bool:
    head = _head_text(text, limit=30)
    return sum(1 for marker in BILL_OR_MATERIAL_MARKERS if marker in head) >= 1


def extract_meeting_title_from_text(text: str) -> str | None:
    """本文先頭が名乗っている会議名を返す。リンク文言よりこちらが会議の名前。"""
    for line in _head_lines(text, limit=20):
        if any(bad in line for bad in ("目次", "表紙", "索引", "出典:", "Source URL")):
            continue
        if not any(hint in line for hint in MINUTES_TITLE_HINTS):
            continue
        cleaned = strip_pdf_notes(line)
        cleaned = re.sub(r"^[\s\-‐－—・]+", "", cleaned).strip()
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


def _candidates_from_text(text: str) -> list[_DateCandidate]:
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

    haystack = _head_text(text, limit=20)
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
    for match in REIWA_FILE_DATE_RE.finditer(text):
        year = ERA_BASE_YEAR["令和"] + int(match.group(1))
        found.append(
            _DateCandidate(year, int(match.group(2)), int(match.group(3)), None, "filename")
        )
    if found:
        return found
    for match in REIWA_FILE_COMPACT_RE.finditer(text):
        year = ERA_BASE_YEAR["令和"] + int(match.group(1))
        found.append(
            _DateCandidate(year, int(match.group(2)), int(match.group(3)), None, "filename")
        )
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
        part for part in (title, _head_text(text, limit=20), filename) if part
    )
    candidates = _candidates_from_text(haystack)
    candidates.extend(_candidates_from_filename(filename))

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
