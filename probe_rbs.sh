#!/bin/sh
set -e
docker compose exec app python backend/scripts/probe_rating_by_sku.py \
    --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \
    --sku 2953864771 \
    --sku 1492106823
