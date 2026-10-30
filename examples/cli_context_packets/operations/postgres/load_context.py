"""Append actual records/artifact contents to PostgreSQL without API keys."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess

BIN = Path("/workspace/.local/postgresql/usr/lib/postgresql/17/bin")
SOCKET = "/tmp/chatgpt1027-postgresql-socket"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args()
    records = json.loads(args.records.read_text())
    if args.artifact_root:
        for relative in [
            "README.md",
            "NOTICE",
            "VERIFICATION.json",
            "dist/build-manifest.json",
            "packets/aider/manifest.json",
            "packets/qwen/manifest.json",
            "packets/continue/manifest.json",
            "packets/codex/manifest.json",
        ]:
            file = args.artifact_root / relative
            data = file.read_bytes()
            records.append(
                {
                    "key": "artifact/" + relative,
                    "kind": "artifact",
                    "source": str(file),
                    "status": "stored_artifact",
                    "body": {
                        "path": relative,
                        "file_sha256": hashlib.sha256(data).hexdigest(),
                        "content": data.decode("utf-8"),
                    },
                }
            )
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for item in records:
        canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        writer.writerow(
            [
                digest,
                item["key"],
                item["kind"],
                item["source"],
                item["status"],
                json.dumps(item["body"], ensure_ascii=False),
            ]
        )
    schema = Path(__file__).with_name("schema.sql").read_text()
    sql = (
        "BEGIN;\n" + schema + "\n"
        "CREATE TEMP TABLE incoming_context (LIKE continuity.entries INCLUDING DEFAULTS);\n"
        "COPY incoming_context(content_sha256,record_key,kind,provenance,verification_state,body) "
        "FROM STDIN WITH (FORMAT csv);\n" + output.getvalue() + "\\.\n"
        "INSERT INTO continuity.entries SELECT * FROM incoming_context "
        "ON CONFLICT(content_sha256) DO NOTHING;\nCOMMIT;\n"
        "SELECT kind,verification_state,count(*) FROM continuity.entries "
        "GROUP BY kind,verification_state ORDER BY kind,verification_state;\n"
    )
    subprocess.run(
        [
            str(BIN / "psql"),
            "-X",
            "-h",
            SOCKET,
            "-U",
            "agent",
            "-d",
            "chatgpt1027",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        check=True,
    )


if __name__ == "__main__":
    main()
