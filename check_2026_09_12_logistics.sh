#!/bin/sh
# Ищем "Услуги доставки" — эталон за 12.09.2026 из файла "Начисления": -36382.00.
set -e
docker compose exec app python backend/scripts/check_realization_by_day_revenue_field.py \
    --file /tmp/realization_by_day_2026-09-12.json \
    --logistics-ref -36382.00
