FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        php-cli \
        php-curl \
        php-mbstring \
        php-xml \
        php-sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/scraper/requirements.txt /tmp/scraper-requirements.txt
RUN pip install --no-cache-dir -r /tmp/scraper-requirements.txt

WORKDIR /workspace
