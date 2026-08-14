#!/usr/bin/env python3
"""Compatibility entry point for the election poster boards domain."""
from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    target = (
        Path(__file__).resolve().parents[2]
        / "domains"
        / "election_poster_boards"
        / "tools"
        / "init_users_db.py"
    )
    runpy.run_path(str(target), run_name="__main__")
