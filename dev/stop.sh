#!/usr/bin/env bash
DEV="$(cd "$(dirname "$0")" && pwd)"; RUN="$DEV/.run"
for n in devserver postgrest auth; do
  [ -f "$RUN/$n.pid" ] && kill "$(cat "$RUN/$n.pid")" 2>/dev/null; rm -f "$RUN/$n.pid"
done
PGBIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)"
if [ -d "$RUN/pgdata" ]; then
  if [ "$(id -u)" = 0 ]; then runuser -u postgres -- "$PGBIN/pg_ctl" -D "$RUN/pgdata" -m fast stop >/dev/null 2>&1
  else "$PGBIN/pg_ctl" -D "$RUN/pgdata" -m fast stop >/dev/null 2>&1; fi
fi
echo "Stopped"
