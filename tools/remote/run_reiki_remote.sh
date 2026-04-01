#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

image_name="${SCRAPER_IMAGE_NAME:-miyabe-tools-scraper}"
shared_data_dir="${SHARED_DATA_DIR:-/mnt/big/miyabe-tools}"

mkdir -p "${shared_data_dir}/reiki"

python_runner="docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/workspace -v ${shared_data_dir}/reiki:/workspace/data/reiki -w /workspace ${image_name} python"
php_runner="docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/workspace -v ${shared_data_dir}/reiki:/workspace/data/reiki -w /workspace ${image_name} php"

exec python3 tools/reiki/scrape_all_reiki.py \
  --python-command "${python_runner}" \
  --php-command "${php_runner}" \
  "$@"
