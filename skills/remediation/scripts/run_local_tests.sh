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

echo "--- Installing test dependencies ---"
if ! pip install -q -r "$BACKEND_DIR/requirements.txt"; then
  echo '{"status":"error","message":"pip install -r requirements.txt failed — check requirements.txt"}'
  exit 1
fi
if ! pip install -q pytest httpx; then
  echo '{"status":"error","message":"pip install pytest httpx failed"}'
  exit 1
fi

# Run pytest from repo root so that `from backend.main import app` resolves correctly.
cd "$LOCAL_PATH"

echo "--- Running pytest on $TEST_TARGET ---"
pytest "$TEST_TARGET" -v --tb=short 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo '{"status":"passed"}'
else
  echo '{"status":"failed","exit_code":'"$EXIT_CODE"'}'
fi
exit $EXIT_CODE
