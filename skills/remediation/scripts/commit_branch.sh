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

git checkout -b "$BRANCH"         || { echo "{\"status\":\"error\",\"step\":\"checkout\"}"; exit 1; }
git add -A                        || { echo "{\"status\":\"error\",\"step\":\"add\"}";      exit 1; }
git commit -m "$COMMIT_MSG"       || { echo "{\"status\":\"error\",\"step\":\"commit\"}";   exit 1; }
git push -u origin "$BRANCH"      || { echo "{\"status\":\"error\",\"step\":\"push\"}";     exit 1; }

echo "{\"status\":\"pushed\",\"branch\":\"$BRANCH\"}"
