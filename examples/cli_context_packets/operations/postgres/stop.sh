#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/activate.sh"
pg_ctl -D "$PGDATA" -m fast -w stop
