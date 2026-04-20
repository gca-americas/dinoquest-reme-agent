#!/usr/bin/env bash
# Usage: clone_repo.sh <repo_url>
# GITHUB_TOKEN must be set in the environment.
# Prints the local path on success, or an error message on failure.
set -euo pipefail

REPO_URL="${1:?repo_url required}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo '{"status":"error","message":"GITHUB_TOKEN is not set"}' >&2
  exit 1
fi

# Inject token into HTTPS URL without exposing it in process args.
AUTH_URL=$(echo "$REPO_URL" | sed -E "s|https://|https://x-access-token:${GITHUB_TOKEN}@|")

LOCAL_PATH=$(mktemp -d /tmp/incident_fix_XXXXXX)

if ! git clone --quiet "$AUTH_URL" "$LOCAL_PATH" 2>&1 | sed "s|${GITHUB_TOKEN}|***|g"; then
  echo "{\"status\":\"error\",\"step\":\"clone\",\"message\":\"git clone failed\"}" >&2
  exit 1
fi

git -C "$LOCAL_PATH" config user.name  "${GIT_AUTHOR_NAME:-DinoAgent}"
git -C "$LOCAL_PATH" config user.email "${GIT_AUTHOR_EMAIL:-dinoagent@noreply.github.com}"

echo "{\"status\":\"cloned\",\"local_path\":\"$LOCAL_PATH\"}"
