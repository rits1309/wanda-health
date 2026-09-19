#!/usr/bin/env bash

set -euo pipefail

URL="${1:?Usage: ./scripts/smoke-test.sh <health-url>}"

curl --fail --silent --show-error "$URL"
echo
echo "Smoke test passed."
