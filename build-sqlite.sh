#!/usr/bin/env bash
#
# Build the SQLite DB from the committed SQL dump, without re-running the full
# extraction (which needs the Stellaris game files). Useful on a server: pull the
# repo, run this, and you have sqlite/stellaris.sqlite for the planner.
#
# The dump (sql/stellaris.sql) is self-contained — it drops + recreates the whole
# schema, then inserts the data — so this always produces a clean DB.
#
# Usage:
#   ./build-sqlite.sh [SQL_FILE] [SQLITE_FILE]
#
# Defaults: sql/stellaris.sql -> sqlite/stellaris.sqlite
set -euo pipefail

cd "$(dirname "$0")"

SQL_FILE="${1:-sql/stellaris.sql}"
DB_FILE="${2:-sqlite/stellaris.sqlite}"

if ! command -v sqlite3 >/dev/null 2>&1; then
	echo "error: sqlite3 not found. Install it (Fedora: sudo dnf install -y sqlite)." >&2
	exit 1
fi
if [[ ! -f "$SQL_FILE" ]]; then
	echo "error: SQL dump not found: $SQL_FILE" >&2
	exit 1
fi

mkdir -p "$(dirname "$DB_FILE")"
# Build a fresh file beside the live one, then swap it in: tables removed from the schema
# don't linger as orphans, and a running planner never sees a missing or half-built DB.
TMP_FILE="$DB_FILE.building"
rm -f "$TMP_FILE"
trap 'rm -f "$TMP_FILE"' EXIT

echo "==> Building $DB_FILE from $SQL_FILE"
# One transaction, no journal, no fsync: the dump has ~24k INSERTs and no BEGIN/COMMIT,
# so sqlite3 would otherwise commit (and fsync) each one — minutes on a slow disk
# (540 s vs 0.5 s measured locally). Safe because a failed build is thrown away.
# -bail makes any SQL error fatal (non-zero exit) instead of being skipped.
{
	echo 'PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; BEGIN;'
	cat "$SQL_FILE"
	echo 'COMMIT;'
} | sqlite3 -bail "$TMP_FILE" > /dev/null
mv -f "$TMP_FILE" "$DB_FILE"

tables=$(sqlite3 "$DB_FILE" "SELECT count(*) FROM sqlite_master WHERE type='table';")
echo "==> Done: $DB_FILE ($(du -h "$DB_FILE" | cut -f1), $tables tables)"
