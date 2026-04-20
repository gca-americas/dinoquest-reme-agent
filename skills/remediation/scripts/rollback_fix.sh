#!/usr/bin/env bash
# Usage: rollback_fix.sh <local_path> <branch_name>
# Closes the open PR for the branch (if any) and deletes the remote branch.
# Safe to call even if the PR was already closed or the branch already deleted.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
BRANCH="${2:?branch_name required}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo '{"status":"error","message":"GITHUB_TOKEN is not set"}' >&2
  exit 1
fi

cd "$LOCAL_PATH"

# Close the PR and delete the remote branch in one step; ignore errors if already gone.
gh pr close "$BRANCH" --delete-branch 2>/dev/null || true

# Belt-and-suspenders: delete remote branch directly in case gh didn't.
git push origin --delete "$BRANCH" 2>/dev/null || true

echo "{\"status\":\"rolled_back\",\"branch\":\"$BRANCH\"}"
