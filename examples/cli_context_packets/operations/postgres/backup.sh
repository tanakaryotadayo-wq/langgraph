#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/activate.sh"
PG_BACKUP_ROOT=/workspace/shared/chatgpt1027
mkdir -p "$PG_BACKUP_ROOT"
chmod 700 "$PG_BACKUP_ROOT"
PG_BACKUP_FILE="$PG_BACKUP_ROOT/chatgpt1027-$(date -u +%Y%m%dT%H%M%SZ).dump"
umask 077
pg_dump --format=custom --file="$PG_BACKUP_FILE" "$PGDATABASE"
sha256sum "$PG_BACKUP_FILE" > "$PG_BACKUP_FILE.sha256"
pg_restore --list "$PG_BACKUP_FILE" > "$PG_BACKUP_FILE.contents.txt"
printf '%s\n' "$PG_BACKUP_FILE"
