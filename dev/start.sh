#!/usr/bin/env bash
# Local Supabase stand-in for testing Cricle: real Postgres 16, real Supabase Auth (GoTrue) and real
# PostgREST, plus a small Node server that serves docs/ and routes /auth/v1 and /rest/v1 like Supabase.
#   dev/start.sh          start everything (creates the database on first run)
#   dev/start.sh --fresh  wipe the database first
#   dev/stop.sh           stop everything
# Needs dev/bin/postgrest and dev/bin/auth (see dev/README.txt). Site: http://localhost:8790
set -euo pipefail
DEV="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEV")"
RUN="$DEV/.run"; PGDATA="$RUN/pgdata"; PGPORT=54329
PGBIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)"
JWT_SECRET="cricle-local-dev-jwt-secret-at-least-32-characters"
export JWT_SECRET
mkdir -p "$RUN"

as_pg() { if [ "$(id -u)" = 0 ]; then runuser -u postgres -- "$@"; else "$@"; fi; }

if [ "${1:-}" = "--fresh" ]; then "$DEV/stop.sh" >/dev/null 2>&1 || true; rm -rf "$PGDATA"; fi

if [ ! -d "$PGDATA" ]; then
  echo "Creating local database"
  mkdir -p "$PGDATA"; [ "$(id -u)" = 0 ] && chown -R postgres "$RUN"
  as_pg "$PGBIN/initdb" -D "$PGDATA" -U supabase_admin --auth=trust -E UTF8 --locale=C.UTF-8 >/dev/null
  as_pg "$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k $RUN -c listen_addresses=127.0.0.1" -l "$RUN/postgres.log" -w start >/dev/null
  psql -h 127.0.0.1 -p $PGPORT -U supabase_admin -d postgres -v ON_ERROR_STOP=1 -q -f "$DEV/supabase_roles.sql"
  ( cd "$DEV/bin" && GOTRUE_DB_DRIVER=postgres \
      DATABASE_URL="postgres://supabase_auth_admin:auth@127.0.0.1:$PGPORT/postgres?search_path=auth" \
      GOTRUE_DB_DATABASE_URL="postgres://supabase_auth_admin:auth@127.0.0.1:$PGPORT/postgres?search_path=auth" \
      GOTRUE_JWT_SECRET="$JWT_SECRET" API_EXTERNAL_URL="http://localhost:8790/auth/v1" GOTRUE_SITE_URL="http://localhost:8790" \
      ./auth migrate >"$RUN/auth-migrate.log" 2>&1 ) || { echo "auth migrate failed, see $RUN/auth-migrate.log"; exit 1; }
  psql -h 127.0.0.1 -p $PGPORT -U supabase_admin -d postgres -v ON_ERROR_STOP=1 -q -c \
    "grant all on all tables in schema auth to postgres; grant all on all sequences in schema auth to postgres;"
else
  if ! as_pg "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    as_pg "$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k $RUN -c listen_addresses=127.0.0.1" -l "$RUN/postgres.log" -w start >/dev/null
  fi
fi

start_bg() {  # name, command...
  local name=$1; shift
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then return; fi
  nohup "$@" >"$RUN/$name.log" 2>&1 &
  echo $! >"$RUN/$name.pid"
}

( cd "$DEV/bin" && start_bg auth env GOTRUE_DB_DRIVER=postgres \
    DATABASE_URL="postgres://supabase_auth_admin:auth@127.0.0.1:$PGPORT/postgres?search_path=auth" \
    GOTRUE_API_HOST=127.0.0.1 PORT=9999 API_EXTERNAL_URL="http://localhost:8790/auth/v1" \
    GOTRUE_SITE_URL="http://localhost:8790" GOTRUE_URI_ALLOW_LIST="http://localhost:8790/**" \
    GOTRUE_JWT_SECRET="$JWT_SECRET" GOTRUE_JWT_EXP=3600 GOTRUE_JWT_AUD=authenticated \
    GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated GOTRUE_JWT_ADMIN_ROLES=service_role \
    GOTRUE_DISABLE_SIGNUP=false GOTRUE_EXTERNAL_EMAIL_ENABLED=true GOTRUE_MAILER_AUTOCONFIRM=true \
    GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED=true GOTRUE_RATE_LIMIT_ANONYMOUS_USERS=100000 \
    GOTRUE_RATE_LIMIT_TOKEN_REFRESH=100000 GOTRUE_RATE_LIMIT_VERIFY=100000 GOTRUE_LOG_LEVEL=warn ./auth serve )

cat >"$RUN/postgrest.conf" <<EOF
db-uri = "postgres://authenticator:authenticator@127.0.0.1:$PGPORT/postgres"
db-schemas = "public"
db-anon-role = "anon"
db-extra-search-path = "public, extensions"
db-max-rows = 1000
jwt-secret = "$JWT_SECRET"
server-host = "127.0.0.1"
server-port = 3000
admin-server-port = 3001
EOF
start_bg postgrest "$DEV/bin/postgrest" "$RUN/postgrest.conf"
start_bg devserver node "$DEV/devserver.mjs"

for i in $(seq 1 50); do
  if curl -fsS http://127.0.0.1:9999/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:3001/ready >/dev/null 2>&1 \
     && curl -sS -o /dev/null http://127.0.0.1:8790/config.js 2>/dev/null; then
    echo "Ready: http://localhost:8790  (Postgres on 127.0.0.1:$PGPORT, user postgres)"; exit 0
  fi
  sleep 0.3
done
echo "Something didn't start. Logs are in $RUN"; tail -5 "$RUN"/*.log; exit 1
