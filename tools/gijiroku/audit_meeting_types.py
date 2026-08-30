#!/usr/bin/env python3
"""取得済み会議録に会議種別の偏りが無いかを調べる。

会議録システムは本会議と委員会を別々の入口に置いていることが多く、
スクレイパが片方の入口しか見ていないと「本会議だけ揃っていて委員会が丸ごと無い」
という取りこぼしが起きる。福岡県（dbsr）が実際にそうだった。

本会議しか無い自治体を機械的に見つけるのが目的。ただし小規模自治体は
委員会記録をそもそも公開していないことも多いので、**会議数が多いのに
委員会が 1 件も無い**自治体を疑わしいものとして並べる。

判定は `meetings_index.json` の `meeting_group` と `title` だけを見る。

**ここで分かるのは「登録した URL の中身」だけである。** 次の 3 つは見えない。

1. 議会が**別の URL** で委員会記録を公開している場合。米子市は本会議の検索
   システムを登録しているが、委員会会議録は市サイトの別ページにある
2. `meetings_index.json` は**発見した候補**の一覧で、本文の取得結果ではない。
   候補に挙がっていても本文が取れていないことがある
3. 委員会が 1 件でもあれば判定から外れる。**一部の委員会・年度だけ欠けている**
   のは見つけられない

つまりここで出る「取りこぼし」は**下限**であって、出なかったから問題が無いとは
言えない。疑わしいと出たものも、出なかったものも、個別に確かめること。

  python tools/gijiroku/audit_meeting_types.py --only-issues
  python tools/gijiroku/audit_meeting_types.py --system gijiroku.com --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import gijiroku_storage  # noqa: E402
import gijiroku_targets  # noqa: E402


# 委員会・協議会の類。voices 系は「総務常任」「予算特別」のように
# 「委員会」を落とした形で持つので、常任・特別・議会運営も手掛かりに含める。
COMMITTEE_PATTERN = re.compile(
    r"委員|協議会|分科会|審査会|部会|常任|特別|議会運営|全員協議"
)
# 本会議側の言い回し。「定例議会」「臨時議会」と書く議会もある（愛知県など）。
PLENARY_PATTERN = re.compile(r"本会議|定例|臨時|通年会期")

# 「委員会という語が無い」だけでは判定にならない。委員会名を「総務」「厚生経済」の
# ように短く持つ取得元があり、それを取りこぼしと数えると誤検出になる（熊本市など）。
# 逆に **どの会議も本会議の言い回ししか持たない** 自治体は、委員会の入口ごと
# 見落としている可能性が高い。福岡県（dbsr）が実際にその形だった。
# 小規模自治体は委員会記録をそもそも公開していないことも多いので、会議数で足切りする。
DEFAULT_MIN_MEETINGS = 200


def load_offered_types(target: dict) -> list[str] | None:
    """取得元が「こういう会議種別がある」と示している一覧を読む。

    スクレイパが `scrape_state.json` の `source_coverage.offered_meeting_types`
    へ書く。まだ書かれていない（この記録が入る前に取得した）自治体は None を返す。
    """
    work_dir = str(target.get("work_dir") or "").strip()
    if work_dir == "":
        return None
    names = gijiroku_storage.load_offered_meeting_types(Path(work_dir))
    if names:
        return names
    # 記録の置き場所を移す前に取得した分は、まだ scrape_state.json に入っている。
    try:
        raw = json.loads((Path(work_dir) / "scrape_state.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    coverage = raw.get("source_coverage") if isinstance(raw, dict) else None
    offered = coverage.get("offered_meeting_types") if isinstance(coverage, dict) else None
    if not isinstance(offered, list):
        return None
    names = [str(value).strip() for value in offered if str(value).strip()]
    # 空リストは「取得元に会議種別が無い」ではなく「読み取れなかった」ことの方が多い。
    # 古い記録には空のまま残っているものがあるので、未確認として扱う。
    return names or None


def load_meetings(index_path: Path) -> list[dict]:
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    for key in ("meetings", "items"):
        value = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def meeting_text(meeting: dict) -> str:
    parts = [
        str(meeting.get("meeting_group") or ""),
        str(meeting.get("title") or ""),
        str(meeting.get("year_label") or ""),
    ]
    return " ".join(parts)


def audit_target(target: dict, *, min_meetings: int) -> dict:
    index_path = Path(str(target["index_json_path"]))
    meetings = load_meetings(index_path)
    committee = 0
    plenary = 0
    other = 0
    groups: dict[str, int] = {}
    for meeting in meetings:
        text = meeting_text(meeting)
        if COMMITTEE_PATTERN.search(text):
            committee += 1
        elif PLENARY_PATTERN.search(text):
            plenary += 1
        else:
            other += 1
        group = str(meeting.get("meeting_group") or "").strip()
        if group:
            groups[group] = groups.get(group, 0) + 1

    total = len(meetings)
    big = total >= min_meetings
    offered = load_offered_types(target)
    offered_committee = [name for name in (offered or []) if COMMITTEE_PATTERN.search(name)]

    if big and committee == 0 and other == 0 and plenary > 0:
        # 本会議の言い回ししか無い。取得元が何を持っていると言っているかで裏を取る。
        if offered is None:
            verdict = "本会議のみ(未確認)"
        elif offered_committee:
            # 取得元は委員会があると言っているのに 1 件も無い。取りこぼし確定。
            verdict = "取りこぼし"
        else:
            # 登録している URL の中には委員会が無い、というだけ。議会が別の URL で
            # 委員会記録を公開していることがある（米子市）ので、取りこぼしが
            # 無いとは言えない。
            verdict = "登録先は本会議のみ"
    elif big and committee == 0 and plenary == 0:
        # 会議種別が記録されていないので、この情報だけでは判定できない。
        verdict = "種別不明"
    else:
        verdict = ""

    return {
        "slug": str(target["slug"]),
        "name": str(target["name"]),
        "system_family": str(target["system_family"]),
        "system_type": str(target["system_type"]),
        "total": total,
        "committee": committee,
        "plenary": plenary,
        "other": other,
        "distinct_groups": len(groups),
        "top_groups": sorted(groups.items(), key=lambda kv: -kv[1])[:5],
        "verdict": verdict,
        "offered_types": offered,
        "offered_committee": offered_committee,
        # 会議種別が会議ごとに全部違う＝種別として機能していない。
        "group_is_title": total > 0 and len(groups) >= total,
        "suspect": bool(verdict),
        "no_index": total == 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="取得済み会議録に本会議しか無い自治体を洗い出します。"
    )
    parser.add_argument("--system", default="", help="system family で絞り込み（例: dbsr）")
    parser.add_argument("--slug", action="append", default=[], help="自治体 slug。複数指定可。")
    parser.add_argument(
        "--min-meetings",
        type=int,
        default=DEFAULT_MIN_MEETINGS,
        help=f"疑わしいとみなす会議数の下限（既定 {DEFAULT_MIN_MEETINGS}）",
    )
    parser.add_argument("--only-issues", action="store_true", help="疑わしい自治体だけ表示")
    parser.add_argument("--json", action="store_true", help="JSON で出力")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = gijiroku_targets.iter_gijiroku_targets(args.system or None)
    if args.slug:
        wanted = set(args.slug)
        targets = [t for t in targets if str(t["slug"]) in wanted]

    rows = [audit_target(t, min_meetings=args.min_meetings) for t in targets]
    rows.sort(key=lambda r: (-int(r["suspect"]), -int(r["total"])))
    shown = [r for r in rows if r["suspect"]] if args.only_issues else rows

    if args.json:
        print(json.dumps(shown, ensure_ascii=False, indent=1))
        return 0

    print(
        "slug\tname\tsystem\t会議数\t委員会\t本会議\tその他\t種別数\t判定\t"
        "取得元が示す委員会\t主な種別"
    )
    for row in shown:
        top = ",".join(f"{name}:{count}" for name, count in row["top_groups"])
        offered = ",".join(row["offered_committee"][:4])
        print(
            f"{row['slug']}\t{row['name']}\t{row['system_family']}\t{row['total']}\t"
            f"{row['committee']}\t{row['plenary']}\t{row['other']}\t{row['distinct_groups']}\t"
            f"{row['verdict']}\t{offered}\t{top}"
        )

    by_system: dict[str, list[dict]] = {}
    for row in rows:
        by_system.setdefault(row["system_family"], []).append(row)
    print(
        "\n系統\t対象\t索引あり\t取りこぼし\t本会議のみ(未確認)\t登録先は本会議のみ\t"
        "種別不明\t種別=表題",
        file=sys.stderr,
    )
    for system, group in sorted(by_system.items(), key=lambda kv: -len(kv[1])):
        counts = {
            key: len([r for r in group if r["verdict"] == key])
            for key in ("取りこぼし", "本会議のみ(未確認)", "登録先は本会議のみ", "種別不明")
        }
        indexed = [r for r in group if not r["no_index"]]
        as_title = [r for r in group if r["group_is_title"]]
        print(
            f"{system}\t{len(group)}\t{len(indexed)}\t{counts['取りこぼし']}\t"
            f"{counts['本会議のみ(未確認)']}\t{counts['登録先は本会議のみ']}\t"
            f"{counts['種別不明']}\t{len(as_title)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
