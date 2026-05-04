#!/usr/bin/env bash
# Usage: apply_fix.sh <local_path> <relative_file_path>
# New file content is read from stdin.
set -euo pipefail

LOCAL_PATH="${1:?local_path required}"
REL_PATH="${2:?relative_file_path required}"
TARGET="$LOCAL_PATH/$REL_PATH"

mkdir -p "$(dirname "$TARGET")"
cat > "$TARGET"

# Validate Python syntax immediately after writing.
if [[ "$TARGET" == *.py ]]; then
  if ! python3 -m py_compile "$TARGET" 2>/tmp/py_compile_err; then
    ERR=$(cat /tmp/py_compile_err 2>/dev/null)
    rm -f "$TARGET"
    echo "{\"status\":\"error\",\"file\":\"$REL_PATH\",\"message\":\"syntax error — file rejected: $ERR\"}"
    exit 1
  fi
fi

echo "{\"status\":\"applied\",\"file\":\"$REL_PATH\"}"
