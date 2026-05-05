#!/bin/bash
# Overwatch Stop hook for Codex Desktop.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OVERWATCH_PY="${OVERWATCH_DIR}/overwatch.py"
STATE_DIR="${OVERWATCH_DIR}/state"
LOG_FILE="${OVERWATCH_DIR}/overwatch.log"

read TURN_THRESHOLD SMART_TRIGGER TURN_MIN TURN_MAX <<< $(OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec python3 -c "
import sys; sys.path.insert(0,'$OVERWATCH_DIR')
from config import TURN_THRESHOLD, SMART_TRIGGER, TURN_THRESHOLD_MIN, TURN_THRESHOLD_MAX
print(TURN_THRESHOLD, SMART_TRIGGER, TURN_THRESHOLD_MIN, TURN_THRESHOLD_MAX)
" 2>/dev/null || echo "10 False 5 15")

OUTPUT='{"continue": true}'
cleanup() {
    echo "$OUTPUT"
}
trap cleanup EXIT

INPUT=$(cat)
mkdir -p "$STATE_DIR"

SESSION_ID=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('session_id') or os.environ.get('CODEX_THREAD_ID',''))" 2>/dev/null || echo "")
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('cwd') or os.getcwd())" 2>/dev/null || pwd)

if [ -z "$TRANSCRIPT_PATH" ] && [ -n "$SESSION_ID" ]; then
    TRANSCRIPT_PATH=$(OW_SID="$SESSION_ID" python3 - <<'PY' 2>/dev/null || true
import os
sid = os.environ.get("OW_SID", "")
base = os.path.expanduser("~/.codex/sessions")
matches = []
for root, _, files in os.walk(base):
    for name in files:
        if name.endswith(".jsonl") and sid in name:
            path = os.path.join(root, name)
            matches.append((os.path.getmtime(path), path))
if matches:
    print(sorted(matches)[-1][1])
PY
)
fi

if [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ]; then
    exit 0
fi

ALLOWED=$(OW_CWD="$CWD" python3 -c "
import os, sys; sys.path.insert(0, '$OVERWATCH_DIR')
from config import ALLOWED_PROJECTS
cwd = os.environ.get('OW_CWD', '')
if not ALLOWED_PROJECTS:
    print('yes')
elif any(cwd.startswith(p) for p in ALLOWED_PROJECTS):
    print('yes')
else:
    print('no')
" 2>/dev/null || echo "yes")
if [ "$ALLOWED" = "no" ]; then
    exit 0
fi

OW_STATE_DIR="$STATE_DIR" OW_CWD="$CWD" OW_SID="$SESSION_ID" python3 -c "
import json, os, tempfile
state_dir = os.environ['OW_STATE_DIR']
map_file = os.path.join(state_dir, 'session_map.json')
m = {}
if os.path.exists(map_file):
    with open(map_file) as f:
        m = json.load(f)
m[os.environ['OW_CWD']] = os.environ['OW_SID']
fd, tmp = tempfile.mkstemp(dir=state_dir, suffix='.tmp')
with os.fdopen(fd, 'w') as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
os.replace(tmp, map_file)
" 2>/dev/null

PENDING_FILE="${STATE_DIR}/auto_review_pending_${SESSION_ID}.json"
LOCK_FILE="${STATE_DIR}/${SESSION_ID}.lock"
if [ -f "$PENDING_FILE" ]; then
    # Keep Stop UI quiet. The prompt hook delivers the review on the next turn,
    # and stop-says summarizes the state in one final status line.
    OUTPUT='{"continue": true}'
    exit 0
elif [ -f "$LOCK_FILE" ]; then
    OUTPUT='{"continue": true}'
    exit 0
fi

STATE_FILE="${STATE_DIR}/${SESSION_ID}.json"
LAST_REVIEWED=0
REVIEW_COUNT=0
if [ -f "$STATE_FILE" ]; then
    LAST_REVIEWED=$(OW_FILE="$STATE_FILE" python3 -c "import json,os; print(json.load(open(os.environ['OW_FILE'])).get('last_reviewed_turn',0))" 2>/dev/null || echo "0")
    REVIEW_COUNT=$(OW_FILE="$STATE_FILE" python3 -c "import json,os; print(json.load(open(os.environ['OW_FILE'])).get('review_count',0))" 2>/dev/null || echo "0")
fi

CURRENT_TURNS=$(OW_TRANSCRIPT="$TRANSCRIPT_PATH" OVERWATCH_ADAPTER=codex python3 -c "
import os, sys; sys.path.insert(0, '$OVERWATCH_DIR')
from config import ADAPTER
from adapters import get_adapter
turns = get_adapter(ADAPTER)(os.environ['OW_TRANSCRIPT'])
print(len([t for t in turns if t.role == 'user']))
" 2>/dev/null || echo "0")

DIFF=$((CURRENT_TURNS - LAST_REVIEWED))
EFFECTIVE_MAX=${TURN_MAX:-15}
EFFECTIVE_MIN=${TURN_MIN:-5}

if [ "$DIFF" -lt "$EFFECTIVE_MIN" ]; then
    OUTPUT='{"continue": true}'
    exit 0
elif [ "$DIFF" -lt "$EFFECTIVE_MAX" ]; then
    OUTPUT='{"continue": true}'
    exit 0
fi

OUTPUT='{"continue": true}'
{ echo "[Overwatch Codex Stop] Dispatching auto review (session=$SESSION_ID)" >> "$LOG_FILE"; } 2>/dev/null || true
OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec OVERWATCH_REVIEW_MODEL="${OVERWATCH_REVIEW_MODEL:-gpt-5.5}" nohup python3 "$OVERWATCH_PY" \
    --session-id "$SESSION_ID" \
    --transcript "$TRANSCRIPT_PATH" \
    --cwd "$CWD" \
    >> "$LOG_FILE" 2>&1 &

exit 0
