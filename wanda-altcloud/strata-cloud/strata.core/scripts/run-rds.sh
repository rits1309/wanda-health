#!/usr/bin/env bash
#
# run-rds.sh - connect to the real AWS RDS via an SSM tunnel and launch the API.
#
# The RDS instance is private-VPC-only, so this:
#   1. exports temp AWS creds (the Terraform/SSM tooling can't read the custom
#      `aws login` token cache on its own),
#   2. applies infra/terraform to create a disposable SSM tunnel host,
#   3. waits for the SSM agent, opens a port-forward to localhost:55432,
#   4. (optionally) seeds the RDS, then launches the API on http://localhost:8001
#      - matching the Bruno "Live RDS" environment.
#
# AWS creds are refreshed automatically mid-session: a background keeper
# re-exports them and reopens the tunnel shortly before they expire, so a long
# session no longer dies on lapsed credentials (and teardown still works too).
#
#   ./scripts/run-rds.sh             # connect + launch API, tear down on exit
#   ./scripts/run-rds.sh --seed      # also reseed the RDS before launching
#   ./scripts/run-rds.sh --keep      # leave the tunnel host up for fast restarts
#
# On Ctrl-C the tunnel is closed and the tunnel host is destroyed (stopping the
# ~$0.012/hr charge) unless you pass --keep. If you used --keep, tear it down
# later with:  terraform -chdir=infra/terraform destroy -auto-approve
set -euo pipefail

# --- config ---------------------------------------------------------------
REGION=eu-west-2
TF_DIR=infra/terraform
LOCAL_PORT=55432            # must match terraform var.local_port
API_PORT="${API_PORT:-8001}"
CRED_REFRESH_BUFFER=300                                 # refresh this many seconds before creds expire
CRED_REFRESH_FALLBACK="${CRED_REFRESH_INTERVAL:-1800}"  # ...or every this often if expiry is unknown

SEED=0
DESTROY=1                  # tear down the tunnel host on exit by default
for arg in "$@"; do
  case "$arg" in
    --seed) SEED=1 ;;
    --keep) DESTROY=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."   # repo root

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }

# Re-export temp AWS creds into the current shell. The Terraform AWS provider
# and the SSM plugin can't read the custom `aws login` token cache themselves.
refresh_creds() {
  eval "$(aws configure export-credentials --region "$REGION" --format env 2>/dev/null)"
}

# Seconds from now until an ISO-8601 timestamp (echoes nothing on failure).
seconds_until() {
  local ts="$1" norm exp now
  [ -n "$ts" ] || return 1
  now=$(date +%s)
  if exp=$(date -d "$ts" +%s 2>/dev/null); then           # GNU date
    echo $(( exp - now )); return 0
  fi
  norm=$(printf '%s' "$ts" | sed -E -e 's/Z$/+0000/' -e 's/([+-][0-9][0-9]):([0-9][0-9])$/\1\2/')
  if exp=$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$norm" +%s 2>/dev/null); then   # BSD/macOS date
    echo $(( exp - now )); return 0
  fi
  return 1
}

# (Re)open the SSM port-forward and wait until the local port accepts traffic.
restart_tunnel() {
  pkill -f "session-manager-plugin" 2>/dev/null || true
  sleep 1
  terraform -chdir="$TF_DIR" output -raw start_session_command | sh &
  TUNNEL_PID=$!
  until (exec 3<>"/dev/tcp/localhost/$LOCAL_PORT") 2>/dev/null; do
    kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    sleep 1
  done
  exec 3>&- 2>/dev/null || true
}

# Background loop: refresh creds and bounce the tunnel just before expiry so the
# SSM session never runs on lapsed credentials.
cred_keeper() {
  local ttl wait_for
  while true; do
    if ttl=$(seconds_until "${AWS_CREDENTIAL_EXPIRATION:-}"); then
      wait_for=$(( ttl - CRED_REFRESH_BUFFER ))
      [ "$wait_for" -lt 10 ] && wait_for=10
    else
      wait_for="$CRED_REFRESH_FALLBACK"
    fi
    sleep "$wait_for"
    refresh_creds || { warn "could not refresh AWS creds (re-run 'aws login'); will retry"; sleep 30; continue; }
    log "Refreshed AWS credentials; reopening tunnel..."
    restart_tunnel || warn "tunnel did not come back up after credential refresh"
  done
}

TUNNEL_PID=""
KEEPER_PID=""
APPLIED=0                  # set to 1 once terraform has created the host
cleanup() {
  set +e
  [ -n "$KEEPER_PID" ] && kill "$KEEPER_PID" 2>/dev/null   # stop the keeper before killing its tunnel
  pkill -f "session-manager-plugin" 2>/dev/null
  [ "$APPLIED" = 1 ] || return   # nothing was provisioned; nothing to tear down
  if [ "$DESTROY" = 1 ]; then
    refresh_creds                # creds may have expired during a long session
    log "Destroying tunnel host (terraform destroy)..."
    terraform -chdir="$TF_DIR" destroy -auto-approve
  else
    printf '\nTunnel host is still running (~$0.012/hr). Tear it down with:\n  terraform -chdir=%s destroy -auto-approve\n' "$TF_DIR"
  fi
}
trap cleanup EXIT

# --- preflight ------------------------------------------------------------
for bin in terraform aws session-manager-plugin; do
  command -v "$bin" >/dev/null || { echo "$bin is not installed / not on PATH" >&2; exit 1; }
done
[ -x .venv/bin/python ] || { echo ".venv not found - run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }
[ -f .env ] || { echo ".env not found - copy .env.example to .env and set the RDS master password." >&2; exit 1; }
if grep -q '^POSTGRES_PASSWORD=your_password' .env 2>/dev/null; then
  warn "POSTGRES_PASSWORD in .env still looks like the placeholder - the seed/API will fail auth until you set the real RDS master password."
fi

# --- AWS creds ------------------------------------------------------------
log "Exporting AWS credentials for region $REGION..."
if ! refresh_creds; then
  echo "Could not export AWS credentials. Log in first (e.g. 'aws login'), then re-run." >&2
  exit 1
fi
aws sts get-caller-identity --region "$REGION" >/dev/null 2>&1 \
  || { echo "AWS credentials are not valid/active. Log in (e.g. 'aws login') and re-run." >&2; exit 1; }

# --- terraform ------------------------------------------------------------
log "Provisioning the SSM tunnel host (terraform apply)..."
terraform -chdir="$TF_DIR" init -input=false >/dev/null
APPLIED=1   # set before apply so a partial apply is still torn down on exit
terraform -chdir="$TF_DIR" apply -auto-approve -input=false

INSTANCE_ID="$(terraform -chdir="$TF_DIR" output -raw tunnel_instance_id)"

log "Waiting for the SSM agent on $INSTANCE_ID to come online..."
until [ "$(aws ssm describe-instance-information --region "$REGION" \
            --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
            --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)" = "Online" ]; do
  sleep 5
done

# --- tunnel ---------------------------------------------------------------
log "Opening port-forward tunnel to localhost:$LOCAL_PORT..."
restart_tunnel || { echo "Tunnel failed to come up." >&2; exit 1; }

# Keep the SSM session alive across credential expiry.
cred_keeper &
KEEPER_PID=$!

# DB connection overrides: point at the tunnel; user/password/db come from .env.
db_env=(
  POSTGRES_HOST=localhost
  POSTGRES_PORT="$LOCAL_PORT"
  POSTGRES_SSLMODE=require       # RDS requires TLS
)

# --- seed (optional) ------------------------------------------------------
if [ "$SEED" = 1 ]; then
  log "Reseeding the RDS (replaces all rows)..."
  env "${db_env[@]}" .venv/bin/python -m app.seed
fi

# --- run ------------------------------------------------------------------
log "Launching API on http://localhost:$API_PORT  (Ctrl-C to stop)"
echo "    Bruno: select the 'Live RDS' environment."
env "${db_env[@]}" .venv/bin/uvicorn app.main:app --port "$API_PORT"
