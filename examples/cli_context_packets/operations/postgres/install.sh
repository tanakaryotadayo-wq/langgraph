#!/usr/bin/env bash
set -euo pipefail
PG_SETUP_ROOT=/workspace/setup/postgres
PG_PACKAGE_ROOT=/workspace/.local/postgresql
PG_APT_ROOT="$PG_SETUP_ROOT/apt"
mkdir -p "$PG_APT_ROOT"/{lists/partial,archives/partial,parts,packages} "$PG_PACKAGE_ROOT"
test -r /usr/share/keyrings/debian-archive-keyring.gpg
cat > "$PG_APT_ROOT/sources.list" <<'SOURCES'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian-security trixie-security main
SOURCES
cat > "$PG_APT_ROOT/apt.conf" <<CONFIG
Dir::Etc::Parts "$PG_APT_ROOT/parts";
Dir::Etc::main "$PG_APT_ROOT/empty.conf";
Dir::Etc::sourcelist "$PG_APT_ROOT/sources.list";
Dir::Etc::sourceparts "-";
Dir::State::Lists "$PG_APT_ROOT/lists";
Dir::Cache::Archives "$PG_APT_ROOT/archives";
APT::Sandbox::User "$(id -un)";
Acquire::Retries "2";
CONFIG
touch "$PG_APT_ROOT/empty.conf"
export APT_CONFIG="$PG_APT_ROOT/apt.conf"
PG_APT_ARGS=()
if [ -n "${HTTPS_PROXY:-}" ]; then PG_APT_ARGS+=(-o "Acquire::https::Proxy=$HTTPS_PROXY"); fi
if [ -n "${HTTP_PROXY:-}" ]; then PG_APT_ARGS+=(-o "Acquire::http::Proxy=$HTTP_PROXY"); fi
/usr/bin/apt-get "${PG_APT_ARGS[@]}" update
cd "$PG_APT_ROOT/packages"
/usr/bin/apt-get "${PG_APT_ARGS[@]}" download postgresql-17 postgresql-client-17 libpq5
for package in ./*.deb; do
  dpkg-deb --extract "$package" "$PG_PACKAGE_ROOT"
done
"$PG_PACKAGE_ROOT/usr/lib/postgresql/17/bin/postgres" --version
"$PG_PACKAGE_ROOT/usr/lib/postgresql/17/bin/psql" --version
sha256sum ./*.deb > "$PG_SETUP_ROOT/package-sha256.txt"
