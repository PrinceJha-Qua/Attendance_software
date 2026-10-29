#!/bin/sh
# Nightly Postgres backup, 14 days kept.  crontab:  15 2 * * *  /opt/gatekeeper/deploy/backup.sh
# Backups contain encrypted photos and face vectors: keep them on the same India-region VM/volume, mode 600, and copy off-box only within India.
set -eu
cd "$(dirname "$0")"
DIR="${BACKUP_DIR:-/var/backups/gatekeeper}"; mkdir -p "$DIR"; umask 077
docker compose exec -T db pg_dump -U gk gk | gzip > "$DIR/gk-$(date +%F).sql.gz"
find "$DIR" -name 'gk-*.sql.gz' -mtime +14 -delete
docker compose exec -T backend python scripts/retention.py
