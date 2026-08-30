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


def load_manifest_names(work_root: Path) -> set[str] | None:
    """マニフェストが指しているファイル名を返す。読めなければ None。"""
    for name in ("source_manifest.json.gz", "source_manifest.json"):
        path = work_root / name
        if not path.exists():
            continue
        try:
            raw = gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes()
            rows = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(rows, list):
            return None
        return {
            str(row.get("source_file") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("source_file") or "").strip()
        }
    return None


def count_indexed(opensearch_url: str, alias: str, slug: str) -> int | None:
    """OpenSearch に入っている件数。数えられなければ None。"""
    payload = json.dumps({"query": {"term": {"slug": slug}}}).encode("utf-8")
    request = urllib.request.Request(
        f"{opensearch_url.rstrip('/')}/{alias}/_count",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(json.loads(response.read().decode("utf-8")).get("count") or 0)
    except (urllib.error.URLError, ValueError, OSError):
        return None


def audit_target(target: dict, *, opensearch_url: str, alias: str, skip_index: bool) -> dict:
    html_dir = Path(str(target.get("html_dir") or ""))
    work_root = Path(str(target.get("work_root") or ""))
    try:
        files = {name for name in os.listdir(html_dir)}
    except OSError:
        files = set()
    manifest = load_manifest_names(work_root)
    indexed = None if skip_index else count_indexed(opensearch_url, alias, str(target["slug"]))

    orphan = len(files - manifest) if manifest is not None else 0
    missing = len(manifest - files) if manifest is not None else 0
    problems = []
    if manifest is None:
        problems.append("マニフェスト無し")
    else:
        if orphan:
            problems.append(f"孤児{orphan}")
        if missing:
            problems.append(f"欠落{missing}")
    if indexed is not None and indexed != len(files):
        problems.append(f"索引ずれ{indexed - len(files):+d}")

    return {
        "slug": str(target["slug"]),
        "name": str(target["name"]),
        "system_type": str(target.get("system_type") or ""),
        "files": len(files),
        "manifest": len(manifest) if manifest is not None else -1,
        "indexed": indexed if indexed is not None else -1,
        "orphan": orphan,
        "missing": missing,
        "problem": " ".join(problems),
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

    print("slug\tname\tsystem\tファイル\tマニフェスト\t索引\t食い違い")
    for row in shown:
        print(
            f"{row['slug']}\t{row['name']}\t{row['system_type']}\t{row['files']}\t"
            f"{row['manifest']}\t{row['indexed']}\t{row['problem']}"
        )

    with_problem = [row for row in rows if row["problem"]]
    print(
        f"\n対象 {len(rows)} 自治体 / 食い違い {len(with_problem)}"
        f" / 孤児の合計 {sum(row['orphan'] for row in rows)}"
        f" / 欠落の合計 {sum(row['missing'] for row in rows)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
