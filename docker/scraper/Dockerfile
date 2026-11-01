# 土台を上げたら requirements.txt の playwright の版も同じにする（同梱のブラウザと組になる）。
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        php-cli \
        php-curl \
        php-mbstring \
        php-xml \
        php-sqlite3 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/scraper/requirements.txt /tmp/scraper-requirements.txt
RUN pip install --no-cache-dir -r /tmp/scraper-requirements.txt

WORKDIR /workspace
