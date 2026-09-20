#!/bin/sh
# Короткая обёртка: sh fk.sh <подстрока> — ищет статью по имени во всех
# сохранённых периодах ДДС.
set -e
docker compose exec app python backend/scripts/inspect_cash_flow_periods.py \
    --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \
    --find-key "$1"
