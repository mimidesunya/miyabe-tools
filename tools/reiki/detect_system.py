#!/usr/bin/env python3
"""取得元がどの製品を使っているかを、返ってきた HTML の印で見分ける。

`独自` は「まだ見ていない」の意味で使われてきた。実際には既知の製品を使って
いる自治体が混ざる。石川県・福井県・おいらせ町は legal-square だったが、
登録が案内ページを指していたので `独自` のままだった。

**登録の系統名を信じない。**取得元を開いて、印で見分ける。

使い方:

    python3 tools/reiki/detect_system.py --slugs 17000-ishikawa-ken,18000-fukui-ken
    python3 tools/reiki/detect_system.py --from-ledger   # 台帳の未取得だけ見る
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (str(REPO_ROOT), str(REPO_ROOT / "tools" / "reiki"), str(REPO_ROOT / "tools" / "gijiroku")):
    if path not in sys.path:
        sys.path.insert(0, path)

USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be)"

# 製品ごとの印。HTML に出る文字列で見分ける。前にあるものほど強い。
PRODUCT_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("legal-square", ("legal-square.com", "HAS-Shohin", "LegalSquare")),
    ("d1-law", ("Reiki-Base", "d1w_reiki", "OpenResDataWin", "d1-law.com")),
    ("g-reiki", ("g-reiki.net", "GyoseiReiki")),
    ("legalcrud", ("legalcrud.com", "public2.legalcrud")),
    ("joureikun", ("joureikun", "aggregate/catalog")),
    ("jourei-v5", ("titleName", "jourei")),
    ("kaigiroku.net", ("kaigiroku.net", "MinuteView")),
    ("kensakusystem", ("kensakusystem.jp",)),
    ("discussvision", ("discussvision", "gijiroku.net")),
]


def fetch(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(300_000)
    except urllib.error.HTTPError as exc:
        return f"__HTTP_{exc.code}__"
    except Exception as exc:
        return f"__ERR_{type(exc).__name__}__"
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def detect(html: str) -> str:
    """HTML から製品名を返す。分からなければ空。"""
    if html.startswith("__"):
        return ""
    for name, markers in PRODUCT_MARKERS:
        if any(marker in html for marker in markers):
            return name
    return ""


def detect_with_relay(url: str, *, depth: int = 1, pause: float = 0.3) -> tuple[str, str]:
    """入口を開いて製品を見分ける。案内ページなら 1 段だけリンクを辿る。

    返すのは `(製品名, 実際に見分けがついた URL)`。
    """
    import re

    html = fetch(url)
    product = detect(html)
    if product or depth <= 0 or html.startswith("__"):
        return product, url
    for match in re.finditer(r'href="([^"]+)"', html):
        href = match.group(1)
        if not any(
            marker in href
            for _, markers in PRODUCT_MARKERS
            for marker in markers
            if "." in marker
        ):
            continue
        time.sleep(pause)
        return detect_with_relay(urllib.parse.urljoin(url, href), depth=depth - 1, pause=pause)
    return "", url


def main() -> int:
    import urllib.parse  # noqa: F401  detect_with_relay が使う

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slugs", default="", help="カンマ区切り")
    parser.add_argument("--from-ledger", action="store_true", help="台帳の未取得だけ見る")
    parser.add_argument("--pause", type=float, default=0.3)
    args = parser.parse_args()

    import reiki_targets as reiki
    import gijiroku_targets as gijiroku

    wanted: list[tuple[str, str, str]] = []
    if args.from_ledger:
        from tools.tasks import coverage_ledger

        for section in coverage_ledger.read_ledger().get("sections", []):
            loader = reiki.load_reiki_target if section["doc_type"] == "reiki" else gijiroku.load_gijiroku_target
            for row in section.get("missing_rows", []):
                if row.get("reason") != "no_saved_files":
                    continue
                try:
                    target = loader(row["slug"])
                except Exception:
                    continue
                wanted.append(
                    (row["slug"], str(target.get("system_type") or ""), str(target.get("source_url") or ""))
                )
    for slug in (s.strip() for s in args.slugs.split(",") if s.strip()):
        for loader in (reiki.load_reiki_target, gijiroku.load_gijiroku_target):
            try:
                target = loader(slug)
            except Exception:
                continue
            wanted.append((slug, str(target.get("system_type") or ""), str(target.get("source_url") or "")))
            break

    mismatched = []
    for slug, declared, url in wanted:
        if not url:
            continue
        product, resolved = detect_with_relay(url, pause=args.pause)
        flag = ""
        if product and product != declared:
            flag = "  <-- 登録と違う"
            mismatched.append({"slug": slug, "declared": declared, "detected": product, "url": resolved})
        print(f"{slug:24s} 登録={declared:14s} 実体={product or '?':14s}{flag}")
        time.sleep(args.pause)
    if mismatched:
        print()
        print(json.dumps(mismatched, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
