#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

image_name="${SCRAPER_IMAGE_NAME:-miyabe-tools-scraper}"
shared_data_dir="${SHARED_DATA_DIR:-/mnt/big/miyabe-tools}"

# 常駐 service 運用へ移行したため、compose がある環境で旧スクリプトを併用すると
# random 名の docker run が二重起動し、scrape_state.json や task JSON を壊しやすい。
if [ -f ./docker-compose.scraping.yml ]; then
  echo "[ERROR] docker-compose.scraping.yml がある環境では run_reiki_remote.sh を使わず、scraper-reiki サービスを再起動してください。" >&2
  exit 1
fi

mkdir -p "${shared_data_dir}/reiki"

python_runner="docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/workspace -v ${shared_data_dir}/reiki:/workspace/data/reiki -w /workspace ${image_name} python"
php_runner="docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/workspace -v ${shared_data_dir}/reiki:/workspace/data/reiki -w /workspace ${image_name} php"

exec python3 tools/reiki/scrape_all_reiki.py \
  --python-command "${python_runner}" \
  --php-command "${php_runner}" \
  "$@"
