#!/bin/bash
# Run the public release compatibility checks.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$RUNTIME_TMP"
}
trap cleanup EXIT
export OVERWATCH_STATE_DIR="$RUNTIME_TMP/state"
export OVERWATCH_REVIEWS_DIR="$RUNTIME_TMP/reviews"
export OVERWATCH_LOG_FILE="$RUNTIME_TMP/overwatch.log"
export PYTHONPYCACHEPREFIX="$RUNTIME_TMP/pycache"
mkdir -p "$OVERWATCH_STATE_DIR" "$OVERWATCH_REVIEWS_DIR"
ANCHOR_TEST_HELPER="${ANCHOR_TEST_HELPER:-${ANCHOR_HELPER:-${HOME}/.codex/skills/anchor/scripts/anchor.py}}"
[[ -f "$ANCHOR_TEST_HELPER" ]] || {
  echo "check_release: missing Anchor helper for integration test: $ANCHOR_TEST_HELPER" >&2
  exit 1
}

candidate_manifest() {
  python3 - "$ROOT" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])


def is_runtime_artifact(path: Path) -> bool:
    rel = path.relative_to(root)
    if any(
        part in {
            ".git",
            "state",
            "reviews",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".vscode",
            ".idea",
        }
        for part in rel.parts
    ):
        return True
    return (
        path.name in {".DS_Store", "Thumbs.db", "overwatch.log"}
        or path.suffix in {".pyc", ".pyo", ".swp", ".swo"}
        or path.name.endswith("~")
    )


managed = [path for path in root.rglob("*") if not is_runtime_artifact(path)]
special = [
    path.relative_to(root).as_posix()
    for path in managed
    if not (path.is_dir() or path.is_file() or path.is_symlink())
]
if special:
    raise SystemExit("release candidate contains special filesystem entries: " + ", ".join(special))
paths = sorted(path for path in managed if path.is_file() or path.is_symlink())
for path in paths:
    rel = path.relative_to(root).as_posix()
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        digest = "link:" + hashlib.sha256(os.readlink(path).encode()).hexdigest()
    else:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{mode:04o} {digest} {rel}")
PY
}

candidate_manifest >"$RUNTIME_TMP/candidate.before"

python3 "$ROOT/tests/test_public_release_clean.py"
python3 "$ROOT/tests/test_adapters.py"
python3 "$ROOT/tests/test_runtime_integrity.py"
python3 "$ROOT/tests/test_tools_security.py"
python3 "$ROOT/tests/test_pending_review.py"
python3 "$ROOT/tests/test_anchor_capture.py"
python3 "$ROOT/tests/test_anchor_drift_fixtures.py"
python3 "$ROOT/tests/test_trigger_policy.py"
python3 "$ROOT/tests/test_config_runtime_defaults.py"
python3 "$ROOT/tests/test_claude_hook_compat.py"
python3 "$ROOT/tests/test_claude_installer.py"
python3 "$ROOT/tests/test_codex_installer.py"
python3 "$ROOT/tests/test_codex_installer_runtime_smoke.py"
ANCHOR_TEST_HELPER="$ANCHOR_TEST_HELPER" python3 "$ROOT/tests/test_codex_hook_observability.py"
python3 "$ROOT/tests/test_codex_exec_client.py"
python3 "$ROOT/tests/test_review_response_protocol.py"
python3 "$ROOT/tests/test_user_context_runtime_scope.py"

bash -n \
  "$ROOT/install.sh" \
  "$ROOT/install_codex.sh" \
  "$ROOT/uninstall.sh" \
  "$ROOT/hooks/claude_code_stop.sh" \
  "$ROOT/hooks/claude_code_prompt.sh" \
  "$ROOT/hooks/codex_stop.sh" \
  "$ROOT/hooks/codex_prompt.sh" \
  "$ROOT/hooks/find_session.sh" \
  "$ROOT/hooks/find_review.sh" \
  "$ROOT/hooks/run_manual_review.sh"

python3 -m py_compile \
  "$ROOT/config.py" \
  "$ROOT/overwatch.py" \
  "$ROOT/context_manager.py" \
  "$ROOT/codex_exec_client.py" \
  "$ROOT/pending_review.py" \
  "$ROOT/anchor_capture.py" \
  "$ROOT/runtime_fs.py" \
  "$ROOT/session_registry.py" \
  "$ROOT/trigger_state.py" \
  "$ROOT/trigger_policy.py" \
  "$ROOT/tools.py" \
  "$ROOT/adapters/__init__.py" \
  "$ROOT/adapters/claude_code.py" \
  "$ROOT/adapters/codex.py" \
  "$ROOT/response_protocol.py" \
  "$ROOT/prompts.py"

git -C "$ROOT" diff --check
candidate_manifest >"$RUNTIME_TMP/candidate.after"
if ! cmp -s "$RUNTIME_TMP/candidate.before" "$RUNTIME_TMP/candidate.after"; then
  echo "check_release: release gate mutated the candidate tree" >&2
  diff -u "$RUNTIME_TMP/candidate.before" "$RUNTIME_TMP/candidate.after" >&2 || true
  exit 1
fi
