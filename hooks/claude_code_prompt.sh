#!/bin/bash
# Overwatch UserPromptSubmit Hook for Claude Code
# Two responsibilities:
#   1. Deliver pending auto-reviews via additionalContext (runs on every message)
#   2. Detect manual trigger keywords → inject review command via additionalContext
# Primary delivery: hookSpecificOutput.additionalContext (injected into AI context)
# Fallback: state/latest_trigger.json (for environments without additionalContext)

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
    # Primary: inject review content via additionalContext (reaches AI context)
    # Fallback: write trigger file for environments without additionalContext support
    OUTPUT=$(OW_STATE="$STATE_DIR" OW_PENDING="$PENDING_FILE" python3 -c "
import json, os
state_dir = os.environ['OW_STATE']
pending = json.load(open(os.environ['OW_PENDING']))
review_path = pending['review_path']
session_id = pending.get('session_id', '')

# Write trigger file as fallback
trigger = {'type': 'auto_review', 'review_path': review_path, 'session_id': session_id}
with open(os.path.join(state_dir, 'latest_trigger.json'), 'w') as f:
    json.dump(trigger, f)

# Read review content
try:
    with open(review_path, 'r') as f:
        content = f.read()
except Exception:
    content = '[Overwatch] Review file not found: ' + review_path

# Truncate to stay within additionalContext limit (10k chars)
if len(content) > 9500:
    content = content[:9500] + '\n\n... [truncated]'

output = {
    'continue': True,
    'systemMessage': '[Overwatch] Auto-review delivered.',
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': '[Overwatch Auto-Review] Present this review verbatim, then respond point by point.\n\n' + content
    }
}
print(json.dumps(output))
" 2>/dev/null || echo '{"continue": true, "systemMessage": "[Overwatch] Auto-review ready."}')
    rm -f "$PENDING_FILE"
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

# Primary: inject trigger info via additionalContext (reaches AI context)
# Fallback: write trigger file for environments without additionalContext support
OUTPUT=$(OW_STATE="$STATE_DIR" OW_SID="$SESSION_ID" OW_TRANSCRIPT="$TRANSCRIPT" OW_CWD="$CWD" OW_DIR="$OVERWATCH_DIR" python3 -c "
import json, os
sid = os.environ['OW_SID']
transcript = os.environ['OW_TRANSCRIPT']
cwd = os.environ['OW_CWD']
ow_dir = os.environ['OW_DIR']
state_dir = os.environ['OW_STATE']

# Write trigger file as fallback
trigger = {
    'type': 'manual_trigger',
    'session_id': sid,
    'transcript_path': transcript,
    'cwd': cwd,
    'overwatch_dir': ow_dir
}
with open(os.path.join(state_dir, 'latest_trigger.json'), 'w') as f:
    json.dump(trigger, f)

# Inject review instructions via additionalContext
context = (
    '[Overwatch Manual Trigger] Run this review now:\n'
    'python3 {dir}/overwatch.py --session-id \"{sid}\" '
    '--transcript \"{transcript}\" --cwd \"{cwd}\" --force 2>&1\n'
    'Then read: bash {dir}/hooks/find_review.sh \"{cwd}\"\n'
    'Present the full review verbatim, then respond point by point.'
).format(dir=ow_dir, sid=sid, transcript=transcript, cwd=cwd)

output = {
    'continue': True,
    'systemMessage': '[Overwatch] Review triggered.',
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': context
    }
}
print(json.dumps(output))
" 2>/dev/null || echo '{"continue": true, "systemMessage": "[Overwatch] Review triggered."}')

exit 0
