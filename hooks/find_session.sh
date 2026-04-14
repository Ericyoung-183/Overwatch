#!/bin/bash
# Find the current project's active session ID and transcript path.
# Usage: bash hooks/find_session.sh [project_directory]
# Output: SESSION_ID TRANSCRIPT_PATH (space-separated)

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="${1:-$(pwd)}"
MAP_FILE="${OVERWATCH_DIR}/state/session_map.json"

# Method 1: Look up session_map.json (written by Stop hook on each fire)
if [ -f "$MAP_FILE" ]; then
    RESULT=$(OW_DIR="$OVERWATCH_DIR" OW_MAP="$MAP_FILE" OW_PROJECT="$PROJECT_DIR" python3 -c "
import json, os, sys
sys.path.insert(0, os.environ['OW_DIR'])
from config import CC_PROJECTS_BASE, CC_PROJECTS_FALLBACKS

with open(os.environ['OW_MAP']) as f:
    m = json.load(f)

project_dir = os.environ['OW_PROJECT']
sid = m.get(project_dir, '')
if not sid:
    for k, v in sorted(m.items(), key=lambda x: -len(x[0])):
        if project_dir.startswith(k):
            sid = v
            break

if sid:
    search_dirs = [CC_PROJECTS_BASE] + CC_PROJECTS_FALLBACKS
    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for d in os.listdir(base):
            t = os.path.join(base, d, sid + '.jsonl')
            if os.path.exists(t):
                print(f'{sid} {t}')
                exit(0)
" 2>/dev/null)

    if [ -n "$RESULT" ]; then
        echo "$RESULT"
        exit 0
    fi
fi

# Method 2: Fallback — find JSONL by scanning project dirs (only if exactly one match)
OW_DIR="$OVERWATCH_DIR" OW_PROJECT="$PROJECT_DIR" python3 -c "
import os, sys, json
sys.path.insert(0, os.environ['OW_DIR'])
from config import CC_PROJECTS_BASE, CC_PROJECTS_FALLBACKS

project_dir = os.environ['OW_PROJECT']
basename = os.path.basename(project_dir.rstrip('/'))
search_dirs = [CC_PROJECTS_BASE] + CC_PROJECTS_FALLBACKS
candidates = []

for base in search_dirs:
    if not os.path.isdir(base):
        continue
    for d in os.listdir(base):
        project_path = os.path.join(base, d)
        if not os.path.isdir(project_path):
            continue
        if basename not in d:
            continue
        for j in os.listdir(project_path):
            if j.endswith('.jsonl'):
                full = os.path.join(project_path, j)
                candidates.append((os.path.getmtime(full), full))

if len(candidates) == 1:
    path = candidates[0][1]
    sid = os.path.basename(path).replace('.jsonl', '')
    print(f'{sid} {path}')
elif len(candidates) == 0:
    sys.exit(1)
else:
    # Multiple sessions — refuse to guess, exit with error
    import sys as _sys
    print(f'ERROR: {len(candidates)} sessions found for this project. Use trigger file or specify session_id.', file=_sys.stderr)
    sys.exit(1)
" 2>/dev/null
