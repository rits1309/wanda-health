#!/usr/bin/env bash
#
# run-local.sh - bring up a local Docker Postgres, seed it, and launch the API.
#
# Backs the API with a throwaway Postgres container (no AWS needed) and serves
# it on http://localhost:8000 - matching the Bruno "Local Docker" environment.
#
#   ./scripts/run-local.sh            # start container (if needed), seed, run API
#   ./scripts/run-local.sh --fresh    # remove any existing container first
#   ./scripts/run-local.sh --no-seed  # skip seeding (reuse existing data)
#
# Ctrl-C stops the API; the container is left running for a fast restart.
# Stop it yourself with:  docker stop summit-pg
set -euo pipefail

# RETIRED: this script targets the read-only FastAPI app removed at;
# it is kept only until the infrastructure phase decides the fate of scripts/ + infra/.
echo "run-local.sh is retired: the legacy strata.core app was removed at." >&2
echo "Local dev now: 'docker compose up -d --wait db' at the repo root, then" >&2
echo "'inv migrate && inv seed --profile demo' here. See strata.core/README.md." >&2
exit 1

# --- config ---------------------------------------------------------------
CONTAINER=summit-pg
PG_PORT=55432           # host port the container's Postgres is published on
API_PORT="${API_PORT:-8000}"
PG_PASSWORD=password
PG_USER=postgres
PG_DB=postgres

FRESH=0
SEED=1
for arg in "$@"; do
  case "$arg" in
    --fresh)   FRESH=1 ;;
    --no-seed) SEED=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."   # repo root

log() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }

# The DB connection overrides shared by the seed step and the API process.
db_env=(
  POSTGRES_HOST=localhost
  POSTGRES_PORT="$PG_PORT"
  POSTGRES_SSLMODE=disable      # local Postgres has no TLS
  POSTGRES_USER="$PG_USER"
  POSTGRES_PASSWORD="$PG_PASSWORD"
  POSTGRES_DB="$PG_DB"
)

# --- preflight ------------------------------------------------------------
command -v docker >/dev/null || { echo "docker is not installed / not on PATH" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker daemon isn't running - start Docker Desktop first." >&2; exit 1; }
[ -x .venv/bin/python ] || { echo ".venv not found - run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }

# --- container ------------------------------------------------------------
if [ "$FRESH" = 1 ] && docker ps -aq -f "name=^${CONTAINER}$" | grep -q .; then
  log "Removing existing container ($CONTAINER)..."
  docker rm -f "$CONTAINER" >/dev/null
fi

if docker ps -q -f "name=^${CONTAINER}$" | grep -q .; then
  log "Container $CONTAINER already running - reusing it."
elif docker ps -aq -f "name=^${CONTAINER}$" | grep -q .; then
  log "Starting existing container $CONTAINER..."
  docker start "$CONTAINER" >/dev/null
else
  log "Starting Postgres container $CONTAINER on localhost:$PG_PORT..."
  docker run -d --rm --name "$CONTAINER" \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" -p "$PG_PORT:5432" postgres:16-alpine >/dev/null
fi

log "Waiting for Postgres to accept connections..."
until docker exec "$CONTAINER" pg_isready -U "$PG_USER" >/dev/null 2>&1; do
  sleep 1
done

# --- seed -----------------------------------------------------------------
if [ "$SEED" = 1 ]; then
  log "Seeding database..."
  env "${db_env[@]}" .venv/bin/python -m app.seed
else
  log "Skipping seed (--no-seed)."
fi

# --- run ------------------------------------------------------------------
log "Launching API on http://localhost:$API_PORT  (Ctrl-C to stop)"
echo "    Bruno: select the 'Local Docker' environment."
exec env "${db_env[@]}" .venv/bin/uvicorn app.main:app --reload --port "$API_PORT"
