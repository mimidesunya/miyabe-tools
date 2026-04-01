FROM php:8.3-fpm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY ./docker/php/requirements-python.txt /tmp/requirements-python.txt
RUN python3 -m venv /opt/miyabe-python \
    && /opt/miyabe-python/bin/pip install --no-cache-dir -r /tmp/requirements-python.txt

ENV MIYABE_PYTHON_BIN=/opt/miyabe-python/bin/python

COPY ./app /var/www/html
COPY ./lib /var/www/lib
COPY ./src /var/www/src
COPY ./docker/php/zz-www-overrides.conf /usr/local/etc/php-fpm.d/zz-www-overrides.conf

# Ensure permissions
RUN chown -R www-data:www-data /var/www/html /var/www/lib /var/www/src
