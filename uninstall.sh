#!/bin/bash
# Overwatch uninstaller for Claude Code and Codex.
# Removes managed hooks from runtime configs. Does NOT delete Overwatch files.

set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Uninstalling Overwatch hooks..."

# Detect settings file
CC_DIR=""
EXTRA_CC_DIR="${OVERWATCH_CC_DIR:-}"
for candidate in "$HOME/.claude" ${EXTRA_CC_DIR:+"$EXTRA_CC_DIR"}; do
    if [ -d "$candidate" ]; then
        if [ -z "$CC_DIR" ]; then
            CC_DIR="$candidate"
        fi
        if [ -f "$candidate/settings.json" ]; then
            CC_DIR="$candidate"
            break
        fi
    fi
done

CC_SETTINGS_FILE="${CC_SETTINGS_PATH:-${CC_DIR:+$CC_DIR/settings.json}}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_HOOKS_FILE="${CODEX_HOOKS_PATH:-$CODEX_HOME/hooks.json}"

if [ -n "$CC_SETTINGS_FILE" ]; then
    CC_DIR="$(dirname "$CC_SETTINGS_FILE")"
fi
CLAUDE_MD="${CC_DIR:+$CC_DIR/CLAUDE.md}"

OW_DIR="$OVERWATCH_DIR" \
OW_CC_SETTINGS="$CC_SETTINGS_FILE" \
OW_CODEX_SETTINGS="$CODEX_HOOKS_FILE" \
OW_CLAUDE_MD="$CLAUDE_MD" \
python3 - <<'PY'
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path

overwatch_dir = os.path.realpath(os.environ["OW_DIR"])
settings_inputs = [
    ("Claude Code", os.environ.get("OW_CC_SETTINGS", "")),
    ("Codex", os.environ.get("OW_CODEX_SETTINGS", "")),
]
claude_md_value = os.environ.get("OW_CLAUDE_MD", "")
managed_scripts = {
    os.path.realpath(os.path.join(overwatch_dir, "hooks", name))
    for name in (
        "claude_code_stop.sh",
        "claude_code_prompt.sh",
        "codex_stop.sh",
        "codex_prompt.sh",
    )
}


def is_managed(command):
    try:
        tokens = shlex.split(str(command))
    except ValueError:
        return False
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
        while tokens and "=" in tokens[0] and not tokens[0].startswith(("/", "./")):
            tokens = tokens[1:]
    return (
        len(tokens) == 2
        and os.path.basename(tokens[0]) == "bash"
        and os.path.realpath(tokens[1]) in managed_scripts
    )


def sync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stage_bytes(path: Path, content: bytes, mode: int) -> str:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.fchmod(descriptor, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


updates = []
seen = set()
for runtime, value in settings_inputs:
    if not value:
        print(f"No {runtime} hook config found (skipped)")
        continue
    path = Path(value)
    key = os.path.realpath(path)
    if key in seen:
        continue
    seen.add(key)
    if not path.is_file():
        print(f"No {runtime} hook config found (skipped)")
        continue
    original = path.read_bytes()
    settings = json.loads(original)
    if not isinstance(settings, dict):
        raise SystemExit(f"Refusing to modify non-object hook config: {path}")
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"Refusing to modify invalid hooks object: {path}")
    removed = 0
    for event_type in ("Stop", "UserPromptSubmit"):
        matchers = hooks.get(event_type, [])
        if not isinstance(matchers, list):
            raise SystemExit(f"Refusing to modify invalid {event_type} hooks: {path}")
        for matcher in matchers:
            entries = matcher.get("hooks", [])
            retained = [entry for entry in entries if not is_managed(entry.get("command", ""))]
            removed += len(entries) - len(retained)
            matcher["hooks"] = retained
    updated = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode()
    updates.append((path, original, updated, path.stat().st_mode & 0o777, f"Removed {removed} {runtime} hook(s). Backup: {path}.backup"))

if claude_md_value:
    path = Path(claude_md_value)
    if path.is_file():
        original = path.read_bytes()
        text = original.decode("utf-8")
        begin = "<!-- OVERWATCH:BEGIN -->"
        end = "<!-- OVERWATCH:END -->"
        has_marker = begin in text or end in text
        if has_marker:
            if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) > text.index(end):
                raise SystemExit("Refusing to modify CLAUDE.md: Overwatch ownership markers are incomplete or ambiguous")
            start = text.index(begin)
            finish = text.index(end, start) + len(end)
            if finish < len(text) and text[finish] == "\n":
                finish += 1
            updated = (text[:start] + text[finish:]).encode("utf-8")
            updates.append((path, original, updated, path.stat().st_mode & 0o777, f"Removed Overwatch section from {path}"))
        else:
            print("No Overwatch section found in CLAUDE.md (skipped)")
    else:
        print("No Overwatch section found in CLAUDE.md (skipped)")
else:
    print("No Overwatch section found in CLAUDE.md (skipped)")

staged = {}
replaced = []
try:
    for path, original, updated, mode, _ in updates:
        staged[path] = stage_bytes(path, updated, mode)
    for path, original, _, _, _ in updates:
        if not path.is_file() or path.read_bytes() != original:
            raise RuntimeError(f"Refusing to replace concurrently modified config: {path}")
    for path, _, _, _, _ in updates:
        shutil.copy2(path, str(path) + ".backup")
    for path, original, updated, mode, _ in updates:
        if not path.is_file() or path.read_bytes() != original:
            raise RuntimeError(f"Config changed before replace: {path}")
        os.replace(staged.pop(path), path)
        sync_parent(path)
        replaced.append((path, original, updated, mode))
except Exception:
    rollback_errors = []
    for path, original, updated, mode in reversed(replaced):
        try:
            if not path.is_file() or path.read_bytes() != updated:
                rollback_errors.append(f"external edit preserved: {path}")
                continue
            rollback = stage_bytes(path, original, mode)
            os.replace(rollback, path)
            sync_parent(path)
        except Exception as exc:
            rollback_errors.append(f"{path}: {exc}")
    if rollback_errors:
        raise RuntimeError("uninstall failed and rollback was incomplete: " + "; ".join(rollback_errors))
    raise
finally:
    for temporary in staged.values():
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass

for _, _, _, _, message in updates:
    print(message)
PY

echo "Done. Restart Claude Code or Codex for changes to take effect."
echo "Overwatch files are still in $OVERWATCH_DIR; delete them manually if desired."
