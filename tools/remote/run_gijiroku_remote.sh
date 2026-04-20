#!/bin/sh
set -eu

# 旧単発実行でも shared data の新規作成物が group writable になるよう揃える。
umask 0002

cd "$(dirname "$0")/../.."

image_name="${SCRAPER_IMAGE_NAME:-miyabe-tools-scraper}"
shared_data_dir="${SHARED_DATA_DIR:-/mnt/big/miyabe-tools}"

# 常駐 service 運用へ移行したため、compose がある環境で旧スクリプトを併用すると
# random 名の docker run が二重起動し、scrape_state.json や task JSON を壊しやすい。
if [ -f ./docker-compose.scraping.yml ]; then
  echo "[ERROR] docker-compose.scraping.yml がある環境では run_gijiroku_remote.sh を使わず、scraper-gijiroku サービスを再起動してください。" >&2
  exit 1
fi

mkdir -p "${shared_data_dir}/gijiroku"

python_runner="docker run --rm --ipc=host --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/workspace -v ${shared_data_dir}/gijiroku:/workspace/data/gijiroku -w /workspace ${image_name} python"

exec python3 tools/gijiroku/scrape_all_minutes.py \
  --python-command "${python_runner}" \
  "$@"
