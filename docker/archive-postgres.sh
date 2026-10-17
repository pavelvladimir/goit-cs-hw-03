#!/bin/bash
# Stream an application-schema archive. Restore only into an empty target schema.
set -euo pipefail
mode=${1:-}
if [[ $mode != backup && $mode != restore && $mode != info ]]; then
    echo 'Usage: archive-postgres.sh backup|restore|info' >&2
    exit 2
fi
export PGPASSWORD
PGPASSWORD=$(cat -- "$PG_APP_PASSWORD_FILE")
connection=(-h 127.0.0.1 -U "$PG_APP_USER" -d "$POSTGRES_DB")
if [[ $mode == info ]]; then
    exec psql -X -qAt -v ON_ERROR_STOP=1 "${connection[@]}" -c \
        "SELECT json_build_object('database', current_database(), 'server_version', current_setting('server_version'))"
fi
if [[ $mode == backup ]]; then
    exec pg_dump "${connection[@]}" --format=custom --table='public.*' --no-owner --no-privileges
fi
objects=$(psql -X -qAt -v ON_ERROR_STOP=1 "${connection[@]}" -c \
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'")
if [[ $objects != 0 ]]; then
    echo 'Restore refused: target public schema is not empty.' >&2
    exit 1
fi
# Roles and database-level permissions come from the target bootstrap, not the dump.
exec pg_restore "${connection[@]}" --exit-on-error --single-transaction --no-owner --no-privileges
