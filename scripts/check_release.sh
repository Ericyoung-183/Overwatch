#!/bin/bash
# Run the public release compatibility checks.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 "$ROOT/tests/test_public_release_clean.py"
python3 "$ROOT/tests/test_config_runtime_defaults.py"
python3 "$ROOT/tests/test_claude_hook_compat.py"
python3 "$ROOT/tests/test_codex_installer.py"
python3 "$ROOT/tests/test_codex_installer_runtime_smoke.py"
python3 "$ROOT/tests/test_codex_hook_observability.py"
python3 "$ROOT/tests/test_codex_exec_client.py"
python3 "$ROOT/tests/test_review_response_protocol.py"

bash -n \
  "$ROOT/install.sh" \
  "$ROOT/install_codex.sh" \
  "$ROOT/hooks/claude_code_stop.sh" \
  "$ROOT/hooks/claude_code_prompt.sh" \
  "$ROOT/hooks/codex_stop.sh" \
  "$ROOT/hooks/codex_prompt.sh" \
  "$ROOT/hooks/find_session.sh" \
  "$ROOT/hooks/find_review.sh"

python3 -m py_compile \
  "$ROOT/config.py" \
  "$ROOT/overwatch.py" \
  "$ROOT/context_manager.py" \
  "$ROOT/codex_exec_client.py" \
  "$ROOT/adapters/__init__.py" \
  "$ROOT/adapters/claude_code.py" \
  "$ROOT/adapters/codex.py" \
  "$ROOT/response_protocol.py" \
  "$ROOT/prompts.py"

git -C "$ROOT" diff --check
