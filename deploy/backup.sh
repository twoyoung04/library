#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p deploy/backups
backup="deploy/backups/library-$(date -u +%Y%m%dT%H%M%SZ).dump"
trap 'rm -f "$backup.partial"' EXIT HUP INT TERM
docker compose exec -T db pg_dump -U library -d library -Fc > "$backup.partial"
mv "$backup.partial" "$backup"
printf '%s\n' "$backup"
