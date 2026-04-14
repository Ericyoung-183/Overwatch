#!/bin/bash
# Overwatch UserPromptSubmit Hook for Claude Code
# Two responsibilities:
#   1. Deliver pending auto-reviews via trigger file (runs on every message)
#   2. Detect manual trigger keywords → write trigger file with session info

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${OVERWATCH_DIR}/state"

# Default output
OUTPUT='{"continue": true}'
cleanup() {
    echo "$OUTPUT"
}
trap cleanup EXIT

# Read stdin
INPUT=$(cat)

mkdir -p "$STATE_DIR"

# Extract session_id early (needed by both phases)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null || echo "")

# --- Phase 1: Check for pending auto-review (per-session) ---
PENDING_FILE="${STATE_DIR}/auto_review_pending_${SESSION_ID}.json"
if [ -n "$SESSION_ID" ] && [ -f "$PENDING_FILE" ]; then
    # Copy pending info into latest_trigger.json for Builder to pick up
    OW_STATE="$STATE_DIR" OW_PENDING="$PENDING_FILE" python3 -c "
import json, os
pending = json.load(open(os.environ['OW_PENDING']))
trigger = {
    'type': 'auto_review',
    'review_path': pending['review_path'],
    'session_id': pending.get('session_id', '')
}
with open(os.path.join(os.environ['OW_STATE'], 'latest_trigger.json'), 'w') as f:
    json.dump(trigger, f)
" 2>/dev/null
    rm -f "$PENDING_FILE"
    OUTPUT='{"continue": true, "systemMessage": "[Overwatch] Auto-review ready."}'
    exit 0
fi

# --- Phase 2: Check for manual trigger keyword ---
# Extract user prompt (field name varies by Claude Code distribution)
USER_PROMPT=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('user_prompt', d.get('prompt', '')))" 2>/dev/null || echo "")

# Check if prompt matches a trigger keyword (exact match, case-insensitive, trimmed)
MATCHED=$(USER_PROMPT="$USER_PROMPT" python3 -c "
import os, sys; sys.path.insert(0, '$OVERWATCH_DIR')
from config import TRIGGER_KEYWORDS
prompt = os.environ.get('USER_PROMPT', '').strip().lower()
print('true' if prompt in [k.lower() for k in TRIGGER_KEYWORDS] else 'false')
" 2>/dev/null || echo "false")

if [ "$MATCHED" != "true" ]; then
    exit 0
fi

# Extract remaining session info for manual trigger
TRANSCRIPT=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('cwd',''))" 2>/dev/null || echo "")

# Write trigger file for manual review
OW_STATE="$STATE_DIR" OW_SID="$SESSION_ID" OW_TRANSCRIPT="$TRANSCRIPT" OW_CWD="$CWD" OW_DIR="$OVERWATCH_DIR" python3 -c "
import json, os
trigger = {
    'type': 'manual_trigger',
    'session_id': os.environ['OW_SID'],
    'transcript_path': os.environ['OW_TRANSCRIPT'],
    'cwd': os.environ['OW_CWD'],
    'overwatch_dir': os.environ['OW_DIR']
}
with open(os.path.join(os.environ['OW_STATE'], 'latest_trigger.json'), 'w') as f:
    json.dump(trigger, f)
" 2>/dev/null

# Brief confirmation visible to user
OUTPUT='{"continue": true, "systemMessage": "[Overwatch] Review triggered."}'

exit 0
