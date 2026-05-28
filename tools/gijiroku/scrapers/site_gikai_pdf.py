#!/usr/bin/env python3
"""汎用の公式サイト議会 PDF 取得用 entry point。

実装は現在 kami_city_pdf.py と共有している。この入口を分けておくことで、
scrape_all_minutes は別 system_type として routing しつつ、共通ロジックを一箇所で育てられる。
"""

from __future__ import annotations

try:
    from tools.gijiroku.scrapers.kami_city_pdf import main
except ModuleNotFoundError:
    from kami_city_pdf import main


if __name__ == "__main__":
    raise SystemExit(main())
