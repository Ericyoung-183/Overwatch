#!/bin/bash
# Overwatch UserPromptSubmit hook for Codex Desktop.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${OVERWATCH_DIR}/state"
LOG_FILE="${OVERWATCH_DIR}/overwatch.log"

OUTPUT='{"continue": true}'
cleanup() {
    echo "$OUTPUT"
}
trap cleanup EXIT

INPUT=$(cat)
mkdir -p "$STATE_DIR"

SESSION_ID=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('session_id') or os.environ.get('CODEX_THREAD_ID',''))" 2>/dev/null || echo "")

find_transcript() {
    OW_SID="$SESSION_ID" python3 - <<'PY'
import os
sid = os.environ.get("OW_SID", "")
if not sid:
    raise SystemExit(1)
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
}

TRANSCRIPT=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || echo "")
if [ -z "$TRANSCRIPT" ]; then
    TRANSCRIPT=$(find_transcript 2>/dev/null || echo "")
fi

PENDING_FILE="${STATE_DIR}/auto_review_pending_${SESSION_ID}.json"
{ echo "[Overwatch Codex Prompt $(date +%H:%M:%S)] Hook fired (session=$SESSION_ID, pending_exists=$([ -f "$PENDING_FILE" ] && echo yes || echo no))" >> "$LOG_FILE"; } 2>/dev/null || true

if [ -n "$SESSION_ID" ] && [ -f "$PENDING_FILE" ]; then
    OUTPUT=$(OW_STATE="$STATE_DIR" OW_PENDING="$PENDING_FILE" python3 -c "
import json, os, sys
state_dir = os.environ['OW_STATE']
sys.path.insert(0, os.path.dirname(state_dir))
from response_protocol import build_auto_review_context
pending = json.load(open(os.environ['OW_PENDING']))
review_path = pending['review_path']
session_id = pending.get('session_id', '')
trigger = {'type': 'auto_review', 'review_path': review_path, 'session_id': session_id}
with open(os.path.join(state_dir, 'latest_trigger.json'), 'w') as f:
    json.dump(trigger, f)
try:
    with open(review_path, 'r', encoding='utf-8') as f:
        content = f.read()
except Exception:
    content = '[Overwatch] Review file not found: ' + review_path
if len(content) > 8500:
    content = content[:8500] + '\n\n... [truncated]'
print(json.dumps({
    'continue': True,
    'systemMessage': '[Overwatch] Auto-review delivered.',
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': build_auto_review_context(
            content,
            cleanup_command='rm -f {dir}/latest_trigger.json'.format(dir=state_dir),
        )
    }
}, ensure_ascii=False))
" 2>/dev/null || echo '{"continue": true, "systemMessage": "[Overwatch] Auto-review ready."}')
    rm -f "$PENDING_FILE"
    exit 0
fi

USER_PROMPT=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('user_prompt') or d.get('prompt') or d.get('message') or '')" 2>/dev/null || echo "")
MATCHED=$(USER_PROMPT="$USER_PROMPT" python3 -c "
import os, sys; sys.path.insert(0, '$OVERWATCH_DIR')
from config import TRIGGER_KEYWORDS
prompt = os.environ.get('USER_PROMPT', '').strip().lower()
print('true' if prompt in [k.lower() for k in TRIGGER_KEYWORDS] else 'false')
" 2>/dev/null || echo "false")

if [ "$MATCHED" != "true" ]; then
    exit 0
fi

CWD=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('cwd') or os.getcwd())" 2>/dev/null || pwd)
OUTPUT=$(OW_STATE="$STATE_DIR" OW_SID="$SESSION_ID" OW_TRANSCRIPT="$TRANSCRIPT" OW_CWD="$CWD" OW_DIR="$OVERWATCH_DIR" python3 -c "
import json, os, sys
sid = os.environ['OW_SID']
transcript = os.environ['OW_TRANSCRIPT']
cwd = os.environ['OW_CWD']
ow_dir = os.environ['OW_DIR']
state_dir = os.environ['OW_STATE']
sys.path.insert(0, ow_dir)
from response_protocol import build_manual_trigger_context
trigger = {
    'type': 'manual_trigger',
    'session_id': sid,
    'transcript_path': transcript,
    'cwd': cwd,
    'overwatch_dir': ow_dir,
    'adapter': 'codex'
}
with open(os.path.join(state_dir, 'latest_trigger.json'), 'w') as f:
    json.dump(trigger, f)
context = build_manual_trigger_context(
    review_command=(
        'OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec OVERWATCH_REVIEW_MODEL=gpt-5.5 '
        'python3 {dir}/overwatch.py --session-id \"{sid}\" --transcript \"{transcript}\" '
        '--cwd \"{cwd}\" --force 2>&1'
    ).format(dir=ow_dir, sid=sid, transcript=transcript, cwd=cwd),
    find_review_command='bash {dir}/hooks/find_review.sh \"{cwd}\"'.format(dir=ow_dir, cwd=cwd),
    cleanup_command='rm -f {dir}/state/latest_trigger.json'.format(dir=ow_dir),
)
print(json.dumps({
    'continue': True,
    'systemMessage': '[Overwatch] Review triggered.',
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': context
    }
}, ensure_ascii=False))
" 2>/dev/null || echo '{"continue": true, "systemMessage": "[Overwatch] Review triggered."}')

exit 0
