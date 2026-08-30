#!/usr/bin/env python3
"""例規集の「いま何を持っているか」が三者で食い違っていないかを調べる。

例規の収録状態は 3 か所にある。

| 場所 | 中身 | 更新のされ方 |
| --- | --- | --- |
| `html/` のファイル | 本文 | 取得のたびに増える。減らない |
| `source_manifest.json.gz` | 収録一覧 | 実行のたびに作り直す。縮む |
| OpenSearch | 検索対象 | `html/` を列挙して作る |

三者が食い違っても、いまは誰も気づかない。実際に 31 自治体でマニフェストが
ファイルより少なく（倉敷市はマニフェスト 100 に対しファイル 1000）、
気づけたのはたまたま数えたからだった。

食い違いの意味は次のとおり。

- **孤児**（ファイルにあってマニフェストに無い）
  取得が途中で終わったか、同一性の計算が変わって古い名前のまま残っている。
  検索には出るが、収録一覧には出ない。
- **欠落**（マニフェストにあってファイルが無い）
  本文が取れていない。検索にも出ない。
- **索引ずれ**（索引件数がファイル数と違う）
  索引更新が走っていないか、失敗している。

  python tools/reiki/audit_reiki_consistency.py --only-issues
  python tools/reiki/audit_reiki_consistency.py --system legal-square
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import reiki_targets  # noqa: E402


def read_manifest(work_root: Path) -> tuple[str, list[str]]:
    """マニフェストの状態と、指しているファイル名を返す。

    件数だけを比べると「1 件欠落＋別の 1 件重複」を見逃す。名前の集合で比べ、
    マニフェスト自身の重複も数える。状態は 無し / 壊れ / 空 / あり のどれか。
    """
    for name in ("source_manifest.json.gz", "source_manifest.json"):
        path = work_root / name
        if not path.exists():
            continue
        try:
            raw = gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes()
            rows = json.loads(raw.decode("utf-8"))
        except Exception:
            return "壊れ", []
        if not isinstance(rows, list):
            return "壊れ", []
        names = [
            str(row.get("source_file") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("source_file") or "").strip()
        ]
        return ("空" if not names else "あり"), names
    return "無し", []


def count_indexed(opensearch_url: str, alias: str, slug: str) -> tuple[int, list[str]] | None:
    """索引の件数と、二重に載っている原典ファイル名を返す。

    件数だけだと、同じ文書が二重に載っていても総数が合ってしまうことがある。
    異なり数（`cardinality`）は近似で ±1 程度ずれるので、実際に 2 件以上ある
    ファイル名を数える方法にしてある（`min_doc_count: 2`）。
    """
    payload = json.dumps(
        {
            "size": 0,
            "query": {"term": {"slug": slug}},
            "aggs": {
                "dups": {
                    "terms": {"field": "source_file", "min_doc_count": 2, "size": 5}
                }
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{opensearch_url.rstrip('/')}/{alias}/_search",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
        total = int(body.get("hits", {}).get("total", {}).get("value") or 0)
        buckets = body.get("aggregations", {}).get("dups", {}).get("buckets") or []
        duplicated = [str(bucket.get("key") or "") for bucket in buckets]
        return total, duplicated
    except (urllib.error.URLError, ValueError, OSError, KeyError):
        return None


def audit_target(target: dict, *, opensearch_url: str, alias: str, skip_index: bool) -> dict:
    html_dir = Path(str(target.get("html_dir") or ""))
    work_root = Path(str(target.get("work_root") or ""))
    try:
        files = {name for name in os.listdir(html_dir)}
    except OSError:
        files = set()
    manifest_state, manifest_names = read_manifest(work_root)
    manifest = set(manifest_names)
    duplicated = len(manifest_names) - len(manifest)
    counted = None if skip_index else count_indexed(opensearch_url, alias, str(target["slug"]))
    indexed, indexed_dups = counted if counted else (None, None)

    usable = manifest_state == "あり"
    orphan = len(files - manifest) if usable else 0
    missing = len(manifest - files) if usable else 0
    problems = []
    if not usable:
        problems.append(f"マニフェスト{manifest_state}")
    else:
        if orphan:
            problems.append(f"孤児{orphan}")
        if missing:
            problems.append(f"欠落{missing}")
        if duplicated:
            problems.append(f"マニフェスト重複{duplicated}")
    if indexed is not None:
        if indexed != len(files):
            problems.append(f"索引ずれ{indexed - len(files):+d}")
        if indexed_dups:
            problems.append(f"索引重複{len(indexed_dups)}種")

    return {
        "slug": str(target["slug"]),
        "name": str(target["name"]),
        "system_type": str(target.get("system_type") or ""),
        "files": len(files),
        "manifest_state": manifest_state,
        "manifest": len(manifest_names),
        "indexed": indexed if indexed is not None else -1,
        "indexed_duplicates": indexed_dups or [],
        "orphan": orphan,
        "missing": missing,
        "duplicated": duplicated,
        "problem": " ".join(problems),
        # 影索引を作ってよいか。切り替えてよいかは、これに加えて母数が上限でも
        # 未確認でもないこと（型 A）の確認が要る。
        "ready_for_shadow": usable and not orphan and not missing and not duplicated,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="例規集の収録一覧・保存ファイル・検索索引の食い違いを調べます。"
    )
    parser.add_argument("--system", default="", help="system_type で絞り込み（例: legal-square）")
    parser.add_argument("--slug", action="append", default=[], help="自治体 slug。複数指定可。")
    parser.add_argument("--only-issues", action="store_true", help="食い違いがあるものだけ表示")
    parser.add_argument("--skip-index", action="store_true", help="OpenSearch を見ない")
    parser.add_argument("--json", action="store_true", help="JSON で出力")
    parser.add_argument(
        "--opensearch-url",
        default=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"),
    )
    parser.add_argument(
        "--alias",
        default=os.environ.get("MIYABE_REIKI_ALIAS", "miyabe-reiki-current"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = list(reiki_targets.iter_reiki_targets())
    if args.system:
        targets = [t for t in targets if str(t.get("system_type") or "") == args.system]
    if args.slug:
        wanted = set(args.slug)
        targets = [t for t in targets if str(t["slug"]) in wanted]

    rows = [
        audit_target(
            target,
            opensearch_url=args.opensearch_url,
            alias=args.alias,
            skip_index=args.skip_index,
        )
        for target in targets
    ]
    rows.sort(key=lambda row: (-(row["orphan"] + row["missing"]), -row["files"]))
    shown = [row for row in rows if row["problem"]] if args.only_issues else rows

    if args.json:
        print(json.dumps(shown, ensure_ascii=False, indent=1))
        return 0

    print("slug\tname\tsystem\tファイル\tマニフェスト\t状態\t索引\t食い違い")
    for row in shown:
        print(
            f"{row['slug']}\t{row['name']}\t{row['system_type']}\t{row['files']}\t"
            f"{row['manifest']}\t{row['manifest_state']}\t{row['indexed']}\t"
            f"{row['problem']}"
        )

    with_problem = [row for row in rows if row["problem"]]
    ready = [row for row in rows if row["ready_for_shadow"]]
    print(
        f"\n対象 {len(rows)} 自治体 / 食い違い {len(with_problem)}"
        f" / 孤児の合計 {sum(row['orphan'] for row in rows)}"
        f" / 欠落の合計 {sum(row['missing'] for row in rows)}"
        f" / マニフェスト重複 {sum(row['duplicated'] for row in rows)}"
        f" / 影索引を作れる {len(ready)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
