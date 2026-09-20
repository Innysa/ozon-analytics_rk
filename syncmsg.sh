#!/bin/sh
set -e
docker compose exec app python backend/scripts/show_last_order_sync_message.py \
    --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \
    --last 3
