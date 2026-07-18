#!/bin/bash
# Overwatch Stop hook for Codex Desktop.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OVERWATCH_PY="${OVERWATCH_DIR}/overwatch.py"
STATE_DIR="${OVERWATCH_STATE_DIR:-${OVERWATCH_DIR}/state}"
LOG_FILE="${OVERWATCH_LOG_FILE:-${OVERWATCH_DIR}/overwatch.log}"

OUTPUT='{"continue": true}'
cleanup() {
    echo "$OUTPUT"
}
trap cleanup EXIT

INPUT=$(cat)
[ ! -L "$STATE_DIR" ] || exit 0
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

write_stop_status() {
    local status="$1"
    local reason="$2"
    local current_turns="${3:-}"
    local last_reviewed="${4:-}"
    local review_count="${5:-}"
    [ -z "${SESSION_ID:-}" ] && return 0
    OW_STATE_DIR="$STATE_DIR" OW_DIR="$OVERWATCH_DIR" OW_SID="$SESSION_ID" OW_CWD="${CWD:-}" OW_TRANSCRIPT="${TRANSCRIPT_PATH:-}" \
    OW_STATUS="$status" OW_REASON="$reason" OW_CURRENT_TURNS="$current_turns" \
    OW_LAST_REVIEWED="$last_reviewed" OW_REVIEW_COUNT="$review_count" python3 -c "
import datetime as dt
import json
import os
import sys
import tempfile

state_dir = os.environ['OW_STATE_DIR']
sid = os.environ['OW_SID']
cwd = os.environ.get('OW_CWD', '')
sys.path.insert(0, os.environ['OW_DIR'])
try:
    from config import ALLOWED_PROJECTS, project_is_allowed
except Exception:
    ALLOWED_PROJECTS = []
    project_is_allowed = lambda value: False

if not ALLOWED_PROJECTS:
    scope = 'active/global'
elif project_is_allowed(cwd):
    scope = 'active/allowed'
else:
    scope = 'disabled/project'

def as_int(value):
    try:
        return int(value)
    except Exception:
        return 0

payload = {
    'session_id': sid,
    'cwd': cwd,
    'transcript_path': os.environ.get('OW_TRANSCRIPT', ''),
    'status': os.environ['OW_STATUS'],
    'reason': os.environ['OW_REASON'],
    'scope': scope,
    'current_turns': as_int(os.environ.get('OW_CURRENT_TURNS', '')),
    'last_reviewed_turn': as_int(os.environ.get('OW_LAST_REVIEWED', '')),
    'review_count': as_int(os.environ.get('OW_REVIEW_COUNT', '')),
    'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
}
path = os.path.join(state_dir, f'stop_status_{sid}.json')
fd, tmp = tempfile.mkstemp(dir=state_dir, suffix='.tmp')
with os.fdopen(fd, 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
os.replace(tmp, path)
" 2>/dev/null || true
    { echo "[Overwatch Codex Stop] status=$status reason=$reason session=$SESSION_ID" >> "$LOG_FILE"; } 2>/dev/null || true
}

SESSION_ID=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('session_id') or os.environ.get('CODEX_THREAD_ID',''))" 2>/dev/null || echo "")
SESSION_VALID=$(OW_DIR="$OVERWATCH_DIR" OW_SID="$SESSION_ID" python3 -c "
import os, sys; sys.path.insert(0, os.environ['OW_DIR'])
from config import valid_session_id
print('true' if valid_session_id(os.environ.get('OW_SID', '')) else 'false')
" 2>/dev/null || echo "false")
if [ "$SESSION_VALID" != "true" ]; then
    SESSION_ID=""
fi
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('cwd') or os.getcwd())" 2>/dev/null || pwd)
PROJECT_ROOT=$(OW_DIR="$OVERWATCH_DIR" OW_CWD="$CWD" python3 -c "
import os, sys; sys.path.insert(0, os.environ['OW_DIR'])
from runtime_fs import canonical_project_root
print(canonical_project_root(os.environ.get('OW_CWD', '')))
" 2>/dev/null || echo "")

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

if [ -z "$SESSION_ID" ]; then
    exit 0
fi
if [ -z "$TRANSCRIPT_PATH" ]; then
    write_stop_status "skipped" "missing_transcript"
    exit 0
fi

ALLOWED=$(OW_DIR="$OVERWATCH_DIR" OW_CWD="$CWD" python3 -c "
import os, sys; sys.path.insert(0, os.environ['OW_DIR'])
from config import project_is_allowed
cwd = os.environ.get('OW_CWD', '')
print('yes' if project_is_allowed(cwd) else 'no')
" 2>/dev/null || echo "yes")
if [ "$ALLOWED" = "no" ]; then
    write_stop_status "skipped" "disallowed_project"
    exit 0
fi

if ! OW_DIR="$OVERWATCH_DIR" OW_STATE_DIR="$STATE_DIR" OW_CWD="$PROJECT_ROOT" OW_SID="$SESSION_ID" python3 -c "
import os, sys
sys.path.insert(0, os.environ['OW_DIR'])
from session_registry import record_session
record_session(os.environ['OW_STATE_DIR'], os.environ['OW_CWD'], os.environ['OW_SID'])
" 2>>"$LOG_FILE"; then
    write_stop_status "skipped" "project_scope_mismatch"
    OUTPUT='{"continue": true, "systemMessage": "[Overwatch] Session belongs to another project; review dispatch blocked."}'
    exit 0
fi

PENDING_FILE="${STATE_DIR}/auto_review_pending_${SESSION_ID}.json"
LOCK_FILE="${STATE_DIR}/${SESSION_ID}.lock"
if [ -f "$PENDING_FILE" ]; then
    PENDING_ACTION=$(OW_DIR="$OVERWATCH_DIR" OW_PENDING="$PENDING_FILE" OW_SID="$SESSION_ID" OW_ROOT="$PROJECT_ROOT" python3 - <<'PY' 2>/dev/null || echo "invalid_marker"
import os
import sys

sys.path.insert(0, os.environ["OW_DIR"])
from pending_review import cleanup_expired_pending

status = cleanup_expired_pending(
    os.environ["OW_PENDING"],
    expected_session_id=os.environ["OW_SID"],
    expected_project_root=os.environ["OW_ROOT"],
)
print("deliver" if status.get("deliverable") else (status.get("reason") or "invalid_marker"))
PY
)
    if [ "$PENDING_ACTION" = "deliver" ]; then
        # Keep Stop UI quiet. The prompt hook delivers the review on the next turn,
        # and stop-says summarizes the state in one final status line.
        write_stop_status "skipped" "pending_review"
        OUTPUT='{"continue": true}'
        exit 0
    fi
    if [ "$PENDING_ACTION" = "expired" ]; then
        { echo "[Overwatch Codex Stop] Expired auto-review pending discarded (session=$SESSION_ID)" >> "$LOG_FILE"; } 2>/dev/null || true
    elif [ "$PENDING_ACTION" = "missing_review" ]; then
        write_stop_status "skipped" "pending_review_missing"
        OUTPUT='{"continue": true, "systemMessage": "[Overwatch] Review file missing; pending marker preserved for retry."}'
        exit 0
    else
        write_stop_status "skipped" "pending_marker_invalid"
        OUTPUT='{"continue": true, "systemMessage": "[Overwatch] Auto-review marker unreadable; pending evidence preserved."}'
        exit 0
    fi
fi
if OW_DIR="$OVERWATCH_DIR" OW_STATE_DIR="$STATE_DIR" OW_SID="$SESSION_ID" python3 -c "
import os, sys
sys.path.insert(0, os.environ['OW_DIR'])
from session_registry import session_lock_active
raise SystemExit(0 if session_lock_active(os.environ['OW_STATE_DIR'], os.environ['OW_SID']) else 1)
" 2>/dev/null; then
    write_stop_status "skipped" "review_in_progress"
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

TRIGGER_DECISION=$(OW_DIR="$OVERWATCH_DIR" OW_TRANSCRIPT="$TRANSCRIPT_PATH" OW_LAST_REVIEWED="$LAST_REVIEWED" OW_REVIEW_COUNT="$REVIEW_COUNT" \
OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec python3 - <<'PY' 2>/dev/null || echo '{"should_trigger": false, "reason": "trigger_policy_error", "current_turns": 0, "last_reviewed_turn": 0, "review_count": 0, "remaining": 0, "signal": ""}'
import json
import os
import sys

sys.path.insert(0, os.environ["OW_DIR"])
from adapters import get_adapter
from config import ADAPTER, SMART_TRIGGER, TURN_THRESHOLD, TURN_THRESHOLD_MAX, TURN_THRESHOLD_MIN
from trigger_policy import evaluate_trigger, summarize_turns_for_policy

turns = get_adapter(ADAPTER)(os.environ["OW_TRANSCRIPT"])
summary = summarize_turns_for_policy(turns)
decision = evaluate_trigger(
    current_turns=summary["user_count"],
    last_reviewed_turn=os.environ.get("OW_LAST_REVIEWED", "0"),
    review_count=os.environ.get("OW_REVIEW_COUNT", "0"),
    tool_names=summary["tool_names"],
    user_contents=summary["user_contents"],
    command_contents=summary["command_contents"],
    turn_threshold=TURN_THRESHOLD,
    smart_trigger=SMART_TRIGGER,
    turn_min=TURN_THRESHOLD_MIN,
    turn_max=TURN_THRESHOLD_MAX,
)
print(json.dumps(decision, ensure_ascii=False))
PY
)

SHOULD_TRIGGER=$(printf '%s' "$TRIGGER_DECISION" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('should_trigger') else 'false')" 2>/dev/null || echo "false")
DECISION_REASON=$(printf '%s' "$TRIGGER_DECISION" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason','trigger_policy_error'))" 2>/dev/null || echo "trigger_policy_error")
CURRENT_TURNS=$(printf '%s' "$TRIGGER_DECISION" | python3 -c "import json,sys; print(json.load(sys.stdin).get('current_turns',0))" 2>/dev/null || echo "0")
DECISION_SIGNAL=$(printf '%s' "$TRIGGER_DECISION" | python3 -c "import json,sys; print(json.load(sys.stdin).get('signal',''))" 2>/dev/null || echo "")

if [ "$SHOULD_TRIGGER" != "true" ]; then
    write_stop_status "skipped" "$DECISION_REASON" "$CURRENT_TURNS" "$LAST_REVIEWED" "$REVIEW_COUNT"
    OUTPUT='{"continue": true}'
    exit 0
fi

OUTPUT='{"continue": true}'
write_stop_status "triggered" "review_dispatched" "$CURRENT_TURNS" "$LAST_REVIEWED" "$REVIEW_COUNT"
{ echo "[Overwatch Codex Stop] Dispatching auto review (session=$SESSION_ID reason=$DECISION_REASON signal=$DECISION_SIGNAL)" >> "$LOG_FILE"; } 2>/dev/null || true
if [ "${OVERWATCH_TEST_DISABLE_DISPATCH:-}" = "1" ]; then
    { echo "[Overwatch Codex Stop] Dispatch disabled by OVERWATCH_TEST_DISABLE_DISPATCH (session=$SESSION_ID)" >> "$LOG_FILE"; } 2>/dev/null || true
    exit 0
fi
OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec OVERWATCH_REVIEW_MODEL="${OVERWATCH_REVIEW_MODEL:-gpt-5.5}" nohup python3 "$OVERWATCH_PY" \
    --session-id "$SESSION_ID" \
    --transcript "$TRANSCRIPT_PATH" \
    --cwd "$PROJECT_ROOT" \
    >> "$LOG_FILE" 2>&1 &

exit 0
