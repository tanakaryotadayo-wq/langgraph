"""Verify source packet manifests, and preserve full run evidence without summaries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .transport import ROOT


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_packets() -> dict:
    result = {}
    for packet in sorted((ROOT / "packets").iterdir()):
        manifest = json.loads((packet / "manifest.json").read_text())
        expected = manifest.pop("manifest_sha256")
        # Source export uses JSON's default ensure_ascii=True.
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(encoded).hexdigest() != expected:
            raise ValueError(f"Manifest changed: {packet.name}")
        actual = set()
        for base, dirs, files in os.walk(packet / "source", followlinks=False):
            for name in dirs + files:
                candidate = Path(base) / name
                if candidate.is_symlink() or candidate.is_file():
                    actual.add(candidate.relative_to(packet / "source").as_posix())
        extra = actual - set(manifest["files"])
        if extra:
            raise ValueError(f"Unrecorded source files in {packet.name}: {sorted(extra)[:10]}")
        for relative, entry in manifest["files"].items():
            file = packet / "source" / relative
            if "symlink" in entry:
                if not file.is_symlink() or os.readlink(file) != entry["symlink"]:
                    raise ValueError(f"Symlink changed: {packet.name}/{relative}")
            elif (
                file.is_symlink()
                or not file.is_file()
                or hashlib.sha256(file.read_bytes()).hexdigest() != entry["sha256"]
                or bool(file.stat().st_mode & 0o111) != entry["executable"]
            ):
                raise ValueError(f"Source changed: {packet.name}/{relative}")
        result[packet.name] = {
            "commit": manifest["commit"],
            "files": len(manifest["files"]),
            "manifest_sha256": expected,
        }
    return result


def write_run(value: dict, directory: Path | None = None) -> Path:
    """Content-addressed result artifact; never overwrite a different run."""
    data = canonical(value)
    directory = directory or ROOT / "var/runs"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (hashlib.sha256(data).hexdigest() + ".json")
    try:
        with target.open("xb") as output:
            output.write(data + b"\n")
    except FileExistsError:
        if target.read_bytes() != data + b"\n":
            raise ValueError("Existing run artifact differs")
    return target
