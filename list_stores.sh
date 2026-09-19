#!/bin/sh
set -e
docker compose exec app python backend/scripts/list_stores.py
