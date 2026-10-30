#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/activate.sh"
mkdir -p "$(dirname "$PGDATA")" "$PGHOST"
chmod 700 "$(dirname "$PGDATA")" "$PGHOST"
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  if [ -d "$PGDATA" ] && [ -n "$(ls -A "$PGDATA")" ]; then
    echo 'Refusing to initialize a nonempty PostgreSQL data directory' >&2
    exit 1
  fi
  initdb -D "$PGDATA" --username="$PGUSER" --encoding=UTF8 --locale=C.UTF-8 \
    --auth-local=peer --auth-host=scram-sha-256
  cat >> "$PGDATA/postgresql.conf" <<'CONFIG'

# Private workspace instance; no TCP listener or API key required.
listen_addresses = ''
port = 5432
unix_socket_directories = '/tmp/chatgpt1027-postgresql-socket'
unix_socket_permissions = 0700
max_connections = 20
shared_buffers = '64MB'
CONFIG
fi
if [ "$(cat "$PGDATA/PG_VERSION")" != 17 ]; then
  echo 'Existing cluster major version is not 17; refusing to change it' >&2
  exit 1
fi
PG_START_OPTIONS="-c unix_socket_directories=$PGHOST"
if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
  if ! pg_isready -h "$PGHOST" -p "$PGPORT" >/dev/null 2>&1; then
    pg_ctl -D "$PGDATA" -o "$PG_START_OPTIONS" -m fast -w -t 30 restart
  fi
else
  pg_ctl -D "$PGDATA" -o "$PG_START_OPTIONS" -l /workspace/.local/state/postgresql/server.log -w -t 30 start
fi
if [ "$(psql -X -d postgres -At -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname='chatgpt1027'")" != 1 ]; then
  createdb chatgpt1027
fi
psql -X -v ON_ERROR_STOP=1 -c 'SELECT current_database(), current_user, version();'
