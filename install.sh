#!/bin/bash
# Overwatch installer for Claude Code
# Adds Stop + UserPromptSubmit hooks to Claude Code settings.
# Usage: ./install.sh

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")" && pwd)"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "========================================="
echo "  Overwatch Installer for Claude Code"
echo "========================================="
echo ""

# Step 1: Resolve the settings authority, then keep CLAUDE.md beside it.
SETTINGS_FILE="${CC_SETTINGS_PATH:-}"
CC_DIR=""
if [ -n "$SETTINGS_FILE" ]; then
    CC_DIR="$(cd "$(dirname "$SETTINGS_FILE")" 2>/dev/null && pwd || true)"
    if [ -n "$CC_DIR" ]; then
        SETTINGS_FILE="$CC_DIR/$(basename "$SETTINGS_FILE")"
    fi
else
    EXTRA_CC_DIR="${OVERWATCH_CC_DIR:-}"
    for candidate in "$HOME/.claude" ${EXTRA_CC_DIR:+"$EXTRA_CC_DIR"}; do
        if [ -d "$candidate" ] && [ -f "$candidate/settings.json" ]; then
            CC_DIR="$candidate"
            SETTINGS_FILE="$candidate/settings.json"
            break
        fi
    done
fi

if [ -z "$CC_DIR" ] || [ -z "$SETTINGS_FILE" ]; then
    echo -e "${RED}Error: Could not find Claude Code settings directory.${NC}"
    echo "Checked: ~/.claude/settings.json"
    echo "Set OVERWATCH_CC_DIR to specify a custom Claude Code config directory."
    echo ""
    echo "If Claude Code is installed elsewhere, set CC_SETTINGS_PATH:"
    echo "  CC_SETTINGS_PATH=/path/to/settings.json ./install.sh"
    exit 1
fi

if [ ! -f "$SETTINGS_FILE" ]; then
    echo -e "${RED}Error: Settings file not found: $SETTINGS_FILE${NC}"
    exit 1
fi
echo -e "Found Claude Code config: ${GREEN}$SETTINGS_FILE${NC}"

# Step 2: Verify Overwatch files exist
for f in overwatch.py config.py api_client.py context_manager.py pending_review.py anchor_capture.py runtime_fs.py prompts.py anchor_drift.py trigger_policy.py response_protocol.py session_registry.py trigger_state.py tools.py claude_md_snippet.md adapters/__init__.py adapters/claude_code.py hooks/claude_code_stop.sh hooks/claude_code_prompt.sh hooks/find_review.sh hooks/find_session.sh hooks/run_manual_review.sh; do
    if [ ! -f "$OVERWATCH_DIR/$f" ]; then
        echo -e "${RED}Error: Missing $f in $OVERWATCH_DIR${NC}"
        exit 1
    fi
done
echo -e "Overwatch files: ${GREEN}OK${NC}"

# Step 3: Finish every fallible preflight before changing user configuration.
mkdir -p "$OVERWATCH_DIR/state" "$OVERWATCH_DIR/reviews"
chmod 700 "$OVERWATCH_DIR/state" "$OVERWATCH_DIR/reviews"
echo -e "Runtime directories: ${GREEN}OK${NC}"
echo -e "Hook commands: ${GREEN}OK${NC}"

# Step 4: Build settings.json and CLAUDE.md together, then commit with rollback.
echo ""
echo "Configuring hooks and CLAUDE.md..."

python3 - "$SETTINGS_FILE" "$OVERWATCH_DIR" "$CC_DIR/CLAUDE.md" <<'PY'
import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

settings_file = Path(sys.argv[1])
overwatch_dir = Path(sys.argv[2])
claude_md = Path(sys.argv[3])

sys.path.insert(0, str(overwatch_dir))
from response_protocol import REVIEW_RESPONSE_PROTOCOL

snippet_path = overwatch_dir / "claude_md_snippet.md"
snippet = snippet_path.read_text(encoding="utf-8")
snippet = snippet.replace("{{OVERWATCH_DIR}}", str(overwatch_dir))
snippet = snippet.replace("{{REVIEW_RESPONSE_PROTOCOL}}", REVIEW_RESPONSE_PROTOCOL)
if "{{OVERWATCH_DIR}}" in snippet or "{{REVIEW_RESPONSE_PROTOCOL}}" in snippet:
    raise SystemExit("Refusing to install CLAUDE.md with unresolved placeholders")

settings_original = settings_file.read_bytes()
settings = json.loads(settings_original)

claude_existed = claude_md.is_file()
claude_original = claude_md.read_bytes() if claude_existed else b""
claude_text = claude_original.decode("utf-8") if claude_existed else ""
begin = "<!-- OVERWATCH:BEGIN -->"
end = "<!-- OVERWATCH:END -->"
if begin in claude_text or end in claude_text:
    if claude_text.count(begin) != 1 or claude_text.count(end) != 1 or claude_text.index(begin) > claude_text.index(end):
        raise SystemExit(
            "Refusing to modify CLAUDE.md: Overwatch ownership markers are incomplete or ambiguous"
        )
    start = claude_text.index(begin)
    finish = claude_text.index(end, start) + len(end)
    if finish < len(claude_text) and claude_text[finish] == "\n":
        finish += 1
    claude_text = claude_text[:start] + claude_text[finish:]

if claude_text and not claude_text.endswith("\n"):
    claude_text += "\n"
if claude_text:
    claude_text += "\n"
updated_claude = claude_text + snippet.rstrip() + "\n"

hooks = settings.setdefault('hooks', {})

stop_hook_cmd = 'bash ' + shlex.quote(str(overwatch_dir / 'hooks' / 'claude_code_stop.sh'))
prompt_hook_cmd = 'bash ' + shlex.quote(str(overwatch_dir / 'hooks' / 'claude_code_prompt.sh'))
managed_markers = ('hooks/claude_code_stop.sh', 'hooks/claude_code_prompt.sh')
existing_managed = []


def managed_hook_script(command):
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) != 2 or Path(tokens[0]).name != 'bash':
        return None
    script = Path(tokens[1]).expanduser().resolve()
    if script.name not in {'claude_code_stop.sh', 'claude_code_prompt.sh'} or script.parent.name != 'hooks':
        return None
    install_root = script.parent.parent
    if not script.is_file() or not (install_root / 'overwatch.py').is_file() or not (install_root / 'config.py').is_file():
        return None
    return script


for existing_event, matchers in hooks.items():
    for matcher in matchers:
        retained = []
        for hook in matcher.get('hooks', []):
            command = str(hook.get('command', ''))
            if managed_hook_script(command) is not None:
                existing_managed.append((existing_event, matcher.get('matcher', ''), hook.copy()))
            else:
                retained.append(hook)
        matcher['hooks'] = retained


def add_canonical_hook(event, command, timeout, marker):
    entry = {'type': 'command', 'command': command, 'timeout': timeout}
    previous = [item for item in existing_managed if marker in str(item[2].get('command', ''))]
    already = (
        len(previous) == 1
        and previous[0][0] == event
        and previous[0][1] == ''
        and previous[0][2] == entry
    )
    matchers = hooks.setdefault(event, [])
    target = next((matcher for matcher in matchers if matcher.get('matcher', '') == ''), None)
    if target is None:
        target = {'matcher': '', 'hooks': []}
        matchers.append(target)
    target.setdefault('hooks', []).append(entry)
    print(f"{event} hook: {'already registered' if already else 'updated'}")


messages = [
    add_canonical_hook('Stop', stop_hook_cmd, 5, 'hooks/claude_code_stop.sh'),
    add_canonical_hook('UserPromptSubmit', prompt_hook_cmd, 120, 'hooks/claude_code_prompt.sh'),
]
updated_settings = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def stage(path, text, mode):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        stream = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return Path(tmp)


settings_mode = settings_file.stat().st_mode & 0o777
claude_mode = (claude_md.stat().st_mode & 0o777) if claude_existed else 0o600
settings_tmp = stage(settings_file, updated_settings, settings_mode)
claude_tmp = stage(claude_md, updated_claude, claude_mode)
updated_settings_bytes = updated_settings.encode("utf-8")
updated_claude_bytes = updated_claude.encode("utf-8")


def matches_original(path, existed, original):
    if existed:
        return path.is_file() and path.read_bytes() == original
    return not path.exists()


settings_committed = False
claude_committed = False
try:
    if not matches_original(settings_file, True, settings_original):
        raise RuntimeError(f"Refusing to replace concurrently modified hook config: {settings_file}")
    if not matches_original(claude_md, claude_existed, claude_original):
        raise RuntimeError(f"Refusing to replace concurrently modified CLAUDE.md: {claude_md}")
    shutil.copy2(settings_file, str(settings_file) + ".backup")
    if claude_existed:
        shutil.copy2(claude_md, str(claude_md) + ".backup")
    if not matches_original(settings_file, True, settings_original):
        raise RuntimeError(f"Hook config changed before replace: {settings_file}")
    os.replace(settings_tmp, settings_file)
    settings_committed = True
    if not matches_original(claude_md, claude_existed, claude_original):
        raise RuntimeError(f"CLAUDE.md changed before replace: {claude_md}")
    os.replace(claude_tmp, claude_md)
    claude_committed = True
except BaseException:
    rollback_errors = []
    if settings_committed:
        if settings_file.read_bytes() == updated_settings_bytes:
            settings_file.write_bytes(settings_original)
            os.chmod(settings_file, settings_mode)
        else:
            rollback_errors.append(f"external edit preserved: {settings_file}")
    if claude_committed:
        if claude_md.read_bytes() != updated_claude_bytes:
            rollback_errors.append(f"external edit preserved: {claude_md}")
        elif claude_existed:
            claude_md.write_bytes(claude_original)
            os.chmod(claude_md, claude_mode)
        else:
            claude_md.unlink(missing_ok=True)
    if rollback_errors:
        raise RuntimeError("install rollback did not overwrite concurrent edits: " + "; ".join(rollback_errors))
    raise
finally:
    settings_tmp.unlink(missing_ok=True)
    claude_tmp.unlink(missing_ok=True)

for message in messages:
    print(message)
print(f"Backup saved to: {settings_file}.backup")
print("CLAUDE.md: Overwatch section injected")
PY

# Step 5: Check API access
echo ""
echo "Checking API access..."
API_CHECK=$(OVERWATCH_DIR="$OVERWATCH_DIR" python3 - <<'PY' 2>/dev/null || echo "WARNING: Could not read config"
import os
import sys
sys.path.insert(0, os.environ['OVERWATCH_DIR'])
from config import API_BASE_URL, API_AUTH_TOKEN, REVIEW_MODEL
if not API_AUTH_TOKEN:
    print('WARNING: No API key found. Set ANTHROPIC_API_KEY environment variable.')
else:
    print(f'API: {API_BASE_URL} | Model: {REVIEW_MODEL} | Key: ...{API_AUTH_TOKEN[-4:]}')
PY
)
echo -e "${YELLOW}$API_CHECK${NC}"

# Done
echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}  Overwatch installed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Usage:"
TURN_COUNT=$(OVERWATCH_DIR="$OVERWATCH_DIR" python3 - <<'PY' 2>/dev/null || echo 10
import os
import sys
sys.path.insert(0, os.environ['OVERWATCH_DIR'])
from config import TURN_THRESHOLD
print(TURN_THRESHOLD)
PY
)
echo "  Auto-review:  Work normally — Overwatch reviews every ${TURN_COUNT} turns"
echo "  Manual review: Type 'overwatch' or 'second opinion' in Claude Code"
echo "  CLI review:    python3 $OVERWATCH_DIR/overwatch.py --session-id <id> --transcript <path> --force"
echo ""
echo -e "${YELLOW}Note: Restart Claude Code for hooks to take effect.${NC}"
