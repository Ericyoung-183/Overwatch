#!/bin/bash
# Overwatch uninstaller for Claude Code
# Removes Overwatch hooks from settings.json. Does NOT delete Overwatch files.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Uninstalling Overwatch hooks..."

# Detect settings file
CC_DIR=""
EXTRA_CC_DIR="${OVERWATCH_CC_DIR:-}"
for candidate in "$HOME/.claude" ${EXTRA_CC_DIR:+"$EXTRA_CC_DIR"}; do
    if [ -d "$candidate" ] && [ -f "$candidate/settings.json" ]; then
        CC_DIR="$candidate"
        break
    fi
done

SETTINGS_FILE="${CC_SETTINGS_PATH:-${CC_DIR:+$CC_DIR/settings.json}}"
if [ -z "$SETTINGS_FILE" ] || [ ! -f "$SETTINGS_FILE" ]; then
    echo "Could not find settings.json. Nothing to uninstall."
    exit 0
fi

OW_SETTINGS="$SETTINGS_FILE" OW_DIR="$OVERWATCH_DIR" python3 -c "
import json, shutil, os

settings_file = os.environ['OW_SETTINGS']
overwatch_dir = os.environ['OW_DIR']

shutil.copy2(settings_file, settings_file + '.backup')

with open(settings_file) as f:
    settings = json.load(f)

hooks = settings.get('hooks', {})
removed = 0

for event_type in ['Stop', 'UserPromptSubmit']:
    if event_type not in hooks:
        continue
    for matcher in hooks[event_type]:
        original = len(matcher.get('hooks', []))
        matcher['hooks'] = [
            h for h in matcher.get('hooks', [])
            if not h.get('command', '').startswith(overwatch_dir)
        ]
        removed += original - len(matcher['hooks'])

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'Removed {removed} hook(s). Backup: {settings_file}.backup')
"

# Remove CLAUDE.md snippet
CLAUDE_MD="${CC_DIR:+$CC_DIR/CLAUDE.md}"
if [ -n "$CLAUDE_MD" ] && [ -f "$CLAUDE_MD" ] && grep -q "OVERWATCH:BEGIN" "$CLAUDE_MD" 2>/dev/null; then
    sed '/<!-- OVERWATCH:BEGIN -->/,/<!-- OVERWATCH:END -->/d' "$CLAUDE_MD" > "$CLAUDE_MD.tmp"
    mv "$CLAUDE_MD.tmp" "$CLAUDE_MD"
    echo "Removed Overwatch section from $CLAUDE_MD"
else
    echo "No Overwatch section found in CLAUDE.md (skipped)"
fi

echo "Done. Restart Claude Code for changes to take effect."
echo "Overwatch files are still in $OVERWATCH_DIR — delete manually if desired."
