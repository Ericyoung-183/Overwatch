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

for f in overwatch.py config.py api_client.py codex_exec_client.py context_manager.py pending_review.py anchor_capture.py runtime_fs.py config_transaction.py prompts.py anchor_drift.py trigger_policy.py response_protocol.py session_registry.py trigger_state.py adapters/__init__.py adapters/codex.py hooks/codex_stop.sh hooks/codex_prompt.sh hooks/find_review.sh hooks/find_session.sh hooks/run_manual_review.sh; do
    if [ ! -f "$OVERWATCH_DIR/$f" ]; then
        echo -e "${RED}Error: Missing $f in $OVERWATCH_DIR${NC}"
        exit 1
    fi
done
echo -e "Overwatch files: ${GREEN}OK${NC}"

if ! CODEX_CHECK=$(OVERWATCH_DIR="$OVERWATCH_DIR" OVERWATCH_ADAPTER=codex OVERWATCH_BACKEND=codex_exec python3 - <<'PY'
import os
import shutil
import sys

sys.path.insert(0, os.environ["OVERWATCH_DIR"])
from config import CODEX_COMMAND, CODEX_REASONING_EFFORT, REVIEW_MODEL

command = CODEX_COMMAND
resolved = command if os.path.isfile(command) and os.access(command, os.X_OK) else shutil.which(command)
if not resolved:
    raise SystemExit(f"Codex command not found or not executable: {command}")
print(
    f"Codex command: {command} | Review model: {REVIEW_MODEL} | "
    f"Reasoning effort: {CODEX_REASONING_EFFORT} | OK"
)
PY
); then
    echo -e "${RED}Error: Codex runtime preflight failed; hooks were not changed.${NC}"
    exit 1
fi
echo -e "${GREEN}$CODEX_CHECK${NC}"

mkdir -p "$OVERWATCH_DIR/state" "$OVERWATCH_DIR/reviews"
chmod 700 "$OVERWATCH_DIR/state" "$OVERWATCH_DIR/reviews"
echo -e "Runtime directories: ${GREEN}OK${NC}"

mkdir -p "$(dirname "$HOOKS_FILE")"
if [ -L "$HOOKS_FILE" ]; then
    echo -e "${RED}Error: Refusing symbolic-link Codex hooks file: $HOOKS_FILE${NC}"
    exit 1
fi

echo -e "Hook commands: ${GREEN}OK${NC}"

python3 - "$HOOKS_FILE" "$OVERWATCH_DIR" <<'PY'
import json
import os
import shutil
import shlex
import sys
from pathlib import Path

hooks_file = Path(sys.argv[1])
overwatch_dir = Path(sys.argv[2])
sys.path.insert(0, str(overwatch_dir))
from config_transaction import commit_staged, reject_symlink, rollback_commit, stage_bytes

reject_symlink(hooks_file)
existed = hooks_file.is_file()
try:
    original = hooks_file.read_bytes() if existed else None
    settings = json.loads(original) if original is not None else {"hooks": {}}
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid JSON in {hooks_file}: {exc}")

backup = hooks_file.with_suffix(hooks_file.suffix + ".backup")
reject_symlink(backup)

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


managed_markers = ("hooks/codex_stop.sh", "hooks/codex_prompt.sh")
existing_managed: list[tuple[str, str, dict]] = []


def managed_hook_script(command: str):
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
        while tokens and "=" in tokens[0] and not tokens[0].startswith(("/", "./")):
            tokens = tokens[1:]
    if len(tokens) != 2 or Path(tokens[0]).name != "bash":
        return None
    script = Path(tokens[1]).expanduser().resolve()
    if script.name not in {"codex_stop.sh", "codex_prompt.sh"} or script.parent.name != "hooks":
        return None
    install_root = script.parent.parent
    if not script.is_file() or not (install_root / "overwatch.py").is_file() or not (install_root / "config.py").is_file():
        return None
    return script


for existing_event, matchers in hooks.items():
    for matcher in matchers:
        retained = []
        for hook in matcher.get("hooks", []):
            hook_command = str(hook.get("command", ""))
            if managed_hook_script(hook_command) is not None:
                existing_managed.append((existing_event, matcher.get("matcher", ""), hook.copy()))
            else:
                retained.append(hook)
        matcher["hooks"] = retained


def add_canonical_hook(event: str, command: str, timeout: int, marker: str) -> str:
    entry = {"type": "command", "command": command, "timeout": timeout}
    previous = [item for item in existing_managed if marker in str(item[2].get("command", ""))]
    already = (
        len(previous) == 1
        and previous[0][0] == event
        and previous[0][1] == ".*"
        and previous[0][2] == entry
    )
    matchers = hooks.setdefault(event, [])
    target = next((matcher for matcher in matchers if matcher.get("matcher", "") == ".*"), None)
    if target is None:
        target = {"matcher": ".*", "hooks": []}
        matchers.append(target)
    target.setdefault("hooks", []).append(entry)
    return f"{event} hook: {'already registered' if already else 'updated'}"


messages = [
    add_canonical_hook("Stop", stop_command, 45, "hooks/codex_stop.sh"),
    add_canonical_hook("UserPromptSubmit", prompt_command, 120, "hooks/codex_prompt.sh"),
]

updated = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
mode = (hooks_file.stat().st_mode & 0o777) if existed else 0o600
staged = stage_bytes(hooks_file, updated, mode)
displaced = None
committed = False
preserve_displaced = False
try:
    displaced = commit_staged(
        hooks_file,
        staged,
        expected_original=original,
        expected_mode=mode if existed else None,
    )
    committed = True
    if displaced is not None:
        shutil.copy2(displaced, backup)
    else:
        backup.write_bytes(b'{\n  "hooks": {}\n}\n')
        os.chmod(backup, mode)
except BaseException:
    if committed:
        try:
            rollback_commit(
                hooks_file,
                displaced,
                expected_current=updated,
                expected_current_mode=mode,
            )
            displaced = None
        except BaseException:
            preserve_displaced = True
            raise
    raise
finally:
    if displaced is not None and not preserve_displaced:
        displaced.unlink(missing_ok=True)
    staged.unlink(missing_ok=True)

for message in messages:
    print(message)
print(f"Backup saved to: {backup}")
print(f"Codex hooks file: {'updated' if existed else 'created'}")
PY

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
