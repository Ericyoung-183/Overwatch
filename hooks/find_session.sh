#!/bin/bash
# Find the current project's active session ID and transcript path.
# Usage: bash hooks/find_session.sh [--json] [project_directory]
# Output: SESSION_ID TRANSCRIPT_PATH, or a JSON object with --json.

if [ "${1:-}" = "--json" ]; then
    shift
    RESULT=$(bash "$0" "${1:-$(pwd)}")
    STATUS=$?
    [ "$STATUS" -eq 0 ] || exit "$STATUS"
    python3 - "$RESULT" <<'PY'
import json
import sys

session_id, separator, transcript_path = sys.argv[1].partition(" ")
if not separator or not session_id or not transcript_path:
    raise SystemExit("find_session returned an invalid session record")
print(json.dumps(
    {"session_id": session_id, "transcript_path": transcript_path},
    ensure_ascii=False,
))
PY
    exit $?
fi

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="${1:-$(pwd)}"
STATE_DIR="${OVERWATCH_STATE_DIR:-${OVERWATCH_DIR}/state}"
MAP_FILE="${STATE_DIR}/session_map.json"

# Method 1: Prefer the exact live Codex thread identity when it is available.
if [ -n "${CODEX_THREAD_ID:-}" ]; then
    RESULT=$(OW_SID="$CODEX_THREAD_ID" python3 -c "
import os
sid = os.environ.get('OW_SID', '')
base = os.path.expanduser('~/.codex/sessions')
matches = []
if sid and os.path.isdir(base):
    for root, _, files in os.walk(base):
        for name in files:
            if name.endswith('.jsonl') and sid in name:
                path = os.path.join(root, name)
                matches.append((os.path.getmtime(path), path))
if matches:
    print(f'{sid} {sorted(matches)[-1][1]}')
" 2>/dev/null)

    if [ -n "$RESULT" ]; then
        echo "$RESULT"
        exit 0
    fi
fi

# Method 2: Look up an exact project match in session_map.json.
if [ -f "$MAP_FILE" ]; then
    RESULT=$(OW_DIR="$OVERWATCH_DIR" OW_STATE="$STATE_DIR" OW_PROJECT="$PROJECT_DIR" python3 -c "
import os, sys
sys.path.insert(0, os.environ['OW_DIR'])
from config import CC_PROJECTS_BASE, CC_PROJECTS_FALLBACKS
from session_registry import sessions_for_project

sessions = sessions_for_project(os.environ['OW_STATE'], os.environ['OW_PROJECT'])
if len(sessions) > 1:
    print(f'__AMBIGUOUS__:{len(sessions)}')
    raise SystemExit(0)
sid = sessions[0] if len(sessions) == 1 else ''

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

    base = os.path.expanduser('~/.codex/sessions')
    matches = []
    if os.path.isdir(base):
        for root, _, files in os.walk(base):
            for name in files:
                if name.endswith('.jsonl') and sid in name:
                    path = os.path.join(root, name)
                    matches.append((os.path.getmtime(path), path))
    if matches:
        print(f'{sid} {sorted(matches)[-1][1]}')
" 2>/dev/null)

    case "$RESULT" in
        __AMBIGUOUS__:*)
            echo "ERROR: ${RESULT#__AMBIGUOUS__:} sessions found for this project. Use exact session context." >&2
            exit 1
            ;;
    esac

    if [ -n "$RESULT" ]; then
        echo "$RESULT"
        exit 0
    fi

fi

# Method 3: Fallback — find JSONL by scanning project dirs (only if exactly one match)
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
