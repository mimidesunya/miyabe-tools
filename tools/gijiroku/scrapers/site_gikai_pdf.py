#!/usr/bin/env python3
"""Entry point for generic official-site gikai PDF scraping.

The implementation is currently shared with kami_city_pdf.py; keeping this
separate entry point lets scrape_all_minutes route a distinct system_type while
the shared logic evolves in one place.
"""

from __future__ import annotations

try:
    from tools.gijiroku.scrapers.kami_city_pdf import main
except ModuleNotFoundError:
    from kami_city_pdf import main


if __name__ == "__main__":
    raise SystemExit(main())
