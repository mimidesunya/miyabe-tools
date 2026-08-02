#!/usr/bin/env python3
"""後方互換用。実装は tools.gijiroku.audit_minutes_robots に置く。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.gijiroku.audit_minutes_robots import *  # noqa: F401,F403,E402
from tools.gijiroku.audit_minutes_robots import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
