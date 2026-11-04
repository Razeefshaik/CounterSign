#!/usr/bin/env bash
# C27 hosted demo: one container, demo mode only.
#
# Image build (hosted_start.sh --build-template): create PostgreSQL 16 and run
# `alembic upgrade head` into an empty template cluster with no password, so a
# boot on a 0.1 CPU host skips initdb and migrations (they cost minutes there).
#
# Boot (no arguments):
# 1. Copy the migrated template to a fresh, disposable cluster on loopback with
#    a random password.
# 2. Start the PayPal mock on loopback and register its webhook.
# 3. Serve the app and dashboard on $PORT (no background workers).
# 4. Optionally (COUNTERSIGN_HOSTED_SEED=week) play the scripted demo week once
#    against the mock (no LLM, no PayPal). Off by default so visitors can step it.
#
# Nothing here reads .env. The database password is random per boot and never
# printed. Sandbox or model credentials in the environment stop the boot.
set -Eeuo pipefail

PORT="${PORT:-8080}"
PG_PORT=55433
MOCK_PORT=8099
PG_BIN="${COUNTERSIGN_PG_BIN:-/usr/lib/postgresql/16/bin}"
TEMPLATE="${COUNTERSIGN_PG_TEMPLATE:-/home/app/pgtemplate}"
STATE="${COUNTERSIGN_HOSTED_STATE:-/tmp/countersign}"
SEED="${COUNTERSIGN_HOSTED_SEED:-none}"
START="${COUNTERSIGN_HOSTED_START:-friday}" # C62
IDLE="${COUNTERSIGN_HOSTED_IDLE_MINUTES:-20}"
FRIDAY="${COUNTERSIGN_FRIDAY_ROOT:-/home/app/friday}"
BUILD_FRIDAY=0
APP_PID="" MOCK_PID=""

log() { printf '[hosted] %s\n' "$*"; }

# C62: validate before opening a service or touching any disposable data.
case "${1:-}" in ""|--build-template|--build-friday) ;; *) log "unknown hosted argument"; exit 64 ;; esac
case "$START" in friday|monday) ;; *) log "COUNTERSIGN_HOSTED_START must be friday or monday"; exit 64 ;; esac
case "$IDLE" in *[!0-9]*|"") log "COUNTERSIGN_HOSTED_IDLE_MINUTES must be a nonnegative integer"; exit 64 ;; esac
if [ "${#IDLE}" -gt 5 ] || [ "$IDLE" -gt 10080 ]; then
  log "COUNTERSIGN_HOSTED_IDLE_MINUTES must be between 0 and 10080"; exit 64
fi
if [ "${1:-}" = --build-friday ]; then BUILD_FRIDAY=1; START=monday; SEED=none; IDLE=0; fi
# C62: a chosen Monday boot stays on Monday unless idle return is asked for.
if [ "$START" = monday ] && [ -z "${COUNTERSIGN_HOSTED_IDLE_MINUTES:-}" ]; then IDLE=0; fi
if [ "$START" = friday ] && [ "$SEED" = week ]; then
  log "COUNTERSIGN_HOSTED_SEED=week requires COUNTERSIGN_HOSTED_START=monday"; exit 64
fi

pg_start() {  # pg_start DATA SOCKET_DIR LISTEN
  "$PG_BIN/pg_ctl" -D "$1" -l "$2/postgres.log" -w -t 300 start -o \
    "-c listen_addresses=$3 -p $PG_PORT -k $2 -c max_connections=30 -c shared_buffers=32MB \
     -c fsync=off -c synchronous_commit=off -c full_page_writes=off" >/dev/null
}

if [ "${1:-}" = "--build-template" ]; then
  # Local socket trust is limited to this user (socket directory mode 700);
  # TCP always requires the per-boot password.
  build="$(mktemp -d)"
  "$PG_BIN/initdb" -D "$TEMPLATE" -U countersign --auth-local=trust \
    --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8 --no-sync >/dev/null
  pg_start "$TEMPLATE" "$build" "''"
  "$PG_BIN/createdb" -h "$build" -p "$PG_PORT" -U countersign countersign_demo
  DATABASE_URL="postgresql://countersign@/countersign_demo?host=$build&port=$PG_PORT" \
    alembic -c migrations/alembic.ini upgrade head
  "$PG_BIN/pg_ctl" -D "$TEMPLATE" -m fast -w stop >/dev/null
  rm -rf "$build" "$TEMPLATE/postmaster.opts"
  exit 0
fi

for name in PAYPAL_CLIENT_ID PAYPAL_CLIENT_SECRET GOOGLE_API_KEY; do
  if [ -n "${!name:-}" ]; then
    log "refusing to start: $name is set; the hosted demo never holds sandbox or model credentials"
    exit 64
  fi
done
case "$SEED" in week|none) ;; *) log "COUNTERSIGN_HOSTED_SEED must be week or none"; exit 64 ;; esac
case "$PORT" in "$MOCK_PORT"|"$PG_PORT")
  log "PORT must differ from the internal ports $MOCK_PORT and $PG_PORT"; exit 64 ;;
esac

# Forced on every boot: platform variables cannot switch this instance to live.
export COUNTERSIGN_MODE=demo
export PAYPAL_BASE_URL="http://127.0.0.1:$MOCK_PORT"
export MOCK_PAYPAL_CLIENT_ID=mock-client
export MOCK_PAYPAL_CLIENT_SECRET=mock-secret
# M15: the dashboard reads the engine's own records, not contract fixtures.
export COUNTERSIGN_DASHBOARD_SOURCE=database
export COUNTERSIGN_HOSTED_START="$START" COUNTERSIGN_HOSTED_IDLE_MINUTES="$IDLE" # C62
export COUNTERSIGN_FRIDAY_ROOT="$FRIDAY" COUNTERSIGN_FRIDAY_BUILD="$BUILD_FRIDAY"
unset PAYPAL_WEBHOOK_ID COUNTERSIGN_LLM_PROVIDER COUNTERSIGN_LLM_MODEL

cleanup() {
  trap - EXIT
  [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null || true
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null || true
  "$PG_BIN/pg_ctl" -D "$STATE/pgdata" -m fast -w stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 143' TERM INT

wait_for_port() {  # wait_for_port PORT SECONDS; bash /dev/tcp costs no process start
  local i
  for ((i = 0; i < $2 * 4; i++)); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; then return 0; fi
    sleep 0.25
  done
  return 1
}

# 1. Fresh cluster from the migrated template, loopback only, random password.
rm -rf "$STATE"
mkdir -p "$STATE"
chmod 700 "$STATE"
log "starting PostgreSQL 16"
# C62: Friday is a cleanly stopped physical clone, with the same per-boot password reset.
SOURCE="$TEMPLATE"
if [ "$START" = friday ]; then SOURCE="$FRIDAY/pgdata"; fi
[ -d "$SOURCE" ] || { log "hosted PostgreSQL template is missing"; exit 1; }
cp -a "$SOURCE" "$STATE/pgdata"
chmod 700 "$STATE/pgdata" # C62: retain PostgreSQL's requirement across host filesystems.
pg_start "$STATE/pgdata" "$STATE" 127.0.0.1
DB_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
printf "ALTER ROLE countersign PASSWORD '%s';\n" "$DB_PASSWORD" \
  | "$PG_BIN/psql" -h "$STATE" -p "$PG_PORT" -U countersign -d postgres -q -v ON_ERROR_STOP=1
export DATABASE_URL="postgresql://countersign:$DB_PASSWORD@127.0.0.1:$PG_PORT/countersign_demo"
unset DB_PASSWORD
# C62: the build and runtime share the exact week runner and callback address.
export COUNTERSIGN_DEMO_ROOT="$STATE/week" COUNTERSIGN_DEMO_APP_URL="http://127.0.0.1:$PORT"

# 2. Mock on loopback with a minimal environment (it never sees the database URL).
log "starting the PayPal mock on loopback"
env -i PATH="$PATH" HOME="${HOME:-/tmp}" PYTHONPATH="${PYTHONPATH:-}" LANG=C.UTF-8 \
  PYTHONUNBUFFERED=1 MOCK_CONTROL=1 COUNTERSIGN_MOCK_CHECKPOINT_DIR="$FRIDAY" \
  COUNTERSIGN_FRIDAY_BUILD="$BUILD_FRIDAY" COUNTERSIGN_MOCK_CHECKPOINT_BACKUP="$STATE/mock-backup" \
  COUNTERSIGN_MOCK_CALLBACK_URL="http://127.0.0.1:$PORT/webhooks/paypal" \
  python -m mock_paypal >"$STATE/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_port "$MOCK_PORT" 600 || { log "mock did not start"; exit 1; }
if [ "$START" = friday ]; then # C62: load only the image's named artifact.
  PAYPAL_WEBHOOK_ID="$(python -m scripts.friday_checkpoint boot --directory "$FRIDAY" \
    --app-url "$COUNTERSIGN_DEMO_APP_URL")" || { log "Friday setup failed"; exit 1; }
else
  REHEARSAL="$(python -m scripts.mock_rehearsal --root "$STATE/week" --app-port "$PORT" --reset)" \
  || { log "mock setup failed"; exit 1; }
PAYPAL_WEBHOOK_ID="$(printf '%s\n' "$REHEARSAL" \
  | sed -n 's/^Mock ready at .*; webhook \(WH-MOCK-[A-Za-z0-9_-]*\)$/\1/p')"
fi
[ -n "$PAYPAL_WEBHOOK_ID" ] || { log "mock webhook registration not found"; exit 1; }
export PAYPAL_WEBHOOK_ID

# 3. App and dashboard. The factory refuses anything but demo mode on the mock.
log "starting the app on port $PORT"
# M17 demo bar: the app steps this same checkpoint (one week process at a time).
export COUNTERSIGN_DEMO_ROOT="$STATE/week" COUNTERSIGN_DEMO_APP_URL="http://127.0.0.1:$PORT"
uvicorn --factory countersign.main:create_hosted_app --host 0.0.0.0 --port "$PORT" \
  --no-access-log --log-level warning &
APP_PID=$!
wait_for_port "$PORT" 600 || { log "app did not start"; exit 1; }
log "dashboard ready"

# C62: play real steps 1–6, capture logical/mock/week state, then stop PostgreSQL
# cleanly before copying the physical boot template. No service survives this RUN.
if [ "$BUILD_FRIDAY" = 1 ]; then
  python -m scripts.friday_checkpoint build --directory "$FRIDAY" \
    --app-url "$COUNTERSIGN_DEMO_APP_URL" >"$STATE/friday-build.log" 2>&1 \
    || { log "Friday checkpoint build failed"; cat "$STATE/friday-build.log"; exit 1; }
  cleanup
  cp -a "$STATE/pgdata" "$FRIDAY/pgdata"
  rm -f "$FRIDAY/pgdata/postmaster.opts"
  log "Friday checkpoint built"
  exit 0
fi

# 4. Optional scripted demo week, off by default so visitors can step it. M15
#    bounds the mock's balance history at 256 snapshots. The checkpoint resumes
#    after a transient stop. A final failure leaves the dashboard serving;
#    only the fixed error code is printed.
if [ "$SEED" = week ]; then
  log "playing the scripted demo week against the mock"
  for attempt in 1 2 3; do
    if python -m scripts.demo_week --target mock --model scripted \
      --root "$STATE/week" --app-url "http://127.0.0.1:$PORT" >>"$STATE/week.log" 2>&1; then
      log "demo week complete"
      break
    fi
    log "demo week attempt $attempt stopped: $(tail -n 1 "$STATE/week.log")"
  done
fi

# Exit (and let the platform restart the container) if the app or the mock stops.
wait -n "$APP_PID" "$MOCK_PID"
