FROM php:8.3-fpm

ENV DEBIAN_FRONTEND=noninteractive

# apt-get upgrade: the base image lags behind Debian security fixes
# (libbrotli1 for CVE-2026-111497, 2026-10-24)
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpq-dev \
        python3 \
        python3-pip \
        python3-venv \
    && docker-php-ext-install pdo_pgsql \
    && rm -rf /var/lib/apt/lists/*

COPY ./docker/php/requirements-python.txt /tmp/requirements-python.txt
RUN python3 -m venv /opt/miyabe-python \
    && /opt/miyabe-python/bin/pip install --no-cache-dir -r /tmp/requirements-python.txt

ENV MIYABE_PYTHON_BIN=/opt/miyabe-python/bin/python

COPY ./app /var/www/html
COPY ./lib /var/www/lib
COPY ./docker/php/zz-www-overrides.conf /usr/local/etc/php-fpm.d/zz-www-overrides.conf

# Ensure permissions
RUN chown -R www-data:www-data /var/www/html /var/www/lib
