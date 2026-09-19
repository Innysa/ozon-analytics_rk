#!/bin/sh
# Короткая обёртка, чтобы не набирать длинную команду с ID магазина в консоли.
set -e
docker compose exec app python backend/scripts/probe_realization_by_day_and_accrual_reference.py \
    --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \
    --date 2026-09-12
