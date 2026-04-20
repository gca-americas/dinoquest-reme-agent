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

URL=$(gh pr create --title "$TITLE" --body "$BODY") \
  || { echo "{\"status\":\"error\",\"message\":\"gh pr create failed\"}"; exit 1; }

echo "{\"status\":\"pr_created\",\"url\":\"$URL\"}"
