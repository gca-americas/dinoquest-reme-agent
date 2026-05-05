#!/usr/bin/env bash
# Usage: commit_branch.sh <local_path> <incident_datetime_iso> <commit_message>
# Creates branch incident_YYMMDDHHММ from incident_datetime, stages all changes,
# commits with the given message, and pushes to origin.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
INCIDENT_DT="${2:?incident_datetime required}"
COMMIT_MSG="${3:?commit_message required}"

# Derive branch name: incident_YYMMDDHHММ (always includes minutes — unique per incident).
# date -d works on GNU/Linux (Debian-based Cloud Run images).
BRANCH="incident_$(date -ud "$INCIDENT_DT" '+%y%m%d%H%M' 2>/dev/null || date -u '+%y%m%d%H%M')"

cd "$LOCAL_PATH"

# Delete any local branch with this name left over from a cached clone.
git branch -D "$BRANCH" 2>/dev/null || true
git checkout -b "$BRANCH"    || { echo "{\"status\":\"error\",\"step\":\"checkout\",\"message\":\"$(git status 2>&1 | head -3)\"}"; exit 1; }
git add -A                   || { echo "{\"status\":\"error\",\"step\":\"add\",\"message\":\"git add -A failed\"}"; exit 1; }
git commit -m "$COMMIT_MSG"  2>/tmp/git_commit_err_$$ || { ERR=$(cat /tmp/git_commit_err_$$ 2>/dev/null); rm -f /tmp/git_commit_err_$$; echo "{\"status\":\"error\",\"step\":\"commit\",\"message\":\"$ERR\"}"; exit 1; }
rm -f /tmp/git_commit_err_$$

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
