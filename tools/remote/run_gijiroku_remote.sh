#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

image_name="${SCRAPER_IMAGE_NAME:-miyabe-tools-scraper}"
shared_data_dir="${SHARED_DATA_DIR:-/mnt/big/miyabe-tools}"

mkdir -p "${shared_data_dir}/gijiroku"

python_runner="docker run --rm --ipc=host --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/workspace -v ${shared_data_dir}/gijiroku:/workspace/data/gijiroku -w /workspace ${image_name} python"

exec python3 tools/gijiroku/scrape_all_minutes.py \
  --python-command "${python_runner}" \
  "$@"
