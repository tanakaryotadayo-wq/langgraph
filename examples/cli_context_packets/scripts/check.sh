#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.cache/uv}"
export CARGO_HOME="${CARGO_HOME:-/workspace/.cache/cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-/workspace/.local/share/rustup}"
export PATH="$CARGO_HOME/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff check context_packets tests scripts
uv run --frozen ruff format --check context_packets tests scripts
uv run --frozen pytest -q
./node_modules/.bin/vitest run --config vitest.config.ts
PYTHONPATH=packets/aider/source uv run --frozen pytest -c pyproject.toml packets/aider/source/tests/basic/test_special.py -q
cargo +1.95.0 test --locked --manifest-path rust/Cargo.toml
uv run --frozen context-packets --verify
