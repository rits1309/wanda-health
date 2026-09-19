#!/usr/bin/env bash
# Runs scripts/provision_users_from_csv.py as a one-off ECS task against the
# SAME task definition + network config the real engine-auth service uses,
# then health-checks every genuinely NEW account by actually logging in
# with it -- proof it works, not just that the script exited 0.
#
# Deliberately never prints a password to stdout: this may run inside a CI
# job whose output is visible to anyone with Actions read access on the
# repo, a wider audience than CloudWatch access. The password-retrieval
# command is printed instead, for whoever triggered this run to fetch
# deliberately and hand off out-of-band -- same discipline
# provision-client-users.yml already followed before this script existed,
# just extracted here so it's testable/runnable outside a GitHub Actions
# step too (e.g. by hand, from a laptop with the right AWS credentials).
#
# Required env: CLUSTER, FAMILY, USERS_CSV_CONTENT, LOGIN_BASE_URL (the
# /wanda-prefixed base URL to health-check logins against, e.g.
# https://<cloudfront-domain>/wanda).
set -euo pipefail

: "${CLUSTER:?}"
: "${FAMILY:?}"
: "${USERS_CSV_CONTENT:?}"
: "${LOGIN_BASE_URL:?}"

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

TASK_DEF_ARN=$(aws ecs describe-services --cluster "$CLUSTER" --services "$FAMILY" \
  --query 'services[0].taskDefinition' --output text)
CONTAINER_NAME=$(aws ecs describe-task-definition --task-definition "$TASK_DEF_ARN" \
  --query 'taskDefinition.containerDefinitions[0].name' --output text)
aws ecs describe-services --cluster "$CLUSTER" --services "$FAMILY" \
  --query 'services[0].networkConfiguration' --output json > "$WORKDIR/network-config.json"

OVERRIDES=$(jq -n --arg name "$CONTAINER_NAME" --arg csv "$USERS_CSV_CONTENT" \
  '{containerOverrides: [{name: $name, command: ["python3", "-m", "scripts.provision_users_from_csv"], environment: [{name: "USERS_CSV_CONTENT", value: $csv}]}]}')

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF_ARN" \
  --launch-type FARGATE \
  --network-configuration "file://$WORKDIR/network-config.json" \
  --overrides "$OVERRIDES" \
  --query 'tasks[0].taskArn' --output text)
echo "Started provisioning task: $TASK_ARN (task def: $TASK_DEF_ARN)"

aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"

EXIT_CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)

LOG_STREAM_PREFIX=$(aws ecs describe-task-definition --task-definition "$TASK_DEF_ARN" \
  --query 'taskDefinition.containerDefinitions[0].logConfiguration.options."awslogs-stream-prefix"' --output text)
LOG_GROUP=$(aws ecs describe-task-definition --task-definition "$TASK_DEF_ARN" \
  --query 'taskDefinition.containerDefinitions[0].logConfiguration.options."awslogs-group"' --output text)
TASK_ID="${TASK_ARN##*/}"
LOG_STREAM="$LOG_STREAM_PREFIX/$CONTAINER_NAME/$TASK_ID"
FETCH_CMD="aws logs tail \"$LOG_GROUP\" --log-stream-names \"$LOG_STREAM\" --since 30m"

if [ "$EXIT_CODE" != "0" ]; then
  echo "::error::provision_users_from_csv.py failed (exit $EXIT_CODE). Logs: $FETCH_CMD"
  exit 1
fi

# The one machine-readable line provision_users_from_csv.py emits for
# exactly this purpose -- never regex-parsed out of the human-readable
# prose above it.
#
# `grep -o 'NEW_ACCOUNTS_JSON:.*'`, NOT `grep '^NEW_ACCOUNTS_JSON:'`: every
# line `aws logs tail` prints is prefixed with a timestamp and the log
# stream name (e.g. "2026-...+00:00 strata-engine-auth/.../<task-id>
# NEW_ACCOUNTS_JSON:[...]"), so the marker is never at the START of the
# line -- an anchored pattern can never match at all, independent of any
# timing question. Confirmed by a real run: the anchored version found
# nothing on every retry, while fetching the exact same log by hand and
# reading it with human eyes made the (still-there) prefix easy to miss.
# `|| true`: under `set -e`, grep's own "no match" exit (1) -- a normal
# outcome if every user in this run already existed -- would otherwise
# kill the script before the empty-JSON fallback below ever runs.
#
# Retried, not fetched once, on top of that fix: CloudWatch can genuinely
# lag a little behind the container actually stopping, so this still
# gives a real ingestion delay a few chances rather than failing on the
# first attempt.
NEW_ACCOUNTS_JSON=""
for _ in 1 2 3 4 5; do
  aws logs tail "$LOG_GROUP" --log-stream-names "$LOG_STREAM" --since 15m > "$WORKDIR/provision-output.log"
  NEW_ACCOUNTS_JSON=$( (grep -o 'NEW_ACCOUNTS_JSON:.*' "$WORKDIR/provision-output.log" || true) | tail -1 | sed 's/^NEW_ACCOUNTS_JSON://')
  [ -n "$NEW_ACCOUNTS_JSON" ] && break
  sleep 2
done
if [ -z "$NEW_ACCOUNTS_JSON" ]; then
  echo "::warning::provision_users_from_csv.py's output didn't include the expected NEW_ACCOUNTS_JSON line after retrying -- skipping the health check, but the run itself succeeded (exit 0)."
  NEW_ACCOUNTS_JSON="[]"
fi

NEW_COUNT=$(echo "$NEW_ACCOUNTS_JSON" | jq 'length')
echo "Provisioning succeeded. $NEW_COUNT brand-new account(s) to health-check."

HEALTH_FAILED=0
if [ "$NEW_COUNT" -gt 0 ]; then
  while IFS= read -r account; do
    email=$(echo "$account" | jq -r '.email')
    password=$(echo "$account" | jq -r '.password')

    # A temp file, not `--data-binary @<(...)` process substitution: a
    # native-Windows curl build (confirmed during testing) can't open the
    # FIFO-like path that substitution creates under git-bash. A plain
    # file works everywhere the process-substitution form does, so this
    # isn't a workaround narrowed to that one environment.
    login_body="$WORKDIR/login-body.json"
    jq -n --arg id "$email" --arg pw "$password" '{identifier: $id, password: $pw}' > "$login_body"
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 -X POST "$LOGIN_BASE_URL/v1/auth/login" \
      -H "Content-Type: application/json" \
      --data-binary "@$login_body")
    rm -f "$login_body"

    if [ "$status" = "200" ]; then
      echo "  OK   $email -- login verified (200)"
    else
      echo "  FAIL $email -- login returned $status, expected 200"
      HEALTH_FAILED=1
    fi
  done < <(echo "$NEW_ACCOUNTS_JSON" | jq -c '.[]')
fi

echo ""
echo "To retrieve any newly-generated password for handoff, run:"
echo "  $FETCH_CMD"

if [ "$HEALTH_FAILED" != "0" ]; then
  echo "::error::One or more newly-created accounts failed their login health check -- see above, and the log-fetch command for details."
  exit 1
fi
