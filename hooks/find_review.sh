#!/bin/bash
# Find the latest Overwatch review for the current project.
# Usage: bash hooks/find_review.sh [project_directory]
# Output: Review file path (if exists)

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="${1:-$(pwd)}"
MAP_FILE="${OVERWATCH_DIR}/state/session_map.json"

# Method 1: Look up session from session_map.json
if [ -f "$MAP_FILE" ]; then
    SESSION_ID=$(OW_MAP="$MAP_FILE" OW_PROJECT="$PROJECT_DIR" python3 -c "
import json, os
with open(os.environ['OW_MAP']) as f:
    m = json.load(f)
project_dir = os.environ['OW_PROJECT']
sid = m.get(project_dir, '')
if not sid:
    for k, v in sorted(m.items(), key=lambda x: -len(x[0])):
        if project_dir.startswith(k):
            sid = v
            break
print(sid)
" 2>/dev/null)

    if [ -n "$SESSION_ID" ]; then
        REVIEW_FILE="${OVERWATCH_DIR}/reviews/${SESSION_ID}/latest.md"
        if [ -f "$REVIEW_FILE" ]; then
            echo "$REVIEW_FILE"
            exit 0
        fi
    fi
fi

# Method 2: Fallback — _current.md symlink
FALLBACK="${OVERWATCH_DIR}/reviews/_current.md"
if [ -f "$FALLBACK" ]; then
    echo "$FALLBACK"
fi
