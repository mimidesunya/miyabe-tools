"""スクレイパ成果物を OpenSearch 登録用レコードへ変換する。

index builder が保存レイアウトごとの癖を直接知らなくて済むようにする層。
会議録・例規集の source ファイルを読み、タイトルや日付を正規化し、
build_opensearch_index.py が投入する tokenizer 用フィールドを準備する。
"""

from __future__ import annotations

import gzip
import html
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit, urlunsplit

try:
    import japanese_search_tokenizer  # type: ignore
except Exception:  # pragma: no cover
    japanese_search_tokenizer = None


TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "cp932", "shift_jis", "euc_jp")
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
ERA_MAX_YEAR = {"昭和": 64, "平成": 31, "令和": 99}
# 取得元は「令和４年（2022年）３月11日」と西暦を併記することがある。
# 「年」の直後に月を求めると、その括弧で切れて開催日が読めない。
_WESTERN_IN_PARENS = r"(?:\s*[（(]\s*(?:19|20)[\d０-９]{2}\s*年?\s*[）)])?"
MINUTES_DATE_PATTERN = re.compile(
    r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?"
    + _WESTERN_IN_PARENS
    + r"\s*([\d０-９]{1,2})月\s*([\d０-９]{1,2})日"
)
YEAR_LABEL_PATTERN = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?")
# 題名の日付はゼロ埋め 2 桁とは限らない。`第3日目 3月 5日` のように 1 桁で
# 空白が入る取得元がある（宇都宮市の空開催日 771 件）。全角数字も来る。
# 題名に月日があって開催日が空の文書は全国 44,581 件あった。
FILE_DATE_PATTERN = re.compile(
    r"([0-9０-９]{1,2})\s*月\s*([0-9０-９]{1,2})\s*日"
)
REIKI_DATE_PATTERN = re.compile(r'<div class="law-date">.*?\((\d{4}-\d{2}-\d{2})\)</div>', re.IGNORECASE | re.DOTALL)
REIKI_TITLE_PATTERN = re.compile(r'<div class="law-title">([^<]+)</div>', re.IGNORECASE)
REIKI_NUMBER_PATTERN = re.compile(r'<div class="law-number">([^<]+)</div>', re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"[ \t\u3000]+")
LINEBREAK_PATTERN = re.compile(r"\n{3,}")
SOURCE_URL_HEADER_PATTERN = re.compile(
    r"^\s*(?:Source URL|出典|原典URL?|原サイト|URL)\s*[:：]\s*(https?://\S+)",
    re.IGNORECASE,
)
TRAILING_SOURCE_URL_CHARS = " \t\r\n、。)]）>\"'"
D1_OPENSEARCH_INIT_PATH_RE = re.compile(r"/opensearch/sr[a-z0-9]+/init$", re.IGNORECASE)
TAIKEI_LIKE_SYSTEMS = {"taikei", "g-reiki"}


@dataclass(frozen=True)
class MinutesSourceMeta:
    title: str
    year_label: str
    meeting_name_hint: str | None
    source_url: str
    source_year: int | None
    source_fino: int | None


@dataclass(frozen=True)
class MinuteRecord:
    rel_path: str
    title: str
    meeting_name: str | None
    year_label: str
    held_on: str | None
    gregorian_year: int | None
    month: int | None
    day: int | None
    doc_type: str
    ext: str
    source_fino: int | None
    source_year: int | None
    source_url: str | None
    content: str
    title_terms: str
    meeting_name_terms: str
    content_terms: str
    indexed_at: str


def read_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix.lower() == ".gz":
        return gzip.decompress(raw)
    return raw


def read_text_auto(path: Path) -> str:
    raw = read_bytes(path)
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text_auto(path))
    except Exception as exc:
        if strict:
            raise ValueError(f"failed to load JSON path={path}: {exc}") from exc
        return default


def logical_path(path: Path) -> Path:
    return path.with_suffix("") if path.suffix.lower() == ".gz" else path


def logical_suffix(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    return suffixes[-1] if suffixes else ""


def existing_path(path: Path) -> Path | None:
    candidates = [path]
    if path.suffix.lower() != ".gz":
        candidates.insert(0, path.with_name(path.name + ".gz"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def normalize_space(value: str) -> str:
    return SPACE_PATTERN.sub(" ", value).strip()


def clean_source_url(value: object) -> str:
    text = html.unescape(str(value or "")).strip().strip(TRAILING_SOURCE_URL_CHARS)
    if text == "":
        return ""
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or parts.netloc == "":
        return ""
    return text


def extract_source_url_from_text(text: str, *, line_limit: int = 50) -> str:
    head = "\n".join(text.splitlines()[:line_limit])
    for line in head.splitlines():
        match = SOURCE_URL_HEADER_PATTERN.search(line)
        if not match:
            continue
        source_url = clean_source_url(match.group(1))
        if source_url:
            return source_url
    return ""


def terms_text(value: str) -> str:
    if value == "":
        return ""
    if japanese_search_tokenizer is not None:
        try:
            return str(japanese_search_tokenizer.document_terms_text(value)).strip()
        except Exception:
            pass
    return " ".join(part for part in re.split(r"[\s\u3000]+", value) if part)


def html_to_text(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", value, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|tr|table|section|article|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = TAG_PATTERN.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINEBREAK_PATTERN.sub("\n\n", text)
    return text.strip()


def markdown_to_text(value: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>*`\-\+\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINEBREAK_PATTERN.sub("\n\n", text)
    return text.strip()


def minutes_source_key(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if relative.suffix.lower() == ".gz":
        relative = relative.with_suffix("")
    return relative.with_suffix("").as_posix()


def choose_minutes_source_files(downloads_dir: Path) -> list[Path]:
    preferred: dict[str, Path] = {}
    for file_path in sorted(downloads_dir.rglob("*")):
        if not file_path.is_file():
            continue
        ext = logical_suffix(file_path)
        if ext not in {".txt", ".html", ".htm"}:
            continue
        rel_stem = minutes_source_key(file_path, downloads_dir)
        current = preferred.get(rel_stem)
        if current is None or (logical_suffix(current) != ".txt" and ext == ".txt"):
            preferred[rel_stem] = file_path
    return sorted(preferred.values())


# 同じ保存名が 2 件目以降にぶつかると、保存側が SHA1 先頭 8 桁を足す
# （`gijiroku_storage.disambiguated_stem()`）。それは保存先を分けるための印で、
# 会議の名前ではない。付いたまま一覧行と照合すると当たらず、原典 URL も
# 開催日も落ちる。本番で suffix 付き 45,731 件のうち 10,696 件がこの形だった。
DISAMBIGUATION_SUFFIX_PATTERN = re.compile(r"-[0-9a-f]{8}$")


def strip_disambiguation_suffix(title: str) -> str:
    stripped = DISAMBIGUATION_SUFFIX_PATTERN.sub("", str(title or "").strip())
    # 題名が印だけになるなら落とさない。名前が消える方が困る。
    return stripped or str(title or "").strip()


def normalize_title(file_path: Path) -> str:
    logical = logical_path(file_path)
    stem = logical.stem.strip() or logical.name
    return strip_disambiguation_suffix(stem)


def decode_query_component(value: str) -> str:
    if value == "":
        return ""
    try:
        raw = unquote_to_bytes(value)
    except Exception:
        return ""
    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return normalize_space(raw.decode(encoding))
        except Exception:
            continue
    return normalize_space(raw.decode("utf-8", errors="ignore"))


def raw_query_values(url: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for part in urlsplit(url).query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        values.setdefault(key, []).append(value)
    return values


def to_ascii_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value.strip())
    if raw == "元":
        return 1
    return int(raw) if raw.isdigit() else None


def collapse_repeated_era_year(value: str) -> str:
    if len(value) <= 2:
        return value
    for width in range(len(value) // 2, 0, -1):
        if len(value) % width != 0:
            continue
        unit = value[:width]
        if unit * (len(value) // width) == value and unit.isdigit():
            return unit
    return value


def era_to_gregorian(era: str, year_text: str) -> int | None:
    max_year = ERA_MAX_YEAR.get(era)
    normalized_year_text = to_ascii_digits(year_text.strip())
    if max_year is not None and normalized_year_text.isdigit():
        normalized_year_text = collapse_repeated_era_year(normalized_year_text)
    era_year = japanese_year_to_int(normalized_year_text)
    if era_year is None:
        return None
    base_year = ERA_BASE_YEAR.get(era)
    if base_year is None or (max_year is not None and era_year > max_year):
        return None
    return base_year + era_year


def parse_optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def first_nonempty_lines(text: str, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        clean = normalize_space(line)
        if clean:
            lines.append(clean)
        if len(lines) >= limit:
            break
    return lines


def joined_head_text(text: str, limit: int = 8) -> str:
    return "\n".join(first_nonempty_lines(text, limit=limit))


def extract_year_label(text: str, fallback: str | None = None) -> str | None:
    match = YEAR_LABEL_PATTERN.search(joined_head_text(text, limit=6))
    if not match:
        return fallback
    label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
    if match.group(3):
        label += f"・{match.group(3)}元年"
    return label


def canonical_year_label(value: str | None) -> str:
    """照合用の年ラベル。空白と `_` を落として比べる。

    保存先のディレクトリ名は、一覧行の年ラベルから作られるが、改行や空白が
    `_` に置き換わる。`平成31年・令和元年` が `平成31年・_令和元年` になり、
    そのままでは一覧行に当たらない。"""
    return re.sub(r"[\s_]+", "", str(value or ""))


# OCR が段組を横に読むと、年ラベルも `平成28282828年` のように繰り返しになる。
# 平川市で 20 件あった。日付順には載るが、年ラベルでの検索と集計が壊れる。
#
# **`平成11年` を `平成1年` に畳んではいけない。**元号の年は 2 桁までありうるので、
# 桁数が 2 桁以内のものは繰り返しに見えても実在の年である。畳むのは、
# 元号の最大年（昭和64・平成31・令和は当面 99）を超える桁数のときだけにする。
ERA_MAX_YEAR_FOR_LABEL = {"昭和": 64, "平成": 31, "令和": 99}


def collapse_repeated_year_label(value: str) -> str:
    text = normalize_space(value)
    match = re.fullmatch(r"(昭和|平成|令和)([0-9０-９]+)年", text)
    if not match:
        return text
    era = match.group(1)
    digits = to_ascii_digits(match.group(2))
    if len(digits) <= 2:
        # 実在しうる年。畳まない。
        return text
    for unit in (1, 2):
        if len(digits) % unit != 0:
            continue
        head = digits[:unit]
        if head * (len(digits) // unit) != digits:
            continue
        year = int(head)
        if 1 <= year <= ERA_MAX_YEAR_FOR_LABEL.get(era, 99):
            return f"{era}{head}年"
    return text


def normalize_year_label_candidate(value: str) -> str | None:
    match = YEAR_LABEL_PATTERN.fullmatch(normalize_space(value))
    if not match:
        match = YEAR_LABEL_PATTERN.fullmatch(canonical_year_label(value))
    if not match:
        return None
    label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
    if match.group(3):
        label += f"・{match.group(3)}元年"
    return label


def warn_invalid_minutes_date(candidate: str, *, year: int | None, month: int, day: int, source: str) -> None:
    print(
        "[WARN] invalid minutes date skipped "
        f"source={source} candidate={candidate!r} year={year!r} month={month} day={day}",
        file=sys.stderr,
        flush=True,
    )


def accept_minutes_date(
    candidate: str,
    year: int,
    month: int,
    day: int,
    source: str,
    today: date | None = None,
) -> tuple[str, int, int, int] | None:
    """会議録の開催日として妥当なときだけ採用する。"""
    try:
        value = date(year, month, day)
    except ValueError:
        warn_invalid_minutes_date(candidate, year=year, month=month, day=day, source=source)
        return None
    # 会議録は開催後に公開されるので、未来の日付は抽出の誤り。
    # そのまま入れると新しい順の先頭に居座り続ける。
    if value > (today or date.today()):
        print(
            f"[WARN] future minutes date skipped candidate={candidate!r} "
            f"date={value.isoformat()} source={source}",
            file=sys.stderr,
        )
        return None
    return value.isoformat(), year, month, day


# 曜日と年ラベルで開催日を検算する。取得側と同じ判定を借りる。
# PDF の OCR が「６／７」を「９」と読むと、年ラベルが令和6年なのに開催日が
# 2027 年になる（田川市）。本文の曜日はその年と合わないので、そこで落とせる。
# 年ラベルから西暦を読む。会議の年が分かっているなら、本文中の別の日付を
# 開催日として採らないための当たりに使う。
# 年ラベルとの差をこれ以上離すと、本文が引用している別の年を拾っている。
# 取得側（`minutes_kind`）と同じ値・同じ比較にする。**年度会期は 1 年ずれる**
# ので、1 に締めてはいけない（出雲市の令和6年度第6回定例会は 2025-02-20）。
YEAR_LABEL_GAP = 2


def normalize_compatibility_forms(value: str) -> str:
    """康煕部首などの互換字を普通の字へ寄せる。取得側と同じ扱いにする。"""
    try:
        from tools.gijiroku import minutes_kind
    except Exception:
        return str(value or "")
    return minutes_kind.normalize_compatibility_forms(value)


def year_from_label(year_label: str) -> int | None:
    match = YEAR_LABEL_PATTERN.search(normalize_space(year_label))
    if not match:
        return None
    return era_to_gregorian(match.group(1), match.group(2))


def _plausible_held_on(
    text: str,
    title: str,
    year_label: str,
    source_year: int | None,
    source_hint: str,
) -> str | None:
    try:
        from tools.gijiroku import minutes_kind
    except Exception:
        return None
    try:
        return minutes_kind.extract_plausible_held_on(
            text,
            title=title,
            year_label=year_label,
            source_year=source_year,
            filename=source_hint,
        )
    except Exception as exc:
        print(f"[WARN] held-on sanity check failed source={source_hint}: {exc}", file=sys.stderr)
        return None


def extract_held_on(
    text: str,
    title: str,
    source_year: int | None,
    *,
    source_hint: str = "",
    year_label: str = "",
) -> tuple[str | None, int | None, int | None, int | None]:
    source_label = source_hint or title
    explicit_match = re.search(r"(?im)^Held-On:\s*(\d{4})-(\d{2})-(\d{2})\s*$", text)
    if explicit_match:
        accepted = accept_minutes_date(
            explicit_match.group(0),
            int(explicit_match.group(1)),
            int(explicit_match.group(2)),
            int(explicit_match.group(3)),
            source_label,
        )
        if accepted is not None:
            return accepted
    # 明示の Held-On が無いときは、曜日と年ラベルで検算した候補を先に使う。
    # 本文先頭の最初の和暦をそのまま採ると、OCR の誤読が未来日になる。
    plausible = _plausible_held_on(text, title, year_label, source_year, source_label)
    if plausible:
        accepted = accept_minutes_date(
            plausible,
            int(plausible[0:4]),
            int(plausible[5:7]),
            int(plausible[8:10]),
            source_label,
        )
        if accepted is not None:
            return accepted
    label_year = year_from_label(year_label)
    # 康煕部首（`12⽉4⽇`）は日付の正規表現に当たらない。読む直前に寄せる。
    head_text = normalize_compatibility_forms(joined_head_text(text, limit=20))
    for match in MINUTES_DATE_PATTERN.finditer(head_text):
        gregorian_year = era_to_gregorian(match.group(1), match.group(2))
        if gregorian_year is None:
            continue
        # 年ラベルと大きく離れた年は、本文中の別の日付を拾っている。
        # 「平成16年」の委員会記録に 1932-10-01 が入っていた（本番で 10 件）。
        # 判定は取得側と揃える。片方が `>=`、片方が `>` だと、2 年差の引用が
        # 後段だけ通る（多度津・周防大島の 2 件が実際にそうだった）。
        if label_year is not None and abs(gregorian_year - label_year) >= YEAR_LABEL_GAP:
            continue
        accepted = accept_minutes_date(
            match.group(0),
            gregorian_year,
            int(to_ascii_digits(match.group(4))),
            int(to_ascii_digits(match.group(5))),
            source_label,
        )
        if accepted is not None:
            return accepted
    match = FILE_DATE_PATTERN.search(title)
    # 年は一覧行の `source_year` が正だが、無いなら年ラベルから読む。
    # 年が分からないだけで題名の月日を捨てていた。
    title_year = source_year if source_year is not None else label_year
    if match and title_year is not None:
        accepted = accept_minutes_date(
            match.group(0),
            title_year,
            int(to_ascii_digits(match.group(1))),
            int(to_ascii_digits(match.group(2))),
            source_label,
        )
        if accepted is not None:
            return accepted
    return None, source_year, None, None


def extract_meeting_name(text: str) -> str | None:
    lines = first_nonempty_lines(text, limit=5)
    if len(lines) >= 2:
        second = lines[1]
        if "－" not in second and len(second) >= 4:
            return second
    if lines:
        first = re.sub(r"－[^－]+$", "", lines[0]).strip()
        if len(first) >= 4:
            return first
    return None


def looks_like_minutes_listing_page(text: str) -> bool:
    head = joined_head_text(text, limit=12)
    markers = (
        "会議日程一覧",
        "会議検索結果一覧",
        "件の日程がヒットしました",
        "をクリックすると発言者を表示します",
    )
    matched = sum(1 for marker in markers if marker in head)
    return matched >= 2 or ("会議日程一覧" in head and re.search(r"\d+件の日程がヒットしました", head) is not None)


def trim_meta_meeting_name(label: str, title: str) -> str:
    trimmed = normalize_space(label)
    trimmed = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", trimmed)
    trimmed = re.sub(rf"[｜|－-]\s*{re.escape(normalize_space(title))}$", "", trimmed).strip()
    return normalize_space(trimmed)


def extract_meta_meeting_name(source_url: str, title: str) -> str | None:
    query = raw_query_values(source_url)
    title_hint = decode_query_component((query.get("TITL") or [""])[0])
    title_subt = decode_query_component((query.get("TITL_SUBT") or [""])[0])
    candidates = [value for value in [title_hint, trim_meta_meeting_name(title_subt, title) if title_subt else ""] if value]
    for candidate in candidates:
        if candidate != normalize_space(title):
            return candidate
    return None


def minutes_source_numbers_from_url(source_url: str) -> tuple[int | None, int | None]:
    query = parse_qs(urlsplit(source_url).query)
    source_year = parse_optional_int((query.get("YEAR") or query.get("year") or [None])[0])
    source_fino = parse_optional_int((query.get("FINO") or query.get("fino") or [None])[0])
    return source_year, source_fino


# 目次だけの文書は短い（実測で最大 24,000 文字弱）。一方、本文の冒頭に
# 目次を載せる会議録があり、そちらは数万〜数十万文字になる。本文中の
# 手がかりだけで目次と決めると、本文まるごと検索対象から外れてしまう。
TOC_TEXT_MAX_LENGTH = 30_000


# 会議録かどうかの判定は取得側と索引側で必ず同じものを使う。別々に持つと、
# 片方だけ直したときに食い違う。取得側が正本なので、そこから借りる。
def _non_minutes_reason(title: str, text: str) -> str | None:
    try:
        from tools.gijiroku import minutes_kind
    except Exception:
        # 取得側を読み込めない環境（索引だけを切り出して動かす場合）では、
        # 判定を諦めて従来どおり会議録として扱う。黙って全部落とす方が危ない。
        return None
    try:
        return minutes_kind.non_minutes_reason(title, text)
    except Exception as exc:
        print(f"[WARN] non-minutes check failed title={title!r}: {exc}", file=sys.stderr)
        return None


def classify_doc_type(title: str, text: str, *, ext: str = "") -> str:
    if normalize_space(title).endswith("目次"):
        return "toc"
    if "会議録目次" in joined_head_text(text, limit=6) and len(text) <= TOC_TEXT_MAX_LENGTH:
        return "toc"
    if ext.lower() in {".html", ".htm"} and looks_like_minutes_listing_page(text):
        return "aux"
    # 議案・資料・広報・表紙を会議録として公開しない。スクレイパ側だけを直しても、
    # 既にディスクにある PDF は次の索引更新でまた会議録として出る。同じ判定を
    # ここでも通す（飯塚市の「案件1」、長与町の題名「61」など）。
    if _non_minutes_reason(title, text):
        return "aux"
    return "minutes"


# 会議名の候補一覧を同じ辞書へ間借りさせるための印。実在しない会議名を使う。
MEETING_CANDIDATES_KEY = "﻿会議名の候補"


def meeting_dir_from_path(file_path: Path, downloads_dir: Path) -> str:
    """保存先の会議ディレクトリ名。年ディレクトリと本文ファイルの間にある。"""
    try:
        parts = file_path.relative_to(downloads_dir).parts
    except ValueError:
        return ""
    return normalize_space(parts[-2]) if len(parts) >= 3 else ""


def pick_meta_by_meeting_dir(
    meta_map: dict[tuple[str, str, str], Any],
    title: str,
    year_labels: Iterable[str | None],
    meeting_dir: str,
) -> MinutesSourceMeta | None:
    """保存先の会議ディレクトリと会議名が重なる候補を選ぶ。

    ディレクトリ名は会議名を短くしたものなので、前方一致で足りる。
    重なる候補が二つ以上あるなら選べないので、選ばない。"""
    hint = normalize_space(meeting_dir)
    if hint == "":
        return None
    # 保存先の会議ディレクトリは、同じ日に開かれた会議をまとめて
    # 「建設常任,建設協議,建設企業」のような名前になることがある。
    # そのままでは前方一致が 1 件も当たらない（八戸市で最大 9 文書が
    # 同じ原典 URL を指していた）。区切って 1 つずつ試す。
    hints = [part for part in (normalize_space(h) for h in hint.split(",")) if part]
    for label in year_labels:
        for candidate_label in year_label_variants(label):
            candidates = meta_map.get((candidate_label, title, MEETING_CANDIDATES_KEY))
            if not isinstance(candidates, list) or len(candidates) < 2:
                continue
            for part in hints:
                matched = [
                    meta
                    for meta in candidates
                    if normalize_space(meta.meeting_name_hint or "").startswith(part)
                ]
                if len(matched) == 1:
                    return matched[0]
    return None


def parse_minutes_source_meta(
    index_json: Path, *, strict: bool = False
) -> dict[tuple[str, str, str], MinutesSourceMeta]:
    rows = load_json(index_json, [], strict=strict)
    metas: dict[tuple[str, str, str], MinutesSourceMeta] = {}
    if not isinstance(rows, list):
        if strict:
            raise ValueError(f"minutes source metadata must be a list: {index_json}")
        return metas
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "")).strip()
        year_label = normalize_space(str(row.get("year_label", "")))
        source_url = str(row.get("url", "")).strip()
        if title == "" or year_label == "" or source_url == "":
            continue
        query = parse_qs(urlsplit(source_url).query)
        source_year = parse_optional_int(row.get("source_year")) or parse_optional_int((query.get("YEAR") or [None])[0])
        source_fino = parse_optional_int(row.get("source_fino")) or parse_optional_int((query.get("FINO") or [None])[0])
        meeting_name_hint = normalize_space(str(row.get("meeting_name", "") or row.get("meeting_group", ""))) or None
        if meeting_name_hint is None:
            meeting_name_hint = extract_meta_meeting_name(source_url, title)
        meta = MinutesSourceMeta(title, year_label, meeting_name_hint, source_url, source_year, source_fino)
        meeting_key = normalize_space(meeting_name_hint or "")
        for label_key in {year_label, canonical_year_label(year_label)}:
            metas.setdefault((label_key, title, meeting_key), meta)
            metas.setdefault((label_key, title, ""), meta)
            if meeting_key:
                # 同じ年・同じ題名の行が複数あると、会議名なしの鍵は最初の
                # 1 件に固定される。飯塚市では 75 文書が同じ PDF を指していた。
                # 保存先の会議ディレクトリで選び直せるよう、候補を控えておく。
                candidates = metas.setdefault(
                    (label_key, title, MEETING_CANDIDATES_KEY), []
                )
                if isinstance(candidates, list):
                    candidates.append(meta)
    return metas


def fallback_year_label_from_path(file_path: Path, downloads_dir: Path) -> str | None:
    for parent in file_path.parents:
        if parent == downloads_dir:
            break
        label = normalize_year_label_candidate(parent.name)
        if label:
            return label
    if file_path.parent != downloads_dir:
        return normalize_space(file_path.parent.name) or None
    return None


def year_label_variants(label: str | None) -> list[str]:
    """`平成31年・令和元年` のような複合ラベルを、片方ずつでも照合できるようにする。"""
    text = normalize_space(label or "")
    if text == "":
        return []
    variants = [text]
    for candidate in (canonical_year_label(text), *text.split("・")):
        candidate = normalize_space(candidate)
        if candidate and candidate not in variants:
            variants.append(candidate)
        canonical = canonical_year_label(candidate)
        if canonical and canonical not in variants:
            variants.append(canonical)
    return variants


def lookup_minutes_source_meta(
    meta_map: dict[tuple[str, str, str], MinutesSourceMeta],
    title: str,
    meeting_name: str | None,
    year_labels: Iterable[str | None],
    *,
    allow_any_meeting: bool = True,
) -> MinutesSourceMeta | None:
    meeting_key = normalize_space(meeting_name or "")
    seen: set[str] = set()
    for label in year_labels:
        for candidate in year_label_variants(label):
            if candidate in seen:
                continue
            seen.add(candidate)
            meta = meta_map.get((candidate, title, meeting_key))
            if meta is None and allow_any_meeting:
                meta = meta_map.get((candidate, title, ""))
            if isinstance(meta, MinutesSourceMeta):
                return meta
    return None


# 公開する題名。取得側と同じ判定を借りる。リンク文言が会議を名乗れないときだけ
# 本文先頭の会議名に置き換える。読めなければ保存名のまま。
def display_minutes_title(title: str, text: str) -> str:
    try:
        from tools.gijiroku import minutes_kind
    except Exception:
        return title
    try:
        display = str(minutes_kind.minutes_display_title(title, text) or "").strip()
    except Exception as exc:
        print(f"[WARN] display title failed title={title!r}: {exc}", file=sys.stderr)
        return title
    return display or title


# 取得側と同じ修復を索引でも通す。既に保存された文字化けは取り直すまで直らない。
def repair_saved_mojibake(text: str) -> str:
    try:
        from tools.gijiroku import minutes_kind
    except Exception:
        return text
    try:
        return minutes_kind.repair_cp932_mojibake(text)
    except Exception as exc:
        print(f"[WARN] mojibake repair failed: {exc}", file=sys.stderr)
        return text


def build_minutes_record(
    file_path: Path,
    downloads_dir: Path,
    meta_map: dict[tuple[str, str, str], MinutesSourceMeta],
    indexed_at: str,
) -> MinuteRecord | None:
    ext = logical_suffix(file_path)
    title = normalize_title(file_path)
    try:
        raw_text = read_text_auto(file_path)
    except Exception:
        return None
    content = html_to_text(raw_text) if ext in {".html", ".htm"} else raw_text.strip()
    # 保存済みの本文が文字化けしていることがある。PDF から取り出した Shift_JIS を
    # 1 バイト 1 文字として保存していたためで、日本語では二度と検索に当たらない。
    # 取得側は直したが、既に保存された分は取り直すまで直らないので、ここでも読み直す。
    content = repair_saved_mojibake(content)
    if content == "":
        return None
    fallback_year_label = fallback_year_label_from_path(file_path, downloads_dir)
    extracted_year_label = extract_year_label(content, fallback=fallback_year_label) or fallback_year_label or "不明"
    # OCR の繰り返し（`平成28282828年`）は年ラベルとして使えない。1 回へ畳む。
    extracted_year_label = collapse_repeated_year_label(extracted_year_label)
    meeting_name = extract_meeting_name(content)
    # 本文冒頭の年は「会期名の年」であって開催年ではない。令和3年2月の会議が
    # 「令和2年第2回定例会」と書かれていることがあり、そのまま照合すると
    # 一覧行に当たらず原典 URL も開催日も落ちる。実データで 917 件がこの形だった。
    # 保存先の年ラベルは計画時に一覧行から決めた値なので、そちらでも照合する。
    year_labels = (extracted_year_label, fallback_year_label)
    # まず本文の会議名でぴったり引く。
    meta = lookup_minutes_source_meta(
        meta_map, title, meeting_name, year_labels, allow_any_meeting=False
    )
    if meta is None:
        # 会議名で引けないときに会議名なしの鍵へ落とすと、同じ年・同じ題名の
        # 行が複数あれば最初の 1 件へ固定される。飯塚市では 75 文書が同じ PDF を
        # 原典として指していた。先に保存先の会議ディレクトリで選び直す。
        meta = pick_meta_by_meeting_dir(
            meta_map,
            title,
            year_labels,
            meeting_dir_from_path(file_path, downloads_dir),
        )
    if meta is None:
        meta = lookup_minutes_source_meta(meta_map, title, meeting_name, year_labels)
    # 本文の `出典:` は、その文書を落としたときに書いた 1 件ぶんの URL である。
    # 一覧行との照合は題名と会議名の当てもので、同じ日の分科会や委員会が
    # 同じ題名だと 1 件の URL を全部へ配ってしまう（北海道の FINO=9077 が
    # 5 会議、滋賀県の FINO=3434 が 6 会議、飯塚市の 1 PDF が 75 文書）。
    # 文書ごとに書いてある方を正とし、無いときだけ一覧行に頼る。
    body_source_url = extract_source_url_from_text(content)
    catalog_source_url = clean_source_url(meta.source_url if meta else "")
    source_url = body_source_url or catalog_source_url
    source_year = meta.source_year if meta else None
    source_fino = meta.source_fino if meta else None
    if body_source_url and catalog_source_url and body_source_url != catalog_source_url:
        # 一覧行から引き継いだ番号は、別の会議のものである。捨てて URL から読む。
        source_year = None
        source_fino = None
    if source_url:
        url_source_year, url_source_fino = minutes_source_numbers_from_url(source_url)
        source_year = source_year or url_source_year
        source_fino = source_fino or url_source_fino
    held_on, gregorian_year, month, day = extract_held_on(
        content,
        title,
        source_year,
        # 原典 URL にも開催日が入っている（`fileName=R070220A` は 2025-02-20）。
        # 保存パスだけを渡していたので、kensakusystem の 16,312 件が
        # 開催日を持てなかった。取れているのに使っていなかった。
        source_hint=" ".join(
            part
            for part in (file_path.relative_to(downloads_dir).as_posix(), source_url)
            if part
        ),
        year_label=meta.year_label if meta else (fallback_year_label or ""),
    )
    return MinuteRecord(
        rel_path=file_path.relative_to(downloads_dir).as_posix(),
        # 一覧行との照合には保存名（`title`）を使うが、公開する題名は会議の名前に
        # したい。リンク文言が「開議」「61」「18日」だけだと、検索結果に会議名が
        # 出ず、会議名で探せない（本番で題名「開議」が 23 件あった）。
        title=display_minutes_title(title, content),
        meeting_name=meeting_name,
        year_label=meta.year_label if meta else extracted_year_label,
        held_on=held_on,
        gregorian_year=gregorian_year,
        month=month,
        day=day,
        doc_type=classify_doc_type(title, content, ext=ext),
        ext=ext,
        source_fino=source_fino,
        source_year=source_year if source_year is not None else gregorian_year,
        source_url=source_url or None,
        content=content,
        title_terms=terms_text(title),
        meeting_name_terms=terms_text(meeting_name or ""),
        content_terms=terms_text(content),
        indexed_at=indexed_at,
    )


def reiki_logical_key_from_path(path: Path, root: Path) -> str:
    return logical_path(path).relative_to(root).with_suffix("").as_posix()


def reiki_logical_key_from_string(value: str) -> str:
    return logical_path(Path(str(value).replace("\\", "/"))).with_suffix("").as_posix()


def collect_reiki_preferred_files(root: Path, suffixes: set[str]) -> dict[str, Path]:
    preferred: dict[str, Path] = {}
    if not root.exists():
        return preferred
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        logical = logical_path(path)
        if logical.suffix.lower() not in suffixes:
            continue
        key = reiki_logical_key_from_path(path, root)
        current = preferred.get(key)
        if current is None or (current.suffix.lower() == ".gz" and path.suffix.lower() != ".gz"):
            preferred[key] = path
    return preferred


def load_reiki_manifest_index(
    path: Path, *, strict: bool = False
) -> dict[str, dict[str, Any]]:
    rows = load_json(path, [], strict=strict)
    if not isinstance(rows, list):
        if strict:
            raise ValueError(f"reiki source manifest must be a list: {path}")
        return {}
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_file = str(row.get("source_file") or row.get("stored_source_file") or "").strip()
        if source_file == "":
            continue
        key = reiki_logical_key_from_string(source_file)
        if key:
            index[key] = row
            index.setdefault(Path(key).name, row)
    return index


def build_alias_map(files: dict[str, Path]) -> dict[str, Path]:
    alias: dict[str, Path] = {}
    for key, path in files.items():
        alias[key] = path
        alias.setdefault(Path(key).name, path)
    return alias


def decode_html_text(value: object) -> str:
    return html.unescape(str(value or "")).strip()


def extract_title_from_html(html_content: str, fallback: str) -> str:
    match = REIKI_TITLE_PATTERN.search(html_content)
    return decode_html_text(match.group(1)) if match else fallback


def extract_number_from_html(html_content: str) -> str:
    match = REIKI_NUMBER_PATTERN.search(html_content)
    return decode_html_text(match.group(1)) if match else ""


# 古い例規は law-date に西暦が併記されず、和暦しか無い。西暦だけを見ると
# 公布日が読めず、日付の欄が空になる（そこへ取得日を入れると、昭和の条例が
# 今日の日付で最新に見えていた）。和暦からも読む。
WAREKI_ERA_BASE = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
WAREKI_DATE_PATTERN = re.compile(
    r"(明治|大正|昭和|平成|令和)\s*([0-9]{1,2})年\s*([0-9]{1,2})月\s*([0-9]{1,2})日"
)
REIKI_DATE_TEXT_PATTERN = re.compile(
    r'<div class="law-date">(.*?)</div>', re.IGNORECASE | re.DOTALL
)


def wareki_to_iso(text: str) -> str:
    """和暦を YYYY-MM-DD へ。日まで揃っていなければ空文字。"""
    if not text:
        return ""
    normalized = text.replace("元年", "1年").translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )
    match = WAREKI_DATE_PATTERN.search(normalized)
    if not match:
        return ""
    era, year, month, day = match.groups()
    try:
        return f"{WAREKI_ERA_BASE[era] + int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (KeyError, ValueError):
        return ""


def extract_date_from_html(html_content: str) -> str:
    match = REIKI_DATE_PATTERN.search(html_content)
    if match:
        return match.group(1)
    text_match = REIKI_DATE_TEXT_PATTERN.search(html_content)
    if text_match:
        return wareki_to_iso(TAG_PATTERN.sub("", text_match.group(1)))
    return ""


def join_strings(value: object) -> str:
    if isinstance(value, list):
        return ",".join(normalize_space(decode_html_text(item)) for item in value if normalize_space(decode_html_text(item)))
    return normalize_space(decode_html_text(value))


def normalize_kana(value: str, prefixes: list[str]) -> str:
    normalized = value.strip()
    for prefix in prefixes:
        if prefix and normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def safe_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def safe_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def normalize_document_type(value: str) -> str:
    text = normalize_space(value)
    return text if text in {"条例", "規則", "規程", "要綱"} else "その他"


def detect_document_type(title: str, number: str) -> str:
    for candidate in (number, title):
        if "条例" in candidate:
            return "条例"
        if "規則" in candidate:
            return "規則"
        if "規程" in candidate or "訓令" in candidate:
            return "規程"
        if "要綱" in candidate:
            return "要綱"
    return "その他"


def record_updated_at(*paths: Path | None) -> str:
    mtimes = [path.stat().st_mtime for path in paths if path is not None and path.exists()]
    if not mtimes:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(max(mtimes), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def reiki_sortable_prefixes(target: dict[str, Any]) -> list[str]:
    name_kana = normalize_space(str(target.get("name_kana", "")).strip()).replace(" ", "")
    return [name_kana] if name_kana else []


def normalize_source_file_value(value: object) -> str:
    normalized = str(value or "").replace("\\", "/").strip("/")
    if normalized.lower().endswith(".gz"):
        normalized = normalized[:-3]
    return normalized


def reiki_source_file_from_key(key: str) -> str:
    normalized = normalize_source_file_value(key)
    if normalized == "":
        return ""
    if normalized.lower().endswith((".html", ".htm")):
        return normalized
    return normalized + ".html"


def reiki_source_file_name(source_file: str) -> str:
    normalized = normalize_source_file_value(source_file)
    return normalized.rsplit("/", 1)[-1]


def reiki_source_file_stem(source_file: str) -> str:
    name = reiki_source_file_name(source_file)
    lower = name.lower()
    for suffix in (".html", ".htm"):
        if lower.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name[:-2] if name.endswith("_j") else name


def reiki_honbun_filename(source_file: str) -> str:
    stem = reiki_source_file_stem(source_file)
    return f"{stem}.html" if stem else ""


def derive_reiki_honbun_source_url(source_url: str, source_file: str) -> str:
    filename = reiki_honbun_filename(source_file)
    source_url = clean_source_url(source_url)
    if source_url == "" or filename == "":
        return ""
    parts = urlsplit(source_url)
    path = parts.path or "/"
    lower_path = path.lower()
    if "/reiki_honbun/" in lower_path:
        base_path = path[: lower_path.find("/reiki_honbun/") + 1]
    elif "/reiki_taikei/" in lower_path:
        base_path = path[: lower_path.find("/reiki_taikei/") + 1]
    elif lower_path.endswith(("/reiki_menu.html", "/reiki_menu.htm", "/index.html", "/index.htm")):
        base_path = path.rsplit("/", 1)[0] + "/"
    elif path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return clean_source_url(urlunsplit((parts.scheme or "https", parts.netloc, base_path + "reiki_honbun/" + filename, "", "")))


def d1_law_base_url(source_url: str) -> str:
    source_url = clean_source_url(source_url)
    if source_url == "":
        return ""
    parts = urlsplit(source_url)
    path = parts.path or "/"
    lower_path = path.lower()
    marker = "/d1w_reiki/"
    marker_index = lower_path.find(marker)
    if marker_index >= 0:
        base_path = path[: marker_index + len(marker)]
    elif D1_OPENSEARCH_INIT_PATH_RE.search(path or "") is not None:
        return ""
    elif lower_path.endswith(("/reiki.html", "/reiki.htm", "/reiki_menu.html", "/reiki_menu.htm", "/index.html", "/index.htm")):
        base_path = path.rsplit("/", 1)[0] + "/"
    else:
        return ""
    return clean_source_url(urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", "")))


def derive_d1_law_source_url(source_url: str, source_file: str) -> str:
    base_url = d1_law_base_url(source_url)
    code = reiki_source_file_stem(source_file)
    filename = reiki_source_file_name(source_file)
    if base_url == "" or code == "":
        return ""
    if filename == "" or not filename.lower().endswith((".html", ".htm")):
        filename = f"{code}_j.html"
    return clean_source_url(base_url.rstrip("/") + f"/{code}/{filename}")


def derive_reiki_source_url(target: dict[str, Any] | None, source_file: str) -> str:
    target = target if isinstance(target, dict) else {}
    source_file = str(source_file or "").strip()
    if source_file == "":
        return ""
    system_type = normalize_space(str(target.get("system_type", "")).strip())
    target_source_url = clean_source_url(target.get("source_url")) or clean_source_url(target.get("entry_url"))
    if target_source_url == "":
        return ""
    if system_type in TAIKEI_LIKE_SYSTEMS:
        return derive_reiki_honbun_source_url(target_source_url, source_file)
    if system_type == "d1-law":
        return derive_d1_law_source_url(target_source_url, source_file)
    return ""


def build_reiki_record(
    key: str,
    html_path: Path,
    markdown_path: Path | None,
    classification_path: Path | None,
    manifest: dict[str, Any] | None,
    prefixes: list[str],
    target: dict[str, Any] | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    html_content = read_text_auto(html_path)
    content_text = html_to_text(html_content)
    if content_text == "" and markdown_path is not None and markdown_path.exists():
        content_text = markdown_to_text(read_text_auto(markdown_path))
    if content_text == "":
        return None
    classification = (
        load_json(classification_path, {}, strict=strict)
        if classification_path is not None
        else {}
    )
    if not isinstance(classification, dict):
        if strict:
            raise ValueError(f"reiki classification must be an object: {classification_path}")
        classification = {}
    manifest = manifest if isinstance(manifest, dict) else {}
    fallback_title = normalize_space(str(manifest.get("title", "")).strip()) or Path(key).name
    title = normalize_space(decode_html_text(classification.get("title", "")) or extract_title_from_html(html_content, fallback_title))
    if title == "":
        title = Path(key).name
    number = normalize_space(
        decode_html_text(classification.get("number", ""))
        or extract_number_from_html(html_content)
        or decode_html_text(manifest.get("number", ""))
    )
    reading_kana = normalize_space(decode_html_text(classification.get("readingKana", ""))) or title
    lens_eval = classification.get("lensEvaluation", {})
    lens_eval = lens_eval if isinstance(lens_eval, dict) else {}
    lens_a = lens_eval.get("lensA", {})
    lens_a = lens_a if isinstance(lens_a, dict) else {}
    lens_b = lens_eval.get("lensB", {})
    lens_b = lens_b if isinstance(lens_b, dict) else {}
    combined = lens_eval.get("combined", {})
    combined = combined if isinstance(combined, dict) else {}
    document_type = normalize_document_type(str(classification.get("documentType", "")).strip())
    if document_type == "その他":
        document_type = detect_document_type(title, number)
    responsible_department = normalize_space(str(classification.get("responsibleDepartment", "")).strip())
    combined_reason = normalize_space(str(combined.get("reason", "")).strip())
    reason = normalize_space(str(classification.get("reason", "")).strip())
    taxonomy_path = normalize_space(str(manifest.get("taxonomy_path", "")).strip())
    source_file = normalize_source_file_value(manifest.get("source_file") or manifest.get("stored_source_file") or "")
    if source_file == "":
        source_file = reiki_source_file_from_key(key)
    source_url = (
        clean_source_url(manifest.get("detail_url"))
        or clean_source_url(manifest.get("source_url"))
        or derive_reiki_source_url(target, source_file)
    )
    return {
        "filename": key,
        "title": title,
        "number": number,
        "reading_kana": reading_kana,
        "sortable_kana": normalize_kana(reading_kana, prefixes),
        "primary_class": normalize_space(str(classification.get("primaryClass", "")).strip()),
        "secondary_tags": join_strings(classification.get("secondaryTags", [])),
        "necessity_score": safe_int(classification.get("necessityScore", -1), -1),
        "fiscal_impact_score": safe_float(classification.get("fiscalImpactScore", 0.0), 0.0),
        "regulatory_burden_score": safe_float(classification.get("regulatoryBurdenScore", 0.0), 0.0),
        "policy_effectiveness_score": safe_float(classification.get("policyEffectivenessScore", 0.0), 0.0),
        "lens_tags": join_strings(classification.get("lensTags", [])),
        "lens_a_stance": normalize_space(str(lens_a.get("stance", "")).strip()),
        "lens_b_stance": normalize_space(str(lens_b.get("stance", "")).strip()),
        "combined_stance": normalize_space(str(combined.get("stance", "")).strip()),
        "combined_reason": combined_reason,
        "document_type": document_type,
        "responsible_department": responsible_department,
        "reason": reason,
        "enactment_date": normalize_space(extract_date_from_html(html_content) or str(manifest.get("enactment_date", "")).strip()),
        "analyzed_at": normalize_space(str(classification.get("analyzedAt", "")).strip()),
        "updated_at": record_updated_at(html_path, markdown_path, classification_path),
        "source_url": source_url,
        "source_file": source_file,
        "taxonomy_path": taxonomy_path,
        "taxonomy_paths": join_strings(manifest.get("taxonomy_paths", [])),
        "content_text": content_text,
        "content_length": len(content_text),
        "title_terms": terms_text(title),
        "reading_terms": terms_text(reading_kana),
        "content_terms": terms_text(content_text),
        "department_terms": terms_text(responsible_department),
        "combined_reason_terms": terms_text(combined_reason),
        "reason_terms": terms_text(reason),
        "secondary_terms": terms_text(join_strings(classification.get("secondaryTags", []))),
        "lens_terms": terms_text(join_strings(classification.get("lensTags", []))),
        "taxonomy_terms": terms_text(taxonomy_path),
        "has_classification": bool(classification_path is not None and classification_path.exists()),
    }
