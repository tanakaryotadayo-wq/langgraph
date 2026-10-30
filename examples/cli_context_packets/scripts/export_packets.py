"""Export complete tracked upstream trees, with byte-level provenance."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "aider": "Aider-AI--aider",
    "qwen": "QwenLM--qwen-code",
    "continue": "continuedev--continue",
    "codex": "openai--codex",
}


def digest_tree(root: Path) -> dict[str, dict]:
    entries = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            p = Path(base) / name
            key = p.relative_to(root).as_posix()
            if p.is_symlink():
                entries[key] = {"symlink": os.readlink(p)}
            elif p.is_file():
                entries[key] = {
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "executable": bool(p.stat().st_mode & 0o111),
                }
    return entries


def align_git_blobs(repository: Path, target: Path, commit: str) -> list[str]:
    """git archive can apply eol attributes; retain the exact committed bytes."""
    changed = []
    tree = subprocess.check_output(["git", "-C", str(repository), "ls-tree", "-rz", commit])
    expected = set()
    for row in tree.split(b"\0"):
        if not row:
            continue
        metadata, filename = row.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        relative = filename.decode()
        expected.add(relative)
        if kind != "blob":
            raise ValueError(f"Unexported Git entry: {relative}")
        path = target / relative
        if path.is_symlink() != (mode == "120000"):
            raise ValueError(f"Git file type differs: {relative}")
        if not path.is_symlink() and bool(path.stat().st_mode & 0o111) != (mode == "100755"):
            raise ValueError(f"Git executable mode differs: {relative}")
        payload = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        if actual != object_id:
            if path.is_symlink():
                raise ValueError(f"Unexpected symlink conversion: {relative}")
            raw = subprocess.check_output(
                ["git", "-C", str(repository), "cat-file", "blob", object_id]
            )
            path.write_bytes(raw)
            changed.append(relative)
    if expected != set(digest_tree(target)):
        raise ValueError("Exported path set does not match the full Git tree")
    return changed


def main() -> None:
    for name, directory in SOURCES.items():
        source = Path("/workspace/cli-agents") / directory
        packet = ROOT / "packets" / name
        target = packet / "source"
        if target.exists():
            raise SystemExit(f"Refusing to overwrite existing packet: {target}")
        packet.mkdir(parents=True, exist_ok=True)
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        with tempfile.TemporaryFile() as archive:
            subprocess.run(
                ["git", "-C", str(source), "archive", "--format=tar", commit],
                stdout=archive,
                check=True,
            )
            archive.seek(0)
            with tarfile.open(fileobj=archive) as tree:
                tree.extractall(target, filter="data")
        align_git_blobs(source, target, commit)
        files = digest_tree(target)
        manifest = {
            "schema": "context-source-packet.v1",
            "upstream": "https://github.com/" + directory.replace("--", "/", 1),
            "commit": commit,
            "license": "Apache-2.0",
            "scope": "complete Git HEAD tree, raw Git blob bytes; no source/test/dependency files omitted",
            "files": files,
        }
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
        (packet / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(name, commit, len(files), flush=True)


if __name__ == "__main__":
    main()
