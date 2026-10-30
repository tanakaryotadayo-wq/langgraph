#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/activate.sh"
psql -X -At -v ON_ERROR_STOP=1 -v "record_key=${1:-}" <<'SQL'
WITH latest AS MATERIALIZED (
  SELECT DISTINCT ON (record_key) * FROM continuity.entries
  ORDER BY record_key,recorded_at DESC,content_sha256 DESC
), selected AS MATERIALIZED (
  SELECT * FROM latest
  WHERE CASE WHEN :'record_key' = '' THEN kind <> 'artifact'
             ELSE record_key = :'record_key' END
), receipt AS (
  INSERT INTO continuity.read_receipts(consumer,entry_sha256s)
  SELECT 'context-read/psql',coalesce(jsonb_agg(content_sha256),'[]'::jsonb) FROM selected
  RETURNING id,read_at
)
SELECT jsonb_build_object('read_receipt',(SELECT to_jsonb(receipt) FROM receipt),
                         'records',coalesce((SELECT jsonb_agg(to_jsonb(selected)) FROM selected),'[]'::jsonb));
SQL
