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
  PY_ERR_FILE="/tmp/py_compile_err_$$"
  if ! python3 -m py_compile "$TARGET" 2>"$PY_ERR_FILE"; then
    ERR=$(cat "$PY_ERR_FILE" 2>/dev/null)
    rm -f "$PY_ERR_FILE" "$TARGET"
    echo "{\"status\":\"error\",\"file\":\"$REL_PATH\",\"message\":\"syntax error — file rejected: $ERR\"}"
    exit 1
  fi
  rm -f "$PY_ERR_FILE"
fi

echo "{\"status\":\"applied\",\"file\":\"$REL_PATH\"}"
