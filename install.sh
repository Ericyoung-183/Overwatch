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

# Step 1: Detect Claude Code config directory
CC_DIR=""
EXTRA_CC_DIR="${OVERWATCH_CC_DIR:-}"
for candidate in "$HOME/.claude" ${EXTRA_CC_DIR:+"$EXTRA_CC_DIR"}; do
    if [ -d "$candidate" ] && [ -f "$candidate/settings.json" ]; then
        CC_DIR="$candidate"
        break
    fi
done

if [ -z "$CC_DIR" ]; then
    echo -e "${RED}Error: Could not find Claude Code settings directory.${NC}"
    echo "Checked: ~/.claude/settings.json"
    echo "Set OVERWATCH_CC_DIR to specify a custom Claude Code config directory."
    echo ""
    echo "If Claude Code is installed elsewhere, set CC_SETTINGS_PATH:"
    echo "  CC_SETTINGS_PATH=/path/to/settings.json ./install.sh"
    exit 1
fi

SETTINGS_FILE="${CC_SETTINGS_PATH:-$CC_DIR/settings.json}"
if [ ! -f "$SETTINGS_FILE" ]; then
    echo -e "${RED}Error: Settings file not found: $SETTINGS_FILE${NC}"
    exit 1
fi
echo -e "Found Claude Code config: ${GREEN}$SETTINGS_FILE${NC}"

# Step 2: Verify Overwatch files exist
for f in overwatch.py config.py api_client.py prompts.py context_manager.py; do
    if [ ! -f "$OVERWATCH_DIR/$f" ]; then
        echo -e "${RED}Error: Missing $f in $OVERWATCH_DIR${NC}"
        exit 1
    fi
done
echo -e "Overwatch files: ${GREEN}OK${NC}"

# Step 3: Make hooks executable
chmod +x "$OVERWATCH_DIR/hooks/"*.sh
echo -e "Hook permissions: ${GREEN}OK${NC}"

# Step 4: Add hooks to settings.json
echo ""
echo "Adding hooks to $SETTINGS_FILE..."

python3 -c "
import json, sys, os, shutil

settings_file = '$SETTINGS_FILE'
overwatch_dir = '$OVERWATCH_DIR'

# Backup
shutil.copy2(settings_file, settings_file + '.backup')

with open(settings_file) as f:
    settings = json.load(f)

hooks = settings.setdefault('hooks', {})

stop_hook_cmd = os.path.join(overwatch_dir, 'hooks', 'claude_code_stop.sh')
prompt_hook_cmd = os.path.join(overwatch_dir, 'hooks', 'claude_code_prompt.sh')

# Helper: check if Overwatch hook already registered
def has_overwatch_hook(hook_list, cmd):
    for matcher in hook_list:
        for h in matcher.get('hooks', []):
            if h.get('command', '') == cmd:
                return True
    return False

# Add Stop hook
stop_hooks = hooks.setdefault('Stop', [])
if has_overwatch_hook(stop_hooks, stop_hook_cmd):
    print('Stop hook: already registered')
else:
    # Find existing matcher with empty string, or create new
    added = False
    for matcher in stop_hooks:
        if matcher.get('matcher', '') == '':
            matcher['hooks'].append({
                'type': 'command',
                'command': stop_hook_cmd,
                'timeout': 5
            })
            added = True
            break
    if not added:
        stop_hooks.append({
            'matcher': '',
            'hooks': [{'type': 'command', 'command': stop_hook_cmd, 'timeout': 5}]
        })
    print('Stop hook: added')

# Add UserPromptSubmit hook
prompt_hooks = hooks.setdefault('UserPromptSubmit', [])
if has_overwatch_hook(prompt_hooks, prompt_hook_cmd):
    print('UserPromptSubmit hook: already registered')
else:
    added = False
    for matcher in prompt_hooks:
        if matcher.get('matcher', '') == '':
            matcher['hooks'].append({
                'type': 'command',
                'command': prompt_hook_cmd,
                'timeout': 120
            })
            added = True
            break
    if not added:
        prompt_hooks.append({
            'matcher': '',
            'hooks': [{'type': 'command', 'command': prompt_hook_cmd, 'timeout': 120}]
        })
    print('UserPromptSubmit hook: added')

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'Backup saved to: {settings_file}.backup')
"

# Step 5: Inject CLAUDE.md configuration
echo ""
echo "Configuring CLAUDE.md..."

SNIPPET_FILE="$OVERWATCH_DIR/claude_md_snippet.md"
CLAUDE_MD="$CC_DIR/CLAUDE.md"

if [ ! -f "$SNIPPET_FILE" ]; then
    echo -e "${YELLOW}Warning: claude_md_snippet.md not found, skipping CLAUDE.md setup.${NC}"
else
    # Replace {{OVERWATCH_DIR}} placeholder with actual path
    SNIPPET=$(sed "s|{{OVERWATCH_DIR}}|$OVERWATCH_DIR|g" "$SNIPPET_FILE")

    # Create CLAUDE.md if it doesn't exist
    if [ ! -f "$CLAUDE_MD" ]; then
        touch "$CLAUDE_MD"
        echo -e "Created: ${GREEN}$CLAUDE_MD${NC}"
    fi

    # Remove any existing Overwatch section (marked or hand-written)
    if grep -q "OVERWATCH:BEGIN" "$CLAUDE_MD" 2>/dev/null; then
        sed '/<!-- OVERWATCH:BEGIN -->/,/<!-- OVERWATCH:END -->/d' "$CLAUDE_MD" > "$CLAUDE_MD.tmp"
        mv "$CLAUDE_MD.tmp" "$CLAUDE_MD"
        echo -e "CLAUDE.md: removed old marked section"
    elif grep -q "## Overwatch System" "$CLAUDE_MD" 2>/dev/null; then
        # Remove hand-written section: from "## Overwatch System" to next "## " or EOF
        python3 -c "
import re, os
path = '$CLAUDE_MD'
with open(path) as f:
    text = f.read()
# Match from '## Overwatch System' to next '## ' heading or EOF
text = re.sub(r'\n## Overwatch System[^\n]*\n.*?(?=\n## |\Z)', '', text, flags=re.DOTALL)
with open(path, 'w') as f:
    f.write(text)
"
        echo -e "CLAUDE.md: removed old hand-written section"
    fi

    # Append new template
    printf '\n%s\n' "$SNIPPET" >> "$CLAUDE_MD"
    echo -e "CLAUDE.md: ${GREEN}Overwatch section injected${NC}"
fi

# Step 6: Check API access
echo ""
echo "Checking API access..."
API_CHECK=$(python3 -c "
import sys; sys.path.insert(0, '$OVERWATCH_DIR')
from config import API_BASE_URL, API_AUTH_TOKEN, REVIEW_MODEL
if not API_AUTH_TOKEN:
    print('WARNING: No API key found. Set ANTHROPIC_API_KEY environment variable.')
else:
    print(f'API: {API_BASE_URL} | Model: {REVIEW_MODEL} | Key: ...{API_AUTH_TOKEN[-4:]}')
" 2>/dev/null || echo "WARNING: Could not read config")
echo -e "${YELLOW}$API_CHECK${NC}"

# Step 7: Create runtime directories
mkdir -p "$OVERWATCH_DIR/state" "$OVERWATCH_DIR/reviews"
echo -e "Runtime directories: ${GREEN}OK${NC}"

# Done
echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}  Overwatch installed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Usage:"
echo "  Auto-review:  Work normally — Overwatch reviews every $( python3 -c "import sys; sys.path.insert(0,'$OVERWATCH_DIR'); from config import TURN_THRESHOLD; print(TURN_THRESHOLD)" 2>/dev/null || echo 10 ) turns"
echo "  Manual review: Type 'overwatch' or 'second opinion' in Claude Code"
echo "  CLI review:    python3 $OVERWATCH_DIR/overwatch.py --session-id <id> --transcript <path> --force"
echo ""
echo -e "${YELLOW}Note: Restart Claude Code for hooks to take effect.${NC}"
