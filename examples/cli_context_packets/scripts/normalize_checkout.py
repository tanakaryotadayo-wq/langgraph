"""Restore one upstream Git eol=crlf path to its recorded raw blob bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
relative = "scripts/installation/install-qwen-standalone.bat"
packet = root / "packets/qwen"
manifest = json.loads((packet / "manifest.json").read_text())
file = packet / "source" / relative
expected = manifest["files"][relative]["sha256"]
data = file.read_bytes()
if hashlib.sha256(data).hexdigest() != expected:
    original = data.replace(b"\r\n", b"\n")
    if hashlib.sha256(original).hexdigest() != expected:
        raise SystemExit("Upstream file changed beyond Git newline conversion: " + relative)
    file.write_bytes(original)
    print("Restored recorded Git blob line endings: " + relative)
