# 事務事業評価の AI による書き写し（tools/hyoka/ai/run_ai.py）を動かす容器。
# 取得・索引づくりの容器（docker/scraper）とは分け、PDF の表を読む PyMuPDF だけを入れる。
# OpenAI との通信は標準ライブラリで行う。使い方は doc/hyoka-ai-extraction.md。
FROM python:3.12-slim

# PyMuPDF は AGPL-3.0。この容器の中で、取得した PDF を読むためだけに使う。
RUN pip install --no-cache-dir pymupdf==1.26.4

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
