#!/bin/bash
set -Eeuo pipefail

if (( $# > 1 )) || [[ ${1:-} != '' && ${1:-} != --rotate-app-password ]]; then
    echo "Usage: init-postgres.sh [--rotate-app-password]" >&2
    exit 1
fi
if [[ ! "$PG_APP_USER" =~ ^[a-z_][a-z0-9_]{0,62}$ ||
      "$PG_APP_USER" == postgres || "$PG_APP_USER" == public ||
      "$PG_APP_USER" == none || "$PG_APP_USER" == pg_* ]]; then
    echo "Invalid PostgreSQL application role." >&2
    exit 1
fi
PG_APP_PASSWORD="$(cat -- "$PG_APP_PASSWORD_FILE")"
export PG_APP_PASSWORD
PGPASSWORD="$(cat -- "${POSTGRES_PASSWORD_FILE:-/run/secrets/pg_admin_password}")"
export PGPASSWORD
if [[ -z "$PG_APP_PASSWORD" || "$PG_APP_PASSWORD" == "$PGPASSWORD" || "$PG_APP_PASSWORD" == *$'\n'* || "$PG_APP_PASSWORD" == *$'\r'* ]]; then
    echo "Application password must be nonempty and distinct." >&2
    exit 1
fi
if [[ ${1:-} == --rotate-app-password ]]; then
    # Explicit maintenance operation; authenticate with the current admin secret.
    psql -X -q -h 127.0.0.1 --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_user PG_APP_USER
\getenv app_password PG_APP_PASSWORD
BEGIN;
SET LOCAL log_statement = 'none';
SET LOCAL log_min_error_statement = 'panic';
ALTER ROLE :"app_user" PASSWORD :'app_password';
COMMIT;
SQL
    unset PG_APP_PASSWORD PGPASSWORD
    exit 0
fi
# Read credentials inside psql; never interpolate them into SQL or argv.
psql -X -q --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_user PG_APP_USER
\getenv app_password PG_APP_PASSWORD
\getenv db_name POSTGRES_DB
BEGIN;
SET LOCAL log_statement = 'none';
SET LOCAL log_min_error_statement = 'panic';
CREATE ROLE :"app_user" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'app_password';
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
COMMIT;
SQL
unset PG_APP_PASSWORD PGPASSWORD
