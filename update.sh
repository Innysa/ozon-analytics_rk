#!/bin/sh
# Однокомандное обновление сервера: подтягивает последнюю версию кода из
# ветки claude/api-data-collection-auto-qrkfs0 и пересобирает приложение.
# Использование (на сервере, из корня репозитория):
#     sh update.sh
set -e
git fetch origin claude/api-data-collection-auto-qrkfs0
git reset --hard origin/claude/api-data-collection-auto-qrkfs0
docker compose build app
docker compose up -d app
echo "Готово. Текущий коммит:"
git log --oneline -1
