CREATE SCHEMA IF NOT EXISTS continuity;

CREATE TABLE IF NOT EXISTS continuity.entries (
    content_sha256 text PRIMARY KEY CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    record_key text NOT NULL,
    kind text NOT NULL,
    provenance text NOT NULL,
    verification_state text NOT NULL CHECK (
        verification_state IN ('user_stated','stored_artifact','live_verified','unverified')
    ),
    body jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS entries_latest
    ON continuity.entries (record_key, recorded_at DESC);

CREATE TABLE IF NOT EXISTS continuity.read_receipts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    read_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    consumer text NOT NULL,
    entry_sha256s jsonb NOT NULL
);
