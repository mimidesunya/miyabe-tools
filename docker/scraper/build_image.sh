#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

image_name="${SCRAPER_IMAGE_NAME:-miyabe-tools-scraper}"

docker build -t "${image_name}" -f docker/scraper/Dockerfile .
