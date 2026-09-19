#!/bin/sh
set -e
docker compose exec app python backend/scripts/probe_product_stock_detail.py \
    --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \
    --sku 5106273621 \
    --sku 4844091135
