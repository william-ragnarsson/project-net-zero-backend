#!/usr/bin/env bash
# Start or stop the Postgres that keeps run history: `make db`, `make db-stop`.
# Uses Docker or Podman when one is running (compose.yaml). Otherwise it runs a local cluster in
# .netzero-db/ with the Postgres on PATH (brew install postgresql@17). Either way it listens on
# 127.0.0.1:54320 as netzero/netzero, the address in .env.example.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=54320
DATA=.netzero-db
URL="postgresql://netzero:netzero@127.0.0.1:$PORT/netzero"
export LANG=C LC_ALL=C  # initdb and postgres reject some macOS locales
export PGPASSWORD=netzero

engine() {
  for e in docker podman; do
    if command -v "$e" >/dev/null 2>&1 && "$e" info >/dev/null 2>&1; then
      echo "$e"
      return
    fi
  done
}

local_up() {
  if [ ! -f "$DATA/PG_VERSION" ]; then
    initdb -D "$DATA" -U netzero -E UTF8 --locale=C --auth=scram-sha-256 --pwfile=<(echo netzero) >/dev/null
  fi
  if ! pg_ctl -D "$DATA" status >/dev/null 2>&1; then
    pg_ctl -D "$DATA" -l "$DATA/server.log" -w -o "-p $PORT -c listen_addresses=127.0.0.1 -k ''" start >/dev/null
  fi
  if ! psql -h 127.0.0.1 -p "$PORT" -U netzero -d postgres -tAc \
    "select 1 from pg_database where datname = 'netzero'" | grep -q 1; then
    createdb -h 127.0.0.1 -p "$PORT" -U netzero netzero
  fi
}

case "${1:-}" in
  up)
    e=$(engine)
    if [ -n "$e" ]; then
      "$e" compose up -d --wait db
    elif command -v pg_ctl >/dev/null 2>&1; then
      local_up
    else
      echo "make db needs Docker, Podman or a local Postgres (brew install postgresql@17)." >&2
      exit 1
    fi
    echo "Postgres is up. Put this in .env, then restart the server:"
    echo "NETZERO_DATABASE_URL=$URL"
    ;;
  down)
    if [ -f "$DATA/postmaster.pid" ]; then
      pg_ctl -D "$DATA" -w stop >/dev/null
    else
      e=$(engine)
      if [ -n "$e" ]; then "$e" compose stop db; fi
    fi
    echo "Postgres stopped. The data is kept."
    ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 2
    ;;
esac
