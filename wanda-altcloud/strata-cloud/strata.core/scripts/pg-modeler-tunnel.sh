#!/usr/bin/env bash
#
# Friendly wrapper around the SSM tunnel to the private RDS instance.
#
# Developers should not need to remember terraform / aws / session-manager
# invocations. This script does the whole dance:
#
#   ./pg-modeler-tunnel.sh up      Provision the tunnel host (if needed), wait for it to
#                       register with SSM, print pgModeler connection details,
#                       then open the port-forward and hold it open.
#   ./pg-modeler-tunnel.sh down    Tear the tunnel host down (stops the hourly charge).
#   ./pg-modeler-tunnel.sh status  Show whether the host exists and is SSM-online.
#
# With the tunnel open, point pgModeler (or psql, or the seed script) at
# 127.0.0.1:<local_port>. Leave `up` running in its own terminal.

set -euo pipefail

# --- Resolve locations ----------------------------------------------------
# This script lives in scripts/; terraform is at ../infra/terraform and the repo
# root (.env) is one level up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cd "$SCRIPT_DIR/../infra/terraform" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
AWS_REGION="${AWS_REGION:-eu-west-2}"
# Minimum credential lifetime (seconds) we insist on before starting an
# operation. A near-expired token is the usual cause of an apply/destroy that
# appears to "hang": AWS calls start failing partway through and the SDK retries
# with long exponential backoff. Override with MIN_CRED_TTL=... if needed.
MIN_CRED_TTL="${MIN_CRED_TTL:-600}"

# --- Pretty output --------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; DIM=""; RESET=""
fi
info()  { printf '%s\n' "${BOLD}${*}${RESET}"; }
ok()    { printf '%s\n' "${GREEN}✓ ${*}${RESET}"; }
warn()  { printf '%s\n' "${YELLOW}! ${*}${RESET}"; }
die()   { printf '%s\n' "${RED}✗ ${*}${RESET}" >&2; exit 1; }

# --- Credentials ----------------------------------------------------------
# The Terraform AWS provider can't read the org's custom `aws login` token
# cache, so we export static creds into the environment for every AWS call.
# `aws configure export-credentials` also *renews* short-lived creds from the
# still-valid login session, so simply re-running it is our auto-refresh.

# Parse an ISO-8601 timestamp to epoch seconds (portable across GNU/BSD date).
# Prints nothing on failure so callers can treat "unknown" as "no expiry".
iso_to_epoch() {
  local ts="$1"
  # GNU date (Linux) understands ISO-8601 directly.
  if date -d "$ts" +%s 2>/dev/null; then return; fi
  # BSD date (macOS): normalise "Z"/"+00:00" and drop fractional seconds first.
  local norm="${ts/Z/+0000}"
  norm="$(printf '%s' "$norm" | sed -E 's/\.[0-9]+//; s/([+-][0-9]{2}):([0-9]{2})$/\1\2/')"
  date -j -f "%Y-%m-%dT%H:%M:%S%z" "$norm" +%s 2>/dev/null || true
}

# Export current creds into the environment (also renews them if the session is
# alive). Returns non-zero if nothing could be exported.
export_creds() {
  local env_block
  env_block="$(aws configure export-credentials --region "$AWS_REGION" --format env 2>/dev/null)" || return 1
  [ -n "$env_block" ] || return 1
  eval "$env_block"
}

# Seconds until the currently-exported creds expire. Prints nothing for
# credentials with no expiry (e.g. long-lived IAM user keys).
cred_ttl_remaining() {
  local exp
  exp="$(aws configure export-credentials --region "$AWS_REGION" --format process 2>/dev/null \
        | awk -F'"' '/Expiration/{print $4; exit}')"
  [ -n "$exp" ] || return 0
  local exp_epoch now
  exp_epoch="$(iso_to_epoch "$exp")"
  [ -n "$exp_epoch" ] || return 0
  now="$(date +%s)"
  printf '%s' "$(( exp_epoch - now ))"
}

load_creds() {
  command -v aws       >/dev/null || die "aws CLI not found. Install with: brew install awscli"
  command -v terraform >/dev/null || die "terraform not found. Install with: brew install terraform"
  command -v session-manager-plugin >/dev/null || \
    die "session-manager-plugin not found. Install with: brew install --cask session-manager-plugin"

  info "Loading AWS credentials for ${AWS_REGION}…"
  export_creds || die "Could not export AWS credentials. Run your org login (e.g. 'aws login') first."
  aws sts get-caller-identity >/dev/null 2>&1 || die "AWS credentials are not valid. Log in and retry."

  # Preflight: refuse to start a long op on a near-dead token (the classic
  # "hang"). Try one automatic refresh; only give up if the login session
  # itself is spent — that genuinely needs a human to re-authenticate.
  local ttl; ttl="$(cred_ttl_remaining)"
  if [ -n "$ttl" ] && [ "$ttl" -lt "$MIN_CRED_TTL" ]; then
    warn "Credentials expire in ${ttl}s (< ${MIN_CRED_TTL}s) — refreshing before we start…"
    export_creds || true
    ttl="$(cred_ttl_remaining)"
    if [ -n "$ttl" ] && [ "$ttl" -lt "$MIN_CRED_TTL" ]; then
      die "Credentials still expire in ${ttl}s after refresh — your login session is spent. Run 'aws login' and retry."
    fi
    ok "Credentials refreshed (valid ~$(( ttl / 60 )) more min)."
  elif [ -n "$ttl" ]; then
    ok "AWS credentials loaded (valid ~$(( ttl / 60 )) more min)."
  else
    ok "AWS credentials loaded."
  fi
}

tf() { terraform -chdir="$TF_DIR" "$@"; }

# Run a mutating terraform command, streaming its output live. If it fails on an
# expired/invalid-token error (a credential that died *mid-run*), refresh creds
# once and retry — this is the automatic recovery for a long apply/destroy that
# outlives its token. Any other failure is returned as-is.
TF_AUTH_ERR_RE='ExpiredToken|RequestExpired|InvalidClientTokenId|expired.*token|token.*expired|security token.*(expired|invalid)|credentials.*expired'
tf_retry() {
  local log rc
  log="$(mktemp -t tunnel-tf.XXXXXX)"
  if tf "$@" 2>&1 | tee "$log"; then rc=0; else rc=${PIPESTATUS[0]}; fi
  if [ "$rc" -ne 0 ] && grep -qiE "$TF_AUTH_ERR_RE" "$log"; then
    warn "Terraform failed on an expired credential — refreshing and retrying once…"
    rm -f "$log"
    export_creds || die "Could not refresh credentials. Run 'aws login' and retry."
    tf "$@"
    return
  fi
  rm -f "$log"
  return "$rc"
}

instance_id() { tf output -raw tunnel_instance_id 2>/dev/null || true; }

ssm_online() {
  local id="$1"
  [ -n "$id" ] || return 1
  local found
  found="$(aws ssm describe-instance-information --region "$AWS_REGION" \
    --filters "Key=InstanceIds,Values=$id" \
    --query 'InstanceInformationList[0].InstanceId' --output text 2>/dev/null || true)"
  [ "$found" = "$id" ]
}

# --- Connection details ---------------------------------------------------
# The exact port-forward command (with the resolved local port) is a terraform
# output; parse the local port out of it so we display the right number even if
# someone overrode local_port in terraform.tfvars.
local_port() {
  tf output -raw start_session_command 2>/dev/null \
    | grep -oE '"localPortNumber":\["[0-9]+"\]' \
    | grep -oE '[0-9]+' || echo 55432
}

env_val() {  # env_val KEY -> value from .env, or empty
  [ -f "$ENV_FILE" ] || { echo ""; return; }
  grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true
}

print_connection() {
  local port; port="$(local_port)"
  local db user
  db="$(env_val POSTGRES_DB)";   db="${db:-postgres}"
  user="$(env_val POSTGRES_USER)"; user="${user:-postgres}"

  echo
  info "pgModeler / psql connection details"
  printf '%s\n' "${DIM}────────────────────────────────────────────${RESET}"
  printf '  Host      %s\n' "127.0.0.1        ${DIM}(use the literal IPv4, not 'localhost')${RESET}"
  printf '  Port      %s\n' "$port"
  printf '  Database  %s\n' "$db"
  printf '  Username  %s\n' "$user"
  printf '  Password  %s\n' "see POSTGRES_PASSWORD in ${ENV_FILE/#$HOME/\~}"
  printf '  SSL mode  %s\n' "require          ${DIM}(RDS enforces TLS end-to-end)${RESET}"
  printf '%s\n' "${DIM}────────────────────────────────────────────${RESET}"
  echo
}

# --- Commands -------------------------------------------------------------
cmd_up() {
  load_creds

  if [ ! -d "$TF_DIR/.terraform" ]; then
    info "Initialising terraform…"; tf init -input=false >/dev/null; ok "Initialised."
  fi

  local id; id="$(instance_id)"
  if [ -z "$id" ]; then
    info "Provisioning the tunnel host (t3.micro, ~\$0.012/hr)…"
    tf_retry apply -auto-approve -input=false -lock-timeout=120s
    id="$(instance_id)"
    ok "Tunnel host created: $id"
  else
    ok "Tunnel host already exists: $id"
  fi

  info "Waiting for the SSM agent to register (can take 1-2 min)…"
  local waited=0
  until ssm_online "$id"; do
    [ "$waited" -ge 180 ] && die "Timed out waiting for SSM. Check 'pg-modeler-tunnel.sh status'."
    printf '  %s…\r' "${waited}s"
    sleep 5; waited=$((waited + 5))
  done
  ok "Tunnel host is online with SSM."

  print_connection

  info "Opening the port-forward. ${DIM}Leave this running; press Ctrl-C to close the tunnel"
  info "(the host stays up — run './pg-modeler-tunnel.sh down' when you're finished for the day).${RESET}"
  echo
  # Runs in the foreground and blocks until Ctrl-C.
  tf output -raw start_session_command | sh
}

cmd_down() {
  load_creds
  local id; id="$(instance_id)"
  if [ -z "$id" ]; then ok "No tunnel host to destroy."; return; fi
  info "Destroying the tunnel host (RDS and its network are never touched)…"
  info "${DIM}EC2 termination takes ~1 min — 'Still destroying…' is normal, not a hang.${RESET}"
  tf_retry destroy -auto-approve -input=false -lock-timeout=120s
  ok "Tunnel host destroyed. Hourly charge stopped."
}

# Release a Terraform state lock stranded by a hard-killed run. Safe: refuses to
# act while a terraform process is still working against this dir.
cmd_unlock() {
  load_creds
  local lockfile="$TF_DIR/.terraform.tfstate.lock.info"
  if [ ! -f "$lockfile" ]; then ok "No state lock present — nothing to release."; return; fi
  if pgrep -f "terraform -chdir=$TF_DIR" >/dev/null 2>&1; then
    die "A terraform process is still running against this dir — refusing to force-unlock. Let it finish (or stop it) first."
  fi
  local id; id="$(awk -F'"' '/"ID"/{print $4; exit}' "$lockfile" 2>/dev/null)"
  [ -n "$id" ] || die "Could not read the lock ID from $lockfile."
  warn "Found a stale lock ($id) with no running terraform process."
  tf force-unlock -force "$id" && ok "State lock released."
}

cmd_status() {
  load_creds
  local id; id="$(instance_id)"
  if [ -z "$id" ]; then warn "No tunnel host provisioned. Run './pg-modeler-tunnel.sh up'."; return; fi
  if ssm_online "$id"; then
    ok "Tunnel host $id is up and SSM-online."
    print_connection
    printf '%s\n' "${DIM}Open the tunnel with: ./pg-modeler-tunnel.sh up${RESET}"
  else
    warn "Tunnel host $id exists but is not SSM-online yet (still registering?)."
  fi
}

usage() {
  cat <<EOF
${BOLD}pg-modeler-tunnel.sh${RESET} — reach the private RDS instance from your laptop.

  ${BOLD}./pg-modeler-tunnel.sh up${RESET}       provision + open the tunnel, print connection details
  ${BOLD}./pg-modeler-tunnel.sh down${RESET}     tear the tunnel host down (stops the hourly charge)
  ${BOLD}./pg-modeler-tunnel.sh status${RESET}   show whether the host exists / is online
  ${BOLD}./pg-modeler-tunnel.sh unlock${RESET}   release a state lock stranded by a killed run

Then connect pgModeler to 127.0.0.1 (details printed by 'up' / 'status').
EOF
}

# Only dispatch when executed directly — being sourced (e.g. by tests) just
# loads the functions above without running a command.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  case "${1:-}" in
    up)     cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    unlock) cmd_unlock ;;
    ""|-h|--help|help) usage ;;
    *) usage; exit 1 ;;
  esac
fi
