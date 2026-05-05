#!/usr/bin/env bash
# run_local_tests.sh — Run pytest on the locally cloned repo before committing.
# Usage: bash run_local_tests.sh <local_path> [test_file]
#   local_path  Root of the cloned repo (returned by clone_repo.sh)
#   test_file   Optional: path relative to local_path to run (default: backend/tests/)
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
TEST_TARGET="${2:-backend/tests/}"

BACKEND_DIR="$LOCAL_PATH/backend"

if [ ! -d "$BACKEND_DIR" ]; then
  echo '{"status":"error","message":"backend/ directory not found — is local_path correct?"}'
  exit 1
fi

cd "$BACKEND_DIR"

# Install deps quietly; requirements already present from the clone
pip install -q -r requirements.txt
pip install -q pytest httpx 2>/dev/null

echo "--- Running pytest on $TEST_TARGET ---"
if pytest "$LOCAL_PATH/$TEST_TARGET" -v --tb=short 2>&1; then
  echo '{"status":"passed"}'
  exit 0
else
  EXIT=$?
  echo '{"status":"failed","exit_code":'"$EXIT"'}'
  exit $EXIT
fi
