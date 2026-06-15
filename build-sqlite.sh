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
# Rebuild from scratch so tables removed from the schema don't linger as orphans.
rm -f "$DB_FILE"

echo "==> Building $DB_FILE from $SQL_FILE"
# -bail makes any SQL error fatal (non-zero exit) instead of being skipped.
sqlite3 -bail "$DB_FILE" < "$SQL_FILE"

tables=$(sqlite3 "$DB_FILE" "SELECT count(*) FROM sqlite_master WHERE type='table';")
echo "==> Done: $DB_FILE ($(du -h "$DB_FILE" | cut -f1), $tables tables)"
