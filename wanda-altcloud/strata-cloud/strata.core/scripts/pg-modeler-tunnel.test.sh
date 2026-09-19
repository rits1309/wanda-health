#!/usr/bin/env bash
#
# Self-contained tests for pg-modeler-tunnel.sh's credential-recovery logic.
#
#   ./pg-modeler-tunnel.test.sh
#
# No AWS access is needed: we put mock `aws` / `terraform` / `session-manager-plugin`
# on PATH and source pg-modeler-tunnel.sh (its command dispatch is guarded, so sourcing just
# loads the functions). The mocks are written to a temp dir that is removed on exit.
#
# Scenarios covered:
#   1. Near-expiry token, login session alive   -> auto-refresh recovers
#   2. Login session spent                       -> fail fast (no hang)
#   3. Credential dies mid-apply                 -> catch ExpiredToken, retry once
#   4. Healthy token                             -> proceed, no refresh

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUNNEL="$TEST_DIR/pg-modeler-tunnel.sh"
[ -f "$TUNNEL" ] || { echo "cannot find pg-modeler-tunnel.sh next to this test" >&2; exit 2; }

BIN="$(mktemp -d -t tunnel-test.XXXXXX)"
trap 'rm -rf "$BIN"' EXIT

# ISO-8601 (UTC) timestamp `secs` in the future, portable across BSD/GNU date.
future_iso() {
  /bin/date -u -v+"$1"S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return
  date -u -d "+$1 seconds" +%Y-%m-%dT%H:%M:%SZ
}

# --- Write the mocks ------------------------------------------------------
cat > "$BIN/aws" <<'MOCK'
#!/usr/bin/env bash
# Driven by: MOCK_EXP_1, MOCK_EXP_2, MOCK_FIRST_CALLS, MOCK_STATE, MOCK_EXPORT_FAIL
set -euo pipefail
args="$*"
case "$args" in
  *"configure export-credentials"*"--format env"*)
    [ "${MOCK_EXPORT_FAIL:-0}" = "1" ] && exit 1
    printf 'export AWS_ACCESS_KEY_ID=AKIAMOCK\n'
    printf 'export AWS_SECRET_ACCESS_KEY=secretmock\n'
    printf 'export AWS_SESSION_TOKEN=tokenmock\n' ;;
  *"configure export-credentials"*"--format process"*)
    n=$(cat "$MOCK_STATE" 2>/dev/null || echo 0); n=$((n + 1)); printf '%s' "$n" > "$MOCK_STATE"
    if [ "$n" -le "${MOCK_FIRST_CALLS:-1}" ]; then exp="$MOCK_EXP_1"; else exp="$MOCK_EXP_2"; fi
    printf '{\n  "Version": 1,\n  "AccessKeyId": "AKIAMOCK",\n  "SecretAccessKey": "secretmock",\n  "SessionToken": "tokenmock",\n  "Expiration": "%s"\n}\n' "$exp" ;;
  *"sts get-caller-identity"*)
    printf '{"Account":"YOUR_AWS_ACCOUNT_ID","Arn":"arn:aws:iam::YOUR_AWS_ACCOUNT_ID:user/mock"}\n';
  *)
    printf 'mock aws: unhandled args: %s\n' "$args" >&2; exit 3 ;;
esac
MOCK

cat > "$BIN/terraform" <<'MOCK'
#!/usr/bin/env bash
# Driven by: MOCK_TF_FAILS, MOCK_TF_STATE
set -euo pipefail
sub=""
for a in "$@"; do case "$a" in -chdir=*) ;; *) sub="$a"; break ;; esac; done
case "$sub" in
  apply|destroy)
    n=$(cat "$MOCK_TF_STATE" 2>/dev/null || echo 0); n=$((n + 1)); printf '%s' "$n" > "$MOCK_TF_STATE"
    if [ "$n" -le "${MOCK_TF_FAILS:-0}" ]; then
      printf 'Error: ExpiredToken: The security token included in the request is expired\n' >&2
      exit 1
    fi
    printf 'Apply complete! Resources: 1 destroyed.\n' ;;
  *)
    printf 'mock terraform: unhandled subcommand: %s\n' "$sub" >&2; exit 3 ;;
esac
MOCK

printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/session-manager-plugin"
chmod +x "$BIN/aws" "$BIN/terraform" "$BIN/session-manager-plugin"

export PATH="$BIN:$PATH"
export AWS_REGION="eu-west-2"
export MIN_CRED_TTL=600
NEAR="$(future_iso 60)"    # below the 600s floor
FAR="$(future_iso 3600)"   # healthy

# --- Assertion helper -----------------------------------------------------
pass=0; fail=0
check() { # name expected_rc actual_rc needle output
  local name="$1" exp_rc="$2" act_rc="$3" needle="$4" out="$5"
  if [ "$act_rc" = "$exp_rc" ] && printf '%s' "$out" | grep -qF "$needle"; then
    printf '  \033[32mPASS\033[0m  %s\n' "$name"; pass=$((pass + 1))
  else
    printf '  \033[31mFAIL\033[0m  %s (rc=%s want %s; looked for "%s")\n' "$name" "$act_rc" "$exp_rc" "$needle"
    printf '        --- output ---\n%s\n        --------------\n' "$out"; fail=$((fail + 1))
  fi
}

# --- Scenario 1 -----------------------------------------------------------
out="$(
  export MOCK_STATE="$(mktemp)" MOCK_EXP_1="$NEAR" MOCK_EXP_2="$FAR" MOCK_FIRST_CALLS=1
  bash -c 'source "'"$TUNNEL"'"; set +e; load_creds' 2>&1
)"; rc=$?
echo "Scenario 1 — near-expiry, session alive (expect auto-refresh, rc 0):"
check "does not hang; refreshes automatically" 0 "$rc" "Credentials refreshed" "$out"

# --- Scenario 2 -----------------------------------------------------------
out="$(
  export MOCK_STATE="$(mktemp)" MOCK_EXP_1="$NEAR" MOCK_EXP_2="$NEAR" MOCK_FIRST_CALLS=99
  bash -c 'source "'"$TUNNEL"'"; set +e; load_creds' 2>&1
)"; rc=$?
echo "Scenario 2 — session spent (expect clean fail, rc 1):"
check "fails fast, tells user to log in" 1 "$rc" "your login session is spent" "$out"

# --- Scenario 3 -----------------------------------------------------------
out="$(
  export MOCK_STATE="$(mktemp)" MOCK_TF_STATE="$(mktemp)" MOCK_EXP_1="$FAR" MOCK_EXP_2="$FAR" MOCK_TF_FAILS=1
  bash -c 'source "'"$TUNNEL"'"; set +e; tf_retry apply -auto-approve' 2>&1
)"; rc=$?
echo "Scenario 3 — credential dies mid-apply (expect refresh + retry, rc 0):"
check "catches ExpiredToken and retries" 0 "$rc" "refreshing and retrying once" "$out"
check "retry ultimately succeeds"        0 "$rc" "Apply complete" "$out"

# --- Scenario 4 -----------------------------------------------------------
out="$(
  export MOCK_STATE="$(mktemp)" MOCK_EXP_1="$FAR" MOCK_EXP_2="$FAR" MOCK_FIRST_CALLS=99
  bash -c 'source "'"$TUNNEL"'"; set +e; load_creds' 2>&1
)"; rc=$?
echo "Scenario 4 — healthy token (expect no refresh, rc 0):"
check "loads without refreshing" 0 "$rc" "valid ~" "$out"

echo
printf 'Total: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
