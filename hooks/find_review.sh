#!/bin/bash
# Find the latest Overwatch review for the current project.
# Usage: bash hooks/find_review.sh [project_directory]
# Output: Review file path (if exists)

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${OVERWATCH_STATE_DIR:-${OVERWATCH_DIR}/state}"
REVIEWS_DIR="${OVERWATCH_REVIEWS_DIR:-${OVERWATCH_DIR}/reviews}"

if [ "${1:-}" = "--result-file" ]; then
    RESULT_FILE="${2:-}"
    shift 2
    EXPECTED_SESSION=""
    if [ "${1:-}" = "--session-id" ]; then
        EXPECTED_SESSION="${2:-}"
    fi
    python3 - "$RESULT_FILE" "$EXPECTED_SESSION" <<'PY'
import json
import hashlib
import os
import re
import sys

result_file, expected_session = sys.argv[1:3]
with open(result_file, encoding="utf-8") as stream:
    result = json.load(stream)
if result.get("status") != "success":
    raise SystemExit("manual review result is not successful")
session_id = str(result.get("session_id") or "")
if not session_id or (expected_session and session_id != expected_session):
    raise SystemExit("manual review result session mismatch")
review_path = os.path.abspath(str(result.get("review_path") or ""))
if not os.path.isfile(review_path):
    raise SystemExit("manual review result path is missing")
expected_hash = str(result.get("review_sha256") or "")
if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
    raise SystemExit("manual review result hash is missing or invalid")
with open(review_path, "rb") as stream:
    review_bytes = stream.read()
actual_hash = hashlib.sha256(review_bytes).hexdigest()
if actual_hash != expected_hash:
    raise SystemExit("manual review result hash mismatch")
first_line = review_bytes.splitlines()[0].decode("utf-8") if review_bytes else ""
match = re.search(r"\| session: ([^| ]+) \|", first_line)
if not match or match.group(1) != session_id:
    raise SystemExit("review metadata session mismatch")
sys.stdout.buffer.write(review_bytes)
PY
    exit $?
fi

PROJECT_DIR="${1:-$(pwd)}"
MAP_FILE="${STATE_DIR}/session_map.json"

# Method 1: Look up session from session_map.json
if [ -f "$MAP_FILE" ]; then
    SESSION_ID=$(OW_STATE="$STATE_DIR" OW_PROJECT="$PROJECT_DIR" OW_REVIEWS="$REVIEWS_DIR" OW_DIR="$OVERWATCH_DIR" python3 -c "
import os, sys
sys.path.insert(0, os.environ['OW_DIR'])
from session_registry import sessions_for_project
matches = [
    sid for sid in sessions_for_project(os.environ['OW_STATE'], os.environ['OW_PROJECT'])
    if os.path.isfile(os.path.join(os.environ['OW_REVIEWS'], sid, 'latest.md'))
]
print(matches[0] if len(matches) == 1 else ('__AMBIGUOUS__' if len(matches) > 1 else ''))
" 2>/dev/null)

    if [ "$SESSION_ID" = "__AMBIGUOUS__" ]; then
        echo "find_review: multiple sessions match this project; use an exact manual result" >&2
        exit 1
    fi

    if [ -n "$SESSION_ID" ]; then
        REVIEW_FILE="${REVIEWS_DIR}/${SESSION_ID}/latest.md"
        if [ -f "$REVIEW_FILE" ]; then
            echo "$REVIEW_FILE"
            exit 0
        fi
    fi
fi

# Method 2: Fallback — project-scoped current review symlink.
PROJECT_NAME="$(basename "${PROJECT_DIR%/}")"
PROJECT_FALLBACK="${REVIEWS_DIR}/_current_${PROJECT_NAME}.md"
if [ -f "$PROJECT_FALLBACK" ] && head -n 1 "$PROJECT_FALLBACK" 2>/dev/null | grep -qF "project: ${PROJECT_DIR} "; then
    echo "$PROJECT_FALLBACK"
    exit 0
fi

# Method 3: Last resort — global _current.md only if its metadata matches this project.
FALLBACK="${REVIEWS_DIR}/_current.md"
if [ -f "$FALLBACK" ] && head -n 1 "$FALLBACK" 2>/dev/null | grep -qF "project: ${PROJECT_DIR} "; then
    echo "$FALLBACK"
fi
