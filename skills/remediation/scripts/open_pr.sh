#!/usr/bin/env bash
# Usage: open_pr.sh <local_path> <title> <body>
# Opens a GitHub pull request from the current branch.
# Requires GITHUB_TOKEN in the environment (gh picks it up automatically).
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
TITLE="${2:?title required}"
BODY="${3:?body required}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo '{"status":"error","message":"GITHUB_TOKEN is not set"}' >&2
  exit 1
fi

cd "$LOCAL_PATH"

for attempt in 1 2 3; do
  if URL=$(gh pr create --title "$TITLE" --body "$BODY" 2>/tmp/gh_pr_err); then
    break
  fi
  ERR=$(cat /tmp/gh_pr_err 2>/dev/null || true)
  if [[ $attempt -lt 3 ]]; then
    echo "open_pr attempt $attempt failed: $ERR — retrying in $((attempt * 4))s" >&2
    sleep $((attempt * 4))
  else
    echo "{\"status\":\"error\",\"message\":\"gh pr create failed after 3 attempts: $ERR\"}" >&2
    exit 1
  fi
done

echo "{\"status\":\"pr_created\",\"url\":\"$URL\"}"
