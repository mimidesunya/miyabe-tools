#!/usr/bin/env python3
"""索引側パーサを直したとき、何が変わったかを実データで測る。

合成した入力だけでは、打ち切りや除外の規則が本物を巻き込んでいないと言えない。
このラウンドで二度、実データの差分だけが改悪を捕まえた。

- 行の途中の文字化けを直そうとして、目次のリーダー点 `··········` を
  `ｷｷｷｷｷｷ` に変えていた
- ファイル名の日付を 1 桁まで許して、奄美市の文書番号 `r2-2-8`（令和2年
  第2回の8）を 2月8日 と読んでいた

どちらも試験は全部通っていた。**`PARSER_GENERATION` を上げる前に、必ず
これを通す。**

使い方（本番の索引ワーカーの中で走らせる）:

    python3 tools/search/parser_diff.py --doc-type minutes --baseline work/baseline
    python3 tools/search/parser_diff.py --doc-type reiki --slugs 32203-izumo-shi

`--baseline` には比較したい版のモジュールを置いたディレクトリを渡す。
省略すると `git show HEAD:<path>` で直前の版を取り出す。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
SEARCH_DIR = REPO_ROOT / "tools" / "search"
GIJIROKU_DIR = REPO_ROOT / "tools" / "gijiroku"

# 比較するのはこの二つ。索引の中身を決めているのはここである。
COMPARED_MODULES = {
    "minutes": ("tools/gijiroku/minutes_kind.py", "minutes_kind"),
    "reiki": ("tools/search/scraped_source_records.py", "scraped_source_records"),
}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass は自分のモジュールを `sys.modules` から引く。登録しないと
    # 型の解決に失敗する。新旧を別の名前で載せるので衝突はしない。
    sys.modules[name] = module
    module.__name__ = name
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def baseline_path(relative: str, baseline: Path | None) -> Path:
    """比べる相手の版を用意する。

    `--baseline` にはファイルでもディレクトリでも渡せる。省略したときは
    git の直前の版を取り出すが、**本番の配置先は git 作業ツリーではない**
    ので、そこでは使えない。開発機で

        git show HEAD:tools/gijiroku/minutes_kind.py > /tmp/base/minutes_kind.py

    のように取り出して一緒に送り、`--baseline /tmp/base` を渡す。
    """
    if baseline is not None:
        candidate = baseline if baseline.is_file() else baseline / Path(relative).name
        if not candidate.exists():
            raise RuntimeError(f"baseline module not found: {candidate}")
        return candidate
    try:
        out = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "git から直前の版を取り出せません。ここが git 作業ツリーでないなら、"
            f"比較したい版を送って --baseline で指定してください（対象: {relative}）。"
        ) from exc
    temporary = Path(tempfile.mkdtemp(prefix="parser-baseline-")) / Path(relative).name
    temporary.write_bytes(out.stdout)
    return temporary


def sample_slugs(doc_type: str, wanted: int, seed: int, only: list[str]) -> list[str]:
    if only:
        return only
    sys.path.insert(0, str(REPO_ROOT))
    if doc_type == "minutes":
        from tools.gijiroku import gijiroku_targets as targets

        slugs = sorted({str(t["slug"]) for t in targets.iter_gijiroku_targets()})
    else:
        from tools.reiki import reiki_targets as targets

        slugs = sorted({str(t["slug"]) for t in targets.iter_reiki_targets()})
    random.Random(seed).shuffle(slugs)
    return slugs[:wanted]


def minutes_reader(module: Any, records: Any) -> Callable[[Path, Path], Any]:
    def read(file_path: Path, root: Path) -> Any:
        text = records.read_text_auto(file_path)
        url = records.extract_source_url_from_text(text)
        hint = " ".join(
            part for part in (file_path.relative_to(root).as_posix(), url) if part
        )
        try:
            return module.extract_plausible_held_on(
                text,
                title=file_path.stem,
                year_label=file_path.parent.name,
                source_year=None,
                filename=hint,
            )
        except Exception as exc:  # 例外そのものが差分である
            return f"ERROR {type(exc).__name__}"

    return read


def reiki_reader(module: Any, _records: Any) -> Callable[[Path, Path], Any]:
    def read(file_path: Path, _root: Path) -> Any:
        try:
            return module.extract_date_from_html(module.read_text_auto(file_path))
        except Exception as exc:
            return f"ERROR {type(exc).__name__}"

    return read


def iter_files(doc_type: str, slug: str, limit: int) -> tuple[Path, list[Path]]:
    sys.path.insert(0, str(REPO_ROOT))
    if doc_type == "minutes":
        from tools.gijiroku import gijiroku_targets as targets

        root = Path(targets.load_gijiroku_target(slug)["downloads_dir"])
    else:
        from tools.reiki import reiki_targets as targets

        targets.load_reiki_target(slug)
        root = Path("/workspace/data/reiki") / slug / "html"
    if not root.is_dir():
        return root, []
    return root, [p for p in sorted(root.rglob("*")) if p.is_file()][:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-type", choices=sorted(COMPARED_MODULES), required=True)
    parser.add_argument("--baseline", default="", help="比較する版を置いたディレクトリ")
    parser.add_argument("--municipalities", type=int, default=40)
    parser.add_argument("--files-per-municipality", type=int, default=300)
    parser.add_argument("--seed", type=int, default=5)
    parser.add_argument("--slugs", default="", help="カンマ区切りで自治体を指定")
    parser.add_argument("--max-lost", type=int, default=0, help="これを超えて読めなくなったら失敗")
    parser.add_argument("--json", default="", help="結果を書き出す先")
    args = parser.parse_args()

    relative, module_name = COMPARED_MODULES[args.doc_type]
    for path in (str(SEARCH_DIR), str(GIJIROKU_DIR), str(REPO_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import scraped_source_records as records  # type: ignore

    new_module = load_module(f"{module_name}_new", REPO_ROOT / relative)
    old_module = load_module(
        f"{module_name}_old",
        baseline_path(relative, Path(args.baseline) if args.baseline else None),
    )
    make_reader = minutes_reader if args.doc_type == "minutes" else reiki_reader
    read_new = make_reader(new_module, records)
    read_old = make_reader(old_module, records)

    only = [s for s in args.slugs.split(",") if s.strip()]
    slugs = sample_slugs(args.doc_type, args.municipalities, args.seed, only)

    same = gained = 0
    lost: list[tuple[str, str, Any]] = []
    changed: list[tuple[str, str, Any, Any]] = []
    looked = 0
    for slug in slugs:
        try:
            root, files = iter_files(args.doc_type, slug, args.files_per_municipality)
        except Exception:
            continue
        if not files:
            continue
        looked += 1
        for file_path in files:
            before = read_old(file_path, root)
            after = read_new(file_path, root)
            if before == after:
                same += 1
            elif not before and after:
                gained += 1
            elif before and not after:
                lost.append((slug, file_path.name, before))
            else:
                changed.append((slug, file_path.name, before, after))

    summary = {
        "doc_type": args.doc_type,
        "municipalities": looked,
        "same": same,
        "gained": gained,
        "lost": len(lost),
        "changed": len(changed),
        "lost_examples": lost[:20],
        "changed_examples": changed[:20],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    if len(lost) > args.max_lost:
        print(
            f"[FAIL] 読めていた値が {len(lost)} 件失われた（上限 {args.max_lost}）。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
