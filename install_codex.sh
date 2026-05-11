#!/bin/bash
# Overwatch installer for Codex Desktop / Codex CLI.
# Adds Stop + UserPromptSubmit hooks to Codex hooks.json.
# Usage: ./install_codex.sh

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HOOKS_FILE="${CODEX_HOOKS_PATH:-$CODEX_HOME/hooks.json}"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "========================================="
echo "  Overwatch Installer for Codex"
echo "========================================="
echo ""

for f in overwatch.py config.py codex_exec_client.py response_protocol.py adapters/codex.py hooks/codex_stop.sh hooks/codex_prompt.sh; do
    if [ ! -f "$OVERWATCH_DIR/$f" ]; then
        echo -e "${RED}Error: Missing $f in $OVERWATCH_DIR${NC}"
        exit 1
    fi
done
echo -e "Overwatch files: ${GREEN}OK${NC}"

mkdir -p "$(dirname "$HOOKS_FILE")"
if [ ! -f "$HOOKS_FILE" ]; then
    printf '{\n  "hooks": {}\n}\n' >"$HOOKS_FILE"
    echo -e "Created Codex hooks file: ${GREEN}$HOOKS_FILE${NC}"
else
    echo -e "Found Codex hooks file: ${GREEN}$HOOKS_FILE${NC}"
fi

chmod +x "$OVERWATCH_DIR/hooks/"*.sh
echo -e "Hook permissions: ${GREEN}OK${NC}"

python3 - "$HOOKS_FILE" "$OVERWATCH_DIR" <<'PY'
import json
import os
import shutil
import shlex
import sys
import tempfile
from pathlib import Path

hooks_file = Path(sys.argv[1])
overwatch_dir = Path(sys.argv[2])

try:
    with hooks_file.open(encoding="utf-8") as f:
        settings = json.load(f)
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid JSON in {hooks_file}: {exc}")

backup = hooks_file.with_suffix(hooks_file.suffix + ".backup")
shutil.copy2(hooks_file, backup)

hooks = settings.setdefault("hooks", {})

stop_hook = overwatch_dir / "hooks" / "codex_stop.sh"
prompt_hook = overwatch_dir / "hooks" / "codex_prompt.sh"

stop_command = "bash " + shlex.quote(str(stop_hook))
prompt_command = "bash " + shlex.quote(str(prompt_hook))

relay_file = os.environ.get("OVERWATCH_CODEX_STATUS_RELAY_FILE", "").strip()
relay_dir = os.environ.get("OVERWATCH_CODEX_STATUS_RELAY_DIR", "").strip()
env_parts = []
if relay_dir:
    env_parts.append("OVERWATCH_CODEX_STATUS_RELAY_DIR=" + shlex.quote(relay_dir))
if relay_file:
    env_parts.append("OVERWATCH_CODEX_STATUS_RELAY_FILE=" + shlex.quote(relay_file))
if env_parts:
    prompt_command = "env " + " ".join(env_parts) + " " + prompt_command


def has_hook(matchers: list[dict], needle: str) -> bool:
    for matcher in matchers:
        for hook in matcher.get("hooks", []):
            if needle in str(hook.get("command", "")):
                return True
    return False


def add_hook(event: str, command: str, timeout: int, marker: str) -> str:
    matchers = hooks.setdefault(event, [])
    if has_hook(matchers, marker):
        return f"{event} hook: already registered"

    entry = {"type": "command", "command": command, "timeout": timeout}
    for matcher in matchers:
        if matcher.get("matcher", "") == ".*":
            matcher.setdefault("hooks", []).append(entry)
            return f"{event} hook: added"

    matchers.append({"matcher": ".*", "hooks": [entry]})
    return f"{event} hook: added"


messages = [
    add_hook("Stop", stop_command, 45, "hooks/codex_stop.sh"),
    add_hook("UserPromptSubmit", prompt_command, 120, "hooks/codex_prompt.sh"),
]

fd, tmp = tempfile.mkstemp(dir=str(hooks_file.parent), suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, hooks_file)

for message in messages:
    print(message)
print(f"Backup saved to: {backup}")
PY

mkdir -p "$OVERWATCH_DIR/state" "$OVERWATCH_DIR/reviews"
echo -e "Runtime directories: ${GREEN}OK${NC}"

CODEX_CHECK=$(OVERWATCH_DIR="$OVERWATCH_DIR" OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec python3 - <<'PY' 2>/dev/null || true
import os
import shutil
import sys
sys.path.insert(0, os.environ["OVERWATCH_DIR"])
from config import CODEX_COMMAND, REVIEW_MODEL
cmd = CODEX_COMMAND
ok = os.path.exists(cmd) or shutil.which(cmd)
print(f"Codex command: {cmd} | Review model: {REVIEW_MODEL} | {'OK' if ok else 'not found'}")
PY
)
if [ -n "$CODEX_CHECK" ]; then
    echo -e "${YELLOW}$CODEX_CHECK${NC}"
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}  Overwatch installed for Codex!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Usage:"
echo "  Auto-review:  Work normally in Codex — Overwatch reviews every configured interval"
echo "  Manual review: Type 'overwatch' or 'second opinion' in Codex"
echo "  CLI review:    OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec python3 $OVERWATCH_DIR/overwatch.py --session-id <id> --transcript <path> --force"
echo ""
echo -e "${YELLOW}Note: Restart Codex for hooks to take effect.${NC}"
