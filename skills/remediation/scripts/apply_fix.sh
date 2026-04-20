#!/usr/bin/env bash
# Usage: apply_fix.sh <local_path> <relative_file_path>
# New file content is read from stdin.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
REL_PATH="${2:?relative_file_path required}"
TARGET="$LOCAL_PATH/$REL_PATH"

if [[ ! -f "$TARGET" ]]; then
  echo "{\"status\":\"error\",\"message\":\"File not found: $REL_PATH\"}" >&2
  exit 1
fi

cat > "$TARGET"

echo "{\"status\":\"applied\",\"file\":\"$REL_PATH\"}"
