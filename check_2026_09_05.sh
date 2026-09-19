#!/bin/sh
# Эталонные суммы за 05.09.2026 взяты из ранее присланного файла
# "Начисления" (01.09-19.09.2026), группы услуг за этот день:
# Продажи 707842.00, Возвраты -15300.00, Вознаграждение Ozon -354164.49.
set -e
docker compose exec app python backend/scripts/check_realization_by_day_revenue_field.py \
    --file /tmp/realization_by_day_2026-09-05.json \
    --sales-ref 707842.00 \
    --returns-ref -15300.00 \
    --commission-ref -354164.49
