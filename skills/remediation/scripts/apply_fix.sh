#!/usr/bin/env bash
# Usage: apply_fix.sh <local_path> <relative_file_path>
# New file content is read from stdin.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
REL_PATH="${2:?relative_file_path required}"
TARGET="$LOCAL_PATH/$REL_PATH"

mkdir -p "$(dirname "$TARGET")"
cat > "$TARGET"

echo "{\"status\":\"applied\",\"file\":\"$REL_PATH\"}"
