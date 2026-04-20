#!/usr/bin/env bash
# Usage: read_file.sh <local_path> <relative_file_path>
# Prints file content as JSON so the agent can inspect code before writing a fix.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
REL_PATH="${2:?relative_file_path required}"
TARGET="$LOCAL_PATH/$REL_PATH"

if [[ ! -f "$TARGET" ]]; then
  echo "{\"status\":\"error\",\"message\":\"File not found: $REL_PATH\"}" >&2
  exit 1
fi

# jq encodes the content safely as a JSON string.
jq -Rs --arg file "$REL_PATH" '{status:"ok", file:$file, content:.}' < "$TARGET"
