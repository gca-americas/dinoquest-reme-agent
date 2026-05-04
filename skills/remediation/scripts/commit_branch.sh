#!/usr/bin/env bash
# Usage: commit_branch.sh <local_path> <incident_datetime_iso> <commit_message>
# Creates branch incident_YYMMDDHH from incident_datetime, stages all changes,
# commits with the given message, and pushes to origin.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
INCIDENT_DT="${2:?incident_datetime required}"
COMMIT_MSG="${3:?commit_message required}"

# Derive branch name: incident_YYMMDDHH
# date -d works on GNU/Linux (Debian-based Cloud Run images).
BRANCH="incident_$(date -ud "$INCIDENT_DT" '+%y%m%d%H' 2>/dev/null || date -u '+%y%m%d%H')"

cd "$LOCAL_PATH"

# If the branch already exists on the remote (prior run for the same incident hour),
# append current minutes+seconds to avoid a non-fast-forward push rejection.
if git ls-remote --exit-code --heads origin "$BRANCH" > /dev/null 2>&1; then
    BRANCH="${BRANCH}_$(date -u '+%M%S')"
fi

git checkout -b "$BRANCH"    || { echo "{\"status\":\"error\",\"step\":\"checkout\"}"; exit 1; }
git add -A                   || { echo "{\"status\":\"error\",\"step\":\"add\"}";      exit 1; }
git commit -m "$COMMIT_MSG"  || { echo "{\"status\":\"error\",\"step\":\"commit\"}";   exit 1; }

for attempt in 1 2 3; do
  git push -u origin "$BRANCH" 2>/tmp/git_push_err && break
  ERR=$(cat /tmp/git_push_err 2>/dev/null || true)
  if [[ $attempt -lt 3 ]]; then
    echo "git push attempt $attempt failed: $ERR — retrying in $((attempt * 4))s" >&2
    sleep $((attempt * 4))
  else
    echo "{\"status\":\"error\",\"step\":\"push\",\"message\":\"push failed after 3 attempts: $ERR\"}"
    exit 1
  fi
done

echo "{\"status\":\"pushed\",\"branch\":\"$BRANCH\"}"
