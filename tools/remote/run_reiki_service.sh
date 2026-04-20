#!/bin/sh
set -eu

# Web 側の www-data でも SQLite を開けるよう、shared data は group writable で作る。
umask 0002

cd "$(dirname "$0")/../.."

loop_seconds="${SCRAPER_LOOP_SECONDS:-21600}"
fail_sleep_seconds="${SCRAPER_FAIL_SLEEP_SECONDS:-900}"

while true; do
  started_at="$(date -Iseconds)"
  echo "[INFO] reiki scrape cycle started at ${started_at}"
  echo "[INFO] checking missing ordinances.sqlite rows"
  if python3 tools/reiki/build_missing_ordinance_indexes.py; then
    echo "[INFO] ordinances.sqlite backfill is up to date"
  else
    status="$?"
    echo "[WARN] ordinance index backfill failed with status=${status}; continuing scrape cycle"
  fi
  if python3 tools/reiki/scrape_all_reiki.py "$@"; then
    finished_at="$(date -Iseconds)"
    echo "[INFO] reiki scrape cycle finished at ${finished_at}; sleeping ${loop_seconds}s"
    sleep "${loop_seconds}"
  else
    status="$?"
    failed_at="$(date -Iseconds)"
    echo "[WARN] reiki scrape cycle failed at ${failed_at} with status=${status}; retrying in ${fail_sleep_seconds}s"
    sleep "${fail_sleep_seconds}"
  fi
done
