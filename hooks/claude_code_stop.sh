#!/bin/bash
# Overwatch Stop Hook for Claude Code
# Fires after each assistant response. Handles:
#   1. Display pending auto-review results
#   2. Throttle-based auto-review triggering
# Always outputs {"continue": true} — never blocks the Builder.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OVERWATCH_PY="${OVERWATCH_DIR}/overwatch.py"
STATE_DIR="${OVERWATCH_DIR}/state"
LOG_FILE="${OVERWATCH_DIR}/overwatch.log"

# Default output
OUTPUT='{"continue": true}'
cleanup() {
    echo "$OUTPUT"
}
trap cleanup EXIT

# Read stdin (hook input JSON)
INPUT=$(cat)

# Parse key fields
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null || echo "")
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || echo "")

if [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT_PATH" ]; then
    exit 0
fi

CWD=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('cwd',''))" 2>/dev/null || echo "")

# Project whitelist check (empty = all projects allowed)
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

# Write session mapping (cwd -> session_id) for find_review.sh
mkdir -p "$STATE_DIR"
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

# Check for pending auto-review (signal only — delivery handled by UserPromptSubmit hook)
PENDING_FILE="${STATE_DIR}/auto_review_pending_${SESSION_ID}.json"
LOCK_FILE="${STATE_DIR}/${SESSION_ID}.lock"
if [ -f "$PENDING_FILE" ]; then
    PENDING_ACTION=$(OW_DIR="$OVERWATCH_DIR" OW_PENDING="$PENDING_FILE" python3 - <<'PY' 2>/dev/null || echo "deliver"
import os
import sys

sys.path.insert(0, os.environ["OW_DIR"])
from pending_review import cleanup_expired_pending

status = cleanup_expired_pending(os.environ["OW_PENDING"])
print("deliver" if status.get("deliverable") else "expired")
PY
)
    if [ "$PENDING_ACTION" = "deliver" ]; then
        OUTPUT='{"continue": true, "systemMessage": "⏱ '"$(date +%H:%M:%S)"' | [Overwatch] Auto-review ready."}'
        exit 0
    fi
    echo "[Overwatch Hook $(date +%H:%M:%S)] Expired auto-review pending discarded (session=$SESSION_ID)" >> "$LOG_FILE" 2>&1
fi
if [ -f "$LOCK_FILE" ]; then
    OUTPUT='{"continue": true, "systemMessage": "⏱ '"$(date +%H:%M:%S)"' | [Overwatch] Review in progress..."}'
    exit 0
fi

# Skip if last user message was a manual trigger (already handled by UserPromptSubmit hook)
LAST_USER_MSG=$(tail -20 "$TRANSCRIPT_PATH" 2>/dev/null | python3 -c "
import sys, json
sys.path.insert(0, '$OVERWATCH_DIR')
from config import TRIGGER_KEYWORDS
last_user = ''
for line in sys.stdin:
    try:
        obj = json.loads(line.strip())
        if obj.get('type') == 'user' and not obj.get('isMeta'):
            msg = obj.get('message', {}).get('content', '')
            if isinstance(msg, str):
                last_user = msg
    except: pass
trimmed = last_user.strip().lower()[:200]
if trimmed in [k.lower() for k in TRIGGER_KEYWORDS]:
    print('SKIP')
else:
    print(trimmed)
" 2>/dev/null || echo "")

if [ "$LAST_USER_MSG" = "SKIP" ]; then
    exit 0
fi

# Throttle check
STATE_FILE="${STATE_DIR}/${SESSION_ID}.json"
LAST_REVIEWED=0
REVIEW_COUNT=0
if [ -f "$STATE_FILE" ]; then
    LAST_REVIEWED=$(OW_FILE="$STATE_FILE" python3 -c "import json,os; print(json.load(open(os.environ['OW_FILE'])).get('last_reviewed_turn',0))" 2>/dev/null || echo "0")
    REVIEW_COUNT=$(OW_FILE="$STATE_FILE" python3 -c "import json,os; print(json.load(open(os.environ['OW_FILE'])).get('review_count',0))" 2>/dev/null || echo "0")
fi

# Shared trigger policy: below min waits, hard ceiling triggers, smart signals
# can trigger between min and max for both Claude Code and Codex.
TRIGGER_DECISION=$(OW_DIR="$OVERWATCH_DIR" OW_TRANSCRIPT="$TRANSCRIPT_PATH" OW_LAST_REVIEWED="$LAST_REVIEWED" OW_REVIEW_COUNT="$REVIEW_COUNT" \
OVERWATCH_ADAPTER=claude_code python3 - <<'PY' 2>/dev/null || echo '{"should_trigger": false, "reason": "trigger_policy_error", "current_turns": 0, "last_reviewed_turn": 0, "review_count": 0, "remaining": 0, "signal": ""}'
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
REMAINING=$(printf '%s' "$TRIGGER_DECISION" | python3 -c "import json,sys; print(json.load(sys.stdin).get('remaining',0))" 2>/dev/null || echo "0")
DECISION_SIGNAL=$(printf '%s' "$TRIGGER_DECISION" | python3 -c "import json,sys; print(json.load(sys.stdin).get('signal',''))" 2>/dev/null || echo "")

if [ "$SHOULD_TRIGGER" != "true" ]; then
    if [ "$DECISION_REASON" = "below_min_threshold" ]; then
        OUTPUT="{\"continue\": true, \"systemMessage\": \"⏱ $(date +%H:%M:%S) | [Overwatch] ${REVIEW_COUNT} reviews | ${REMAINING}+ turns until next | Type 'overwatch' or '第二意见'\"}"
    else
        OUTPUT="{\"continue\": true, \"systemMessage\": \"⏱ $(date +%H:%M:%S) | [Overwatch] ${REVIEW_COUNT} reviews | ${REMAINING} turns until next | Type 'overwatch' or '第二意见'\"}"
    fi
    exit 0
fi
echo "[Overwatch Hook $(date +%H:%M:%S)] Trigger fired (session=$SESSION_ID reason=$DECISION_REASON signal=$DECISION_SIGNAL)" >> "$LOG_FILE" 2>&1

# Dispatch async review
OUTPUT='{"continue": true, "systemMessage": "⏱ '"$(date +%H:%M:%S)"' | [Overwatch] Review triggered / 审查已触发..."}'
echo "[Overwatch Hook] Dispatching auto review (session=$SESSION_ID)" >> "$LOG_FILE" 2>&1
if [ "${OVERWATCH_TEST_DISABLE_DISPATCH:-}" = "1" ]; then
    echo "[Overwatch Hook] Dispatch disabled by OVERWATCH_TEST_DISABLE_DISPATCH (session=$SESSION_ID)" >> "$LOG_FILE" 2>&1
    exit 0
fi
nohup python3 "$OVERWATCH_PY" \
    --session-id "$SESSION_ID" \
    --transcript "$TRANSCRIPT_PATH" \
    --cwd "$CWD" \
    >> "$LOG_FILE" 2>&1 &

exit 0
