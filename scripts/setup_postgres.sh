#!/usr/bin/env bash
# Create the Postgres role and database that irl_corpus connects to.
set -euo pipefail

PGDB_USER="${PGDB_USER:-irl}"
PGDB_PASSWORD="${PGDB_PASSWORD:-irl}"
PGDB_NAME="${PGDB_NAME:-irl_corpus}"

role_exists=$(psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '${PGDB_USER}'")
if [ -n "$role_exists" ]; then
    echo "Role '${PGDB_USER}' already exists — leaving it alone."
else
    psql -v pw="$PGDB_PASSWORD" \
         -c "CREATE USER \"${PGDB_USER}\" WITH PASSWORD :'pw';"
    echo "Created role '${PGDB_USER}'."
fi

db_exists=$(psql -tAc "SELECT 1 FROM pg_database WHERE datname = '${PGDB_NAME}'")
if [ -n "$db_exists" ]; then
    echo "Database '${PGDB_NAME}' already exists — leaving it alone."
else
    psql -c "CREATE DATABASE \"${PGDB_NAME}\" OWNER \"${PGDB_USER}\";"
    echo "Created database '${PGDB_NAME}' owned by '${PGDB_USER}'."
fi

echo
echo "Connection string (substitute the password you set):"
echo "  postgresql://${PGDB_USER}:<password>@<host>:5432/${PGDB_NAME}"
echo
echo "Export it as IRL_DB_URL, then run 'irl admin init' to create the schema."
