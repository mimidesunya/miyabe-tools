"""スクレイパ成果物の保存プリミティブ。

圧縮テキスト・JSON の書き込み、置換前アーカイブ、文字コード fallback、
digest 計算をここへまとめ、source system が違っても保存の振る舞いを揃える。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


TEXT_ENCODINGS = ("utf-8", "cp932", "shift_jis", "euc_jp")
ARCHIVE_MARKER = "_archive"
SCRAPE_VALIDATION_MODE = "classified_scrape_result"
SCRAPE_EXCLUDED_STATUSES = frozenset({"empty_text", "empty_pdf_text"})
SCRAPE_FAILED_STATUSES = frozenset({"error", "timeout", "not_found"})


def gzip_path(path: Path) -> Path:
    return path if path.suffix.lower() == ".gz" else path.with_name(path.name + ".gz")


def logical_path(path: Path) -> Path:
    return path.with_suffix("") if path.suffix.lower() == ".gz" else path


def existing_output(path: Path) -> Path | None:
    candidates = [gzip_path(path)]
    if gzip_path(path) != path:
        candidates.append(path)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def existing_named_outputs(directory: Path, stem: str) -> list[Path]:
    try:
        if not directory.exists():
            return []
    except OSError:
        return []
    try:
        return sorted(
            [path for path in directory.glob(stem + ".*") if path.is_file()],
            key=lambda path: path.name,
        )
    except OSError:
        return []


def archive_root_for(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve()
    parts = resolved.parts
    for marker in ("gijiroku", "reiki"):
        if marker not in parts:
            continue
        index = len(parts) - 1 - list(reversed(parts)).index(marker)
        if index + 1 >= len(parts) - 1:
            continue
        base = Path(*parts[: index + 2])
        try:
            return base / ARCHIVE_MARKER, resolved.relative_to(base)
        except ValueError:
            continue
    return resolved.parent / ARCHIVE_MARKER, Path(resolved.name)


def archive_existing_file(path: Path, *, reason: str = "replace") -> Path | None:
    # 置換前ファイルは元の自治体データの近くに残す。
    # 別のバックアップ置き場を探さなくても、リモート上で差分調査できるようにする。
    try:
        candidate = path.resolve()
        if ARCHIVE_MARKER in candidate.parts or not candidate.is_file():
            return None
        archive_root, relative = archive_root_for(candidate)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_reason = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in reason).strip("_") or "replace"
        destination = archive_root / f"{stamp}_{safe_reason}" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, destination)
        return destination
    except Exception as exc:
        print(f"[WARN] failed to archive old file before {reason}: {path} [{type(exc).__name__}] {exc}", flush=True)
        return None


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
    return raw.decode("utf-8", errors="ignore")


def write_bytes(path: Path, data: bytes, *, compress: bool = False) -> Path:
    final_path = gzip_path(path) if compress else path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    existing = existing_output(path)
    archived_existing: Path | None = None
    if existing is not None:
        try:
            if read_bytes(existing) != data:
                archive_existing_file(existing, reason="overwrite")
                archived_existing = existing.resolve()
        except Exception:
            archive_existing_file(existing, reason="overwrite")
            archived_existing = existing.resolve()
    if compress:
        with gzip.open(final_path, "wb", compresslevel=6) as handle:
            handle.write(data)
        plain_path = logical_path(final_path)
        if plain_path != final_path and plain_path.exists():
            if archived_existing != plain_path.resolve():
                archive_existing_file(plain_path, reason="delete")
            plain_path.unlink()
    else:
        final_path.write_bytes(data)
        gz_path = gzip_path(final_path)
        if gz_path != final_path and gz_path.exists():
            if archived_existing != gz_path.resolve():
                archive_existing_file(gz_path, reason="delete")
            gz_path.unlink()
    return final_path


def write_text(path: Path, text: str, *, encoding: str = "utf-8", compress: bool = False) -> Path:
    data = text.encode(encoding)
    return write_bytes(path, data, compress=compress)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text_auto(path))
    except Exception:
        return default


def write_json(path: Path, payload: Any, *, compress: bool = False) -> Path:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return write_text(path, text + "\n", compress=compress)


def logical_suffix(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    return suffixes[-1] if suffixes else ""


def source_key(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if relative.suffix.lower() == ".gz":
        relative = relative.with_suffix("")
    return relative.with_suffix("").as_posix()


def item_signature(payload: Any) -> str:
    if is_dataclass(payload):
        payload = asdict(payload)
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def disambiguated_stem(stem: str, discriminator: str, occurrence_index: int) -> str:
    """最初の保存名は固定し、同名衝突した 2 件目以降だけ suffix を付ける。"""
    stem = str(stem).strip() or "meeting"
    if occurrence_index <= 0:
        return stem
    token = hashlib.sha1(str(discriminator or stem).encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{token}"


def load_state(path: Path) -> dict[str, Any]:
    state = load_json(path, {"version": 1, "items": {}})
    if not isinstance(state, dict):
        return {"version": 1, "items": {}}
    if not isinstance(state.get("items"), dict):
        state["items"] = {}
    state.setdefault("version", 1)
    return state


# 取得元が示す会議種別。scrape_state.json は実行のたびに消されるので、
# ここへ分けて置く（batch.py の remove_stale_scrape_state）。
# 取得元をどこまで歩けたかの記録。scrape_state.json は実行のたびに消される
# （batch.py の remove_stale_scrape_state）ので、そこに置くと殺された実行が
# 「全部歩けた」という記録ごと消してしまう。実行をまたぐものは別ファイルに置く。
SOURCE_COVERAGE_FILE = "source_coverage.json"


def source_coverage_path(work_dir: Path) -> Path:
    return Path(work_dir) / SOURCE_COVERAGE_FILE


def load_source_coverage(work_dir: Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """走査の記録を読む。無ければ空の辞書。

    `state` を渡すと、別ファイルがまだ無い自治体について
    scrape_state.json の中の古い記録に落ちる。移行が済むまでの経路。
    """
    try:
        payload = json.loads(source_coverage_path(work_dir).read_text(encoding="utf-8"))
    except Exception:
        payload = None
    if not isinstance(payload, dict) or not payload:
        payload = None
    previous = (state or {}).get("source_coverage")
    if not isinstance(previous, dict) or not previous:
        previous = None
    if payload is None:
        return previous or {}
    if previous is None:
        return payload
    # 両方あるなら新しい方。写しの書き込みが失敗していると、state の方が
    # 新しいのに古い写しを返してしまう。
    if str(previous.get("updated_at") or "") > str(payload.get("updated_at") or ""):
        return previous
    return payload


def mark_walk_started(work_dir: Path, previous: dict[str, Any], when: str) -> None:
    """これから取得元を歩き直す、という印を残す。

    前回 complete の自治体は、歩き直しの途中で殺されても complete のまま
    残る。取得元が増えていても完了に見えてしまうので、始めた時刻を
    別に控えて、終わった時刻より新しければ確認前と分かるようにする。

    `previous` は読み終えたあとの記録を渡すこと。先に印だけ書くと、
    写しがまだ無い自治体で state 側の記録を取りこぼす。
    """
    payload = dict(previous) if previous else {"mode": "source_discovery_coverage"}
    payload["walk_started_at"] = when
    save_source_coverage(work_dir, payload)


def effective_walk_state(payload: dict[str, Any] | None) -> str:
    """記録から読み取れる走査の状態を返す。

    `complete` でも、歩き直しを始めたまま終われていないなら当てにできない。
    読み手ごとにこの判断を書くと、公開画面と監査とキューで別々の答えを出す。
    """
    if not isinstance(payload, dict) or not payload:
        return "unknown"
    if int(payload.get("rule_version") or 0) < COVERAGE_RULE_VERSION:
        # 古い規則で書かれた記録。complete の意味が違うので信用しない。
        return "stale_rule"
    state = str(payload.get("state") or "").strip() or "unknown"
    started = str(payload.get("walk_started_at") or "")
    updated = str(payload.get("updated_at") or "")
    if state == "complete" and started and started > updated:
        return "rewalking"
    return state


# 走査記録の判定ルールの版。意味が変わったら上げる。上げないと、古い規則で
# 書かれた complete を新しい規則の complete と同じに読んでしまう。
#   2 … ページ送りを諦めた回数を失敗に数え、歩き直しの印を持つ形
COVERAGE_RULE_VERSION = 2


def save_source_coverage(work_dir: Path, payload: dict[str, Any]) -> None:
    """走査の記録を保存する。空では上書きしない。

    save_state は会議ごとに呼ばれるので、中身が変わっていないときは書かない。
    """
    if not payload:
        return
    payload = {"rule_version": COVERAGE_RULE_VERSION, **payload}
    path = source_coverage_path(work_dir)
    try:
        if json.loads(path.read_text(encoding="utf-8")) == payload:
            return
    except Exception:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)


OFFERED_MEETING_TYPES_FILE = "offered_meeting_types.json"


def offered_meeting_types_path(work_dir: Path) -> Path:
    return Path(work_dir) / OFFERED_MEETING_TYPES_FILE


def load_offered_meeting_types(work_dir: Path) -> list[str] | None:
    """取得元が示す会議種別を読む。記録が無ければ None。"""
    payload = load_json(offered_meeting_types_path(work_dir), None)
    names = payload.get("names") if isinstance(payload, dict) else None
    if not isinstance(names, list):
        return None
    cleaned = [str(value).strip() for value in names if str(value).strip()]
    return cleaned or None


def save_offered_meeting_types(work_dir: Path, names: list[str]) -> None:
    """読み取れた会議種別だけを保存する。空では上書きしない。

    空で上書きすると、監査が「取得元にも委員会が無い」と読んでしまう。
    読み取れなかったのか本当に無いのかは区別できないので、前の記録を残す。
    """
    cleaned = [str(value).strip() for value in names if str(value).strip()]
    if not cleaned:
        return
    write_json(
        offered_meeting_types_path(work_dir),
        {"names": cleaned, "observed_at": datetime.now().strftime("%Y%m%d_%H%M%S")},
    )


def save_state(path: Path, state: dict[str, Any]) -> None:
    # 走査の記録だけは実行をまたいで残す必要があるので、書くたびに写しておく。
    # 呼ぶ側に覚えさせると、必ずどこかで書き忘れる。
    coverage = state.get("source_coverage")
    if isinstance(coverage, dict) and coverage:
        try:
            save_source_coverage(path.parent, coverage)
        except Exception as error:
            # 写しに失敗してもスクレイプは続ける。ただし黙って捨てると
            # 「state は新しいのに写しが古い」に後から気付けない。
            print(f"[WARN] 走査記録の写しに失敗しました: {error}", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def update_progress_state(path: Path, *, current: int, total: int, unit: str = "meeting") -> None:
    state = load_state(path)
    state["progress_current"] = max(0, int(current))
    state["progress_total"] = max(0, int(total))
    state["progress_unit"] = str(unit).strip() or "meeting"
    save_state(path, state)


def normalized_status_counts(status_counts: dict[str, int] | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_status, raw_count in (status_counts or {}).items():
        status = str(raw_status or "").strip()
        if not status:
            continue
        try:
            count = max(0, int(raw_count))
        except Exception:
            continue
        if count > 0:
            counts[status] = counts.get(status, 0) + count
    return counts


def count_statuses(status_counts: dict[str, int], statuses: frozenset[str]) -> int:
    return sum(max(0, int(status_counts.get(status, 0))) for status in statuses)


def classified_scrape_summary(
    *,
    discovered_count: int,
    downloaded_count: int,
    status_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """会議録候補を「成功・除外・失敗・未確認」に分けた完了判定用 summary を作る。"""
    counts = normalized_status_counts(status_counts)
    discovered = max(0, int(discovered_count))
    downloaded = max(0, int(downloaded_count))
    excluded = count_statuses(counts, SCRAPE_EXCLUDED_STATUSES)
    failed = count_statuses(counts, SCRAPE_FAILED_STATUSES)

    # discovered は最初に見つかった候補数なので、目次・一覧・空 PDF が混ざることがある。
    # ただし「成功でも除外でも失敗でもない候補」は取りこぼしなので、明示的に失敗扱いへ回す。
    accounted = downloaded + excluded + failed
    unknown_missing = max(0, discovered - accounted)
    eligible = downloaded + failed + unknown_missing

    warning_lines: list[str] = []
    # 本文が取り出せなかった PDF は、目次や名簿を除いたのとは事情が違う。
    # 会議録そのものなのに紙を画像で貼った PDF で、待っても本文にならない。
    empty_pdf = int(counts.get("empty_pdf_text", 0))
    other_excluded = excluded - empty_pdf
    if empty_pdf > 0:
        if downloaded == 0 and other_excluded == 0:
            warning_lines.append(
                f"取得元の PDF {empty_pdf}件はすべて文字情報を持たず、本文を取り出せません"
            )
        else:
            warning_lines.append(f"文字情報のない PDF を除外 {empty_pdf}件")
    if other_excluded > 0:
        warning_lines.append(f"会議録本体ではない候補を除外 {other_excluded}件")
    if failed > 0:
        warning_lines.append(f"取得エラー {failed}件")
    if unknown_missing > 0:
        warning_lines.append(f"取得結果が確認できない候補 {unknown_missing}件")
    # 候補は見つかったのに 1 件も取れなかった。取得元がページを作り直して
    # 一覧のリンクが全部死んでいる形がこれになる。放っておくと毎回
    # 同じ失敗を繰り返すだけで、登録の見直しが要ることが誰にも見えない。
    all_failed = discovered > 0 and downloaded == 0 and excluded == 0 and failed > 0
    if all_failed:
        warning_lines.append(
            f"候補 {discovered}件のすべてが取得エラー。取得元のページ構成が"
            "変わって、登録した入口が古くなっている可能性があります"
        )

    return {
        "mode": SCRAPE_VALIDATION_MODE,
        "discovered_count": discovered,
        "downloaded_count": downloaded,
        "excluded_count": excluded,
        "failed_count": failed,
        "unknown_missing_count": unknown_missing,
        "eligible_count": eligible,
        "all_failed": all_failed,
        "progress_current": downloaded,
        "progress_total": eligible,
        "progress_unit": "meeting",
        "status_counts": counts,
        "warning_lines": warning_lines,
    }


def apply_classified_scrape_validation(
    state_path: Path,
    state: dict[str, Any],
    *,
    discovered_count: int,
    downloaded_count: int,
    status_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """分類済みの完了判定を scrape_state.json へ保存し、親バッチの母数を揃える。"""
    summary = classified_scrape_summary(
        discovered_count=discovered_count,
        downloaded_count=downloaded_count,
        status_counts=status_counts,
    )
    state["validation"] = summary
    state["progress_current"] = summary["progress_current"]
    state["progress_total"] = summary["progress_total"]
    state["progress_unit"] = summary["progress_unit"]
    save_state(state_path, state)
    return summary
