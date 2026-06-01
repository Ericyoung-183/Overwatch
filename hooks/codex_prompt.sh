#!/bin/bash
# Overwatch UserPromptSubmit hook for Codex Desktop.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${OVERWATCH_STATE_DIR:-${OVERWATCH_DIR}/state}"
LOG_FILE="${OVERWATCH_LOG_FILE:-${OVERWATCH_DIR}/overwatch.log}"

OUTPUT='{"continue": true}'
cleanup() {
    echo "$OUTPUT"
}
trap cleanup EXIT

INPUT=$(cat)
mkdir -p "$STATE_DIR"

SESSION_ID=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('session_id') or os.environ.get('CODEX_THREAD_ID',''))" 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python3 -c "import os,sys,json; d=json.load(sys.stdin); print(d.get('cwd') or os.getcwd())" 2>/dev/null || pwd)

render_anchor_context() {
    case "${ANCHOR_DISABLE:-}" in
        1|true|TRUE|yes|YES) return 0 ;;
    esac

    local helper="${ANCHOR_HELPER:-}"
    if [ -z "$helper" ]; then
        local installed_helper="${HOME:-}/.codex/skills/anchor/scripts/anchor.py"
        if [ -f "$installed_helper" ]; then
            helper="$installed_helper"
        fi
    fi
    [ -z "$helper" ] && return 0
    [ -z "$SESSION_ID" ] && return 0
    [ ! -f "$helper" ] && return 0

    local args=(python3 "$helper" render-context --cwd "$CWD" --thread-id "$SESSION_ID")
    if [ -n "${ANCHOR_GLOBAL_STATE_ROOT:-}" ]; then
        args+=(--global-state-root "$ANCHOR_GLOBAL_STATE_ROOT")
    fi
    if [ -n "${ANCHOR_MAX_CONTEXT_CHARS:-}" ]; then
        args+=(--max-context-chars "$ANCHOR_MAX_CONTEXT_CHARS")
    fi
    if [ -n "${ANCHOR_STALE_AFTER_MINUTES:-}" ]; then
        args+=(--stale-after-minutes "$ANCHOR_STALE_AFTER_MINUTES")
    fi
    "${args[@]}" 2>/dev/null || true
}

anchor_helper_path() {
    local helper="${ANCHOR_HELPER:-}"
    if [ -z "$helper" ]; then
        local installed_helper="${HOME:-}/.codex/skills/anchor/scripts/anchor.py"
        if [ -f "$installed_helper" ]; then
            helper="$installed_helper"
        fi
    fi
    [ -n "$helper" ] && [ -f "$helper" ] && printf '%s\n' "$helper"
}

render_anchor_todo_bridge_reminder() {
    case "${ANCHOR_DISABLE:-}" in
        1|true|TRUE|yes|YES) return 0 ;;
    esac
    [ -z "$(anchor_helper_path)" ] && return 0
    USER_PROMPT="$USER_PROMPT" python3 - <<'PY'
import os
prompt = os.environ.get("USER_PROMPT", "")
lower = prompt.lower()
direct_needles = ["todo", "待办", "任务清单", "task list", "tasks"]
context_needles = ["还有哪些", "没做", "继续处理", "未完成", "remaining", "open"]
todo_terms = ["todo", "待办", "任务", "task"]
direct_match = any(needle in lower or needle in prompt for needle in direct_needles)
contextual_match = (
    any(needle in lower or needle in prompt for needle in context_needles)
    and any(term in lower or term in prompt for term in todo_terms)
)
if not (direct_match or contextual_match):
    raise SystemExit(0)
print(
    "[Anchor Todo Bridge]\n"
    "User prompt mentions project TODO. Before answering, use the Anchor helper for TODO work: "
    "run `todo-status --cwd <project>`; if it reports multiple candidates, ask the user to choose and run "
    "`todo-configure`; if open items should be processed, run `todo-start`; after a TODO-backed agenda closes, "
    "run `todo-sync`. Do not hand-edit `.anchor/config.json` or treat non-canonical TODO files as active sources."
)
PY
}

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

if [ -n "$SESSION_ID" ] && [ -n "$CWD" ]; then
    OW_STATE_DIR="$STATE_DIR" OW_CWD="$CWD" OW_SID="$SESSION_ID" python3 -c "
import json, os, tempfile
state_dir = os.environ['OW_STATE_DIR']
map_file = os.path.join(state_dir, 'session_map.json')
m = {}
if os.path.exists(map_file):
    with open(map_file, encoding='utf-8') as f:
        m = json.load(f)
m[os.environ['OW_CWD']] = os.environ['OW_SID']
fd, tmp = tempfile.mkstemp(dir=state_dir, suffix='.tmp')
with os.fdopen(fd, 'w', encoding='utf-8') as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
os.replace(tmp, map_file)
" 2>/dev/null || true
fi

PENDING_FILE="${STATE_DIR}/auto_review_pending_${SESSION_ID}.json"
{ echo "[Overwatch Codex Prompt $(date +%H:%M:%S)] Hook fired (session=$SESSION_ID, pending_exists=$([ -f "$PENDING_FILE" ] && echo yes || echo no))" >> "$LOG_FILE"; } 2>/dev/null || true

if [ -n "$SESSION_ID" ] && [ -f "$PENDING_FILE" ]; then
    OUTPUT=$(OW_STATE="$STATE_DIR" OW_PENDING="$PENDING_FILE" OW_DIR="$OVERWATCH_DIR" python3 -c "
import json, os, sys
state_dir = os.environ['OW_STATE']
sys.path.insert(0, os.environ['OW_DIR'])
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
    ANCHOR_CONTEXT=$(render_anchor_context)
    if [ -z "$ANCHOR_CONTEXT" ]; then
        ANCHOR_CONTEXT=$(render_anchor_todo_bridge_reminder)
    fi
    SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9_.-' '_')
    STATUS_RELAY_DIR="${OVERWATCH_CODEX_STATUS_RELAY_DIR:-}"
    STATUS_RELAY_FILE="${OVERWATCH_CODEX_STATUS_RELAY_FILE:-}"
    if [ -z "$STATUS_RELAY_FILE" ] && [ -n "$STATUS_RELAY_DIR" ]; then
        STATUS_RELAY_FILE="${STATUS_RELAY_DIR%/}/last_stop_says_${SAFE_SESSION_ID}.json"
    fi
    if [ -n "$SESSION_ID" ] && [ -n "$STATUS_RELAY_FILE" ] && [ -f "$STATUS_RELAY_FILE" ]; then
        OUTPUT=$(OW_STATUS_RELAY_FILE="$STATUS_RELAY_FILE" OW_ANCHOR_CONTEXT="$ANCHOR_CONTEXT" python3 -c "
import json
import os

path = os.environ['OW_STATUS_RELAY_FILE']
with open(path, encoding='utf-8') as f:
    payload = json.load(f)
message = str(payload.get('systemMessage', '') or '').strip()
if not message:
    raise SystemExit(1)
context = (
    '<system-reminder>\\n'
    '[Stop Says Previous Turn]\\n'
    'The previous Codex Stop hook status may not have been visible in the UI. '
    'Before answering the user, show this status in one short line exactly once:\\n'
    f'{message}\\n'
    '</system-reminder>'
)
anchor = os.environ.get('OW_ANCHOR_CONTEXT', '').strip()
if anchor:
    context = context + '\\n\\n<system-reminder>\\n' + anchor + '\\n</system-reminder>'
print(json.dumps({
    'continue': True,
    'systemMessage': '[Stop Says] Previous turn status delivered.',
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': context,
    },
}, ensure_ascii=False))
" 2>/dev/null || echo '{"continue": true}')
        rm -f "$STATUS_RELAY_FILE" 2>/dev/null || true
        exit 0
    fi
    if [ -n "$ANCHOR_CONTEXT" ]; then
        OUTPUT=$(OW_ANCHOR_CONTEXT="$ANCHOR_CONTEXT" python3 -c "
import json
import os

anchor_context = os.environ['OW_ANCHOR_CONTEXT'].strip()
context = '<system-reminder>\\n' + anchor_context + '\\n</system-reminder>'
message = '[Anchor] Todo Bridge reminder delivered.' if '[Anchor Todo Bridge]' in anchor_context else '[Anchor] Active agenda context delivered.'
print(json.dumps({
    'continue': True,
    'systemMessage': message,
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': context,
    },
}, ensure_ascii=False))
" 2>/dev/null || echo '{"continue": true}')
        exit 0
    fi
    exit 0
fi

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
