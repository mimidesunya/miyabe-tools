#!/usr/bin/env python3
from __future__ import annotations

# Web ランタイムが参照する自治体マスタだけを data/municipalities へ公開用コピーする。

import shutil
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = WORKSPACE_ROOT / "work" / "municipalities"
DEST_ROOT = WORKSPACE_ROOT / "data" / "municipalities"
FILES = (
    "municipality_master.tsv",
    "assembly_minutes_system_urls.tsv",
    "reiki_system_urls.tsv",
    "municipality_homepages.csv",
)


# work/municipalities は作業用なので、そのまま公開側へ見せず必要なファイルだけ複製する。
def main() -> int:
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    copied = 0
    for filename in FILES:
        source = SOURCE_ROOT / filename
        destination = DEST_ROOT / filename
        if not source.exists():
            continue
        shutil.copy2(source, destination)
        copied += 1
        print(f"[COPY] {source} -> {destination}", flush=True)
    print(f"[DONE] copied={copied}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
