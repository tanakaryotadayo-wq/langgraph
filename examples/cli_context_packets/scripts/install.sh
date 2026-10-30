#!/usr/bin/env bash
set -euo pipefail
PACKET_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.cache/uv}"
export npm_config_cache="${npm_config_cache:-/workspace/.cache/npm}"
export CARGO_HOME="${CARGO_HOME:-/workspace/.cache/cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-/workspace/.local/share/rustup}"
export PYTHONDONTWRITEBYTECODE=1
export PATH="$CARGO_HOME/bin:$PATH"
cd "$PACKET_ROOT"
if ! rustup toolchain list 2>/dev/null | rg -q '^1\.95\.0'; then
  BOOTSTRAP_DIR="$(mktemp -d /tmp/context-rust-bootstrap.XXXXXX)"
  (
    cd "$BOOTSTRAP_DIR"
    curl --fail --silent --show-error https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init -o rustup-init
    curl --fail --silent --show-error https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init.sha256 -o rustup-init.sha256
    sha256sum --check rustup-init.sha256
    chmod +x rustup-init
    ./rustup-init -y --profile minimal --default-toolchain 1.95.0 --no-modify-path
  )
fi
uv sync --frozen
npm ci --no-audit --no-fund
npm run build
cargo +1.95.0 build --locked --manifest-path rust/Cargo.toml
uv run --frozen context-packets --verify
