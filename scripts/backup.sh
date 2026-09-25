#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
backup="backups/planner-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U planner -d planner -Fc > "$backup"
echo "Backup saved to $backup"
