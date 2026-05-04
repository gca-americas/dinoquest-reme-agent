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

CACHE_DIR="/tmp/repo_cache"

# Reuse the cached clone if it exists and is for the same repo; fresh clone otherwise.
# Shallow depth=1 keeps the clone fast regardless of repo history.
REPO_IDENTITY="${REPO_URL#https://}"   # e.g. github.com/owner/repo — present in both plain and token-prefixed URLs
_git_fetch_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if git -C "$CACHE_DIR" fetch --depth=1 --quiet origin 2>&1 | sed "s|${GITHUB_TOKEN}|***|g"; then
      return 0
    fi
    if [[ $attempt -lt 3 ]]; then
      echo "git fetch attempt $attempt failed — retrying in $((attempt * 5))s" >&2
      sleep $((attempt * 5))
    fi
  done
  return 1
}

_git_clone_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if git clone --depth=1 --quiet "$AUTH_URL" "$CACHE_DIR" 2>&1 | sed "s|${GITHUB_TOKEN}|***|g"; then
      return 0
    fi
    if [[ $attempt -lt 3 ]]; then
      echo "git clone attempt $attempt failed — retrying in $((attempt * 5))s" >&2
      rm -rf "$CACHE_DIR"
      sleep $((attempt * 5))
    fi
  done
  return 1
}

if [[ -d "$CACHE_DIR/.git" ]] && \
   git -C "$CACHE_DIR" remote get-url origin 2>/dev/null | grep -qF "$REPO_IDENTITY"; then
  if ! _git_fetch_with_retry; then
    echo "Cache fetch failed after 3 attempts, re-cloning" >&2
    rm -rf "$CACHE_DIR"
  else
    git -C "$CACHE_DIR" reset --hard origin/HEAD --quiet
    git -C "$CACHE_DIR" clean -fd --quiet
    git -C "$CACHE_DIR" remote set-url origin "$AUTH_URL"
  fi
fi

if [[ ! -d "$CACHE_DIR/.git" ]]; then
  rm -rf "$CACHE_DIR"
  if ! _git_clone_with_retry; then
    echo "{\"status\":\"error\",\"step\":\"clone\",\"message\":\"git clone failed after 3 attempts\"}" >&2
    exit 1
  fi
fi

LOCAL_PATH="$CACHE_DIR"

git -C "$LOCAL_PATH" config user.name  "${GIT_AUTHOR_NAME:-DinoAgent}"
git -C "$LOCAL_PATH" config user.email "${GIT_AUTHOR_EMAIL:-dinoagent@noreply.github.com}"

echo "{\"status\":\"cloned\",\"local_path\":\"$LOCAL_PATH\"}"
