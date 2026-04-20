#!/bin/sh
set -eu

# Web 側の www-data でも SQLite を開けるよう、shared data は group writable で作る。
umask 0002

cd "$(dirname "$0")/../.."

loop_seconds="${SCRAPER_LOOP_SECONDS:-21600}"
fail_sleep_seconds="${SCRAPER_FAIL_SLEEP_SECONDS:-900}"

while true; do
  started_at="$(date -Iseconds)"
  echo "[INFO] gijiroku scrape cycle started at ${started_at}"
  echo "[INFO] checking missing minutes.sqlite rows"
  if python3 tools/gijiroku/build_missing_minutes_indexes.py; then
    echo "[INFO] minutes.sqlite backfill is up to date"
  else
    status="$?"
    echo "[WARN] missing index backfill failed with status=${status}; continuing scrape cycle"
  fi
  if python3 tools/gijiroku/scrape_all_minutes.py "$@"; then
    finished_at="$(date -Iseconds)"
    echo "[INFO] gijiroku scrape cycle finished at ${finished_at}; sleeping ${loop_seconds}s"
    sleep "${loop_seconds}"
  else
    status="$?"
    failed_at="$(date -Iseconds)"
    echo "[WARN] gijiroku scrape cycle failed at ${failed_at} with status=${status}; retrying in ${fail_sleep_seconds}s"
    sleep "${fail_sleep_seconds}"
  fi
done
