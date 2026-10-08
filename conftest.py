"""テストが本物の作業データ・外部サービスに触らないようにする。

`tools/` の下のモジュールは、`sys.path` 経由の名前（`discovered_sources`）と
パッケージ経由の名前（`tools.discovered_sources`）の 2 つで読まれることがある。
片方の置き場だけ差し替えると、もう片方が本物の `work/`・`data/` に書く
（2026-10-08、`tools/tasks/test_discover_sources.py` が手元の
`work/gijiroku/discovered_sources.json` に、別のテストが
`data/background_tasks/gijiroku.json` に書いていた）。

ここでは、読み込まれている全部の名前について置き場を一時ディレクトリへ向け、
OpenSearch と管理 DB には繋がらないようにする。最後に本物の記録が書き換わって
いたらテストを失敗にする。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
# テストが書いてはいけない本物の記録。
GUARDED_FILES = (
    REPO_ROOT / "work" / "gijiroku" / "discovered_sources.json",
    REPO_ROOT / "work" / "reiki" / "discovered_sources.json",
)
GUARDED_DIRS = (REPO_ROOT / "data" / "background_tasks",)


def _guarded_snapshot() -> dict[str, float]:
    snapshot: dict[str, float] = {}
    paths = list(GUARDED_FILES)
    for directory in GUARDED_DIRS:
        if directory.is_dir():
            paths.extend(directory.glob("*.json"))
    for path in paths:
        try:
            snapshot[str(path)] = path.stat().st_mtime
        except OSError:
            snapshot[str(path)] = -1.0
    return snapshot


def pytest_configure(config) -> None:
    # 繋がらない宛先にしておく。子プロセスにも引き継がれる。
    os.environ["OPENSEARCH_URL"] = "http://127.0.0.1:9"
    os.environ.pop("MANAGEMENT_DATABASE_URL", None)
    os.environ.pop("DATABASE_URL", None)
    config._miyabe_guarded = _guarded_snapshot()


def pytest_sessionfinish(session, exitstatus) -> None:
    before = getattr(session.config, "_miyabe_guarded", None)
    if before is None:
        return
    after = _guarded_snapshot()
    changed = sorted(path for path, mtime in after.items() if before.get(path, -1.0) != mtime)
    if changed:
        print("\n[conftest] テストが本物の作業データを書き換えました:\n  " + "\n  ".join(changed), file=sys.stderr)
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _isolate_work_data(tmp_path_factory, monkeypatch):
    status_root = tmp_path_factory.mktemp("background_tasks")
    work_root = tmp_path_factory.mktemp("work")
    for module in list(sys.modules.values()):
        configure = getattr(module, "configure_status_root", None)
        if callable(configure) and getattr(module, "__name__", "").endswith("status"):
            configure(status_root)
        # discovered_sources は WORK_ROOT/<task>/discovered_sources.json に書く。
        if callable(getattr(module, "store_path", None)) and isinstance(getattr(module, "WORK_ROOT", None), Path):
            monkeypatch.setattr(module, "WORK_ROOT", work_root)
    yield
    for module in list(sys.modules.values()):
        configure = getattr(module, "configure_status_root", None)
        if callable(configure) and getattr(module, "__name__", "").endswith("status"):
            configure(None)
