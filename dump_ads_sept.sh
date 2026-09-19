#!/bin/sh
set -e
docker compose exec app python backend/scripts/dump_advertising_daily_for_period.py \
    --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \
    --date-from 2026-09-01 --date-to 2026-09-19
