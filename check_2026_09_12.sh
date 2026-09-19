#!/bin/sh
# Короткая обёртка для проверки сумм за 12.09.2026 (запускать СРАЗУ после
# probe_2026_09_12.sh, без пересборки между ними — иначе временный файл
# снова пропадёт).
set -e
docker compose exec app python backend/scripts/check_realization_by_day_revenue_field.py \
    --file /tmp/realization_by_day_2026-09-12.json
