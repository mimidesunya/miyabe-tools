#!/usr/bin/env python3
"""リポジトリのテストをまとめて走らせる。

    python dev/run_tests.py            # 全部
    python dev/run_tests.py --only php # pytest / lint / php / mcp のどれかだけ

1. pytest（`pytest.ini` の testpaths。テストが本物の作業データや OpenSearch・
   管理 DB に触らないよう、`conftest.py` が置き場を一時ディレクトリへ向ける）
2. git 管理下の PHP 全部の `php -l`
3. PHP のテスト（`tools/**/test_*.php`・`tests/*_test.php`・`domains/**/tests/*_test.php`）。
   どれも失敗すると終了コードが 0 以外になる
4. MCP サーバ（docker/mcp）の型検査 `npm run check`。`node_modules` が無ければ飛ばす

OpenSearch は繋がらない宛先にしておく。PHP のテストが手元の索引を探しに行かない。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STEPS = ("pytest", "lint", "php", "mcp")


def tracked(*patterns: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", *patterns],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def run(cmd: list[str], *, cwd: Path = REPO_ROOT, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, env=os.environ.copy(), text=True, encoding="utf-8", errors="replace",
        capture_output=quiet,
    )


def step_pytest() -> bool:
    return run([sys.executable, "-m", "pytest", "-q"]).returncode == 0


def step_lint() -> bool:
    files = tracked("*.php")

    def lint(path: str) -> tuple[str, int, str]:
        result = run(["php", "-l", path], quiet=True)
        return path, result.returncode, (result.stdout + result.stderr).strip()

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        failures = [(path, out) for path, code, out in pool.map(lint, files) if code != 0]
    for path, out in failures:
        print(f"[lint] {path}\n{out}")
    print(f"[lint] php -l {len(files)} 本、失敗 {len(failures)}")
    return not failures


def step_php() -> bool:
    files = tracked("tools/*test_*.php", "tests/*_test.php", "domains/*/tests/*_test.php")
    failures = []
    for path in files:
        result = run(["php", path], quiet=True)
        if result.returncode != 0:
            failures.append(path)
            print(f"[php] FAILED {path}\n{(result.stdout + result.stderr).strip()}")
    print(f"[php] {len(files)} 本、失敗 {len(failures)}")
    return not failures


def step_mcp() -> bool:
    mcp_dir = REPO_ROOT / "docker" / "mcp"
    if not (mcp_dir / "node_modules").is_dir():
        print("[mcp] docker/mcp/node_modules が無いので飛ばします（npm ci で入る）")
        return True
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return run([npm, "run", "check"], cwd=mcp_dir).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="リポジトリのテストをまとめて走らせる。")
    parser.add_argument("--only", choices=STEPS, action="append", help="この段だけ走らせる（複数可）")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ["OPENSEARCH_URL"] = "http://127.0.0.1:9"
    steps = {"pytest": step_pytest, "lint": step_lint, "php": step_php, "mcp": step_mcp}
    failed = []
    for name in args.only or STEPS:
        started = time.monotonic()
        print(f"== {name}", flush=True)
        ok = steps[name]()
        print(f"== {name}: {'OK' if ok else 'FAILED'}（{time.monotonic() - started:.1f} 秒）", flush=True)
        if not ok:
            failed.append(name)
    if failed:
        print("失敗: " + ", ".join(failed))
        return 1
    print("すべて OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
