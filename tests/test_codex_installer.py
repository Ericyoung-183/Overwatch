#!/usr/bin/env python3
"""Regression tests for the Codex installer."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_codex.sh"


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def run_installer(hooks_path: Path, *, relay_dir: str = "") -> str:
    env = os.environ.copy()
    env["CODEX_HOOKS_PATH"] = str(hooks_path)
    if relay_dir:
        env["OVERWATCH_CODEX_STATUS_RELAY_DIR"] = relay_dir
    else:
        env.pop("OVERWATCH_CODEX_STATUS_RELAY_DIR", None)
    proc = subprocess.run(
        ["bash", str(INSTALLER)],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    return proc.stdout


def hook_commands(data: dict, event: str) -> list[str]:
    commands: list[str] = []
    for matcher in data.get("hooks", {}).get(event, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if command:
                commands.append(command)
    return commands


def test_codex_installer_registers_hooks_idempotently() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        hooks_path = Path(tmp) / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/bin/echo existing-stop",
                                        "timeout": 5,
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        first = run_installer(hooks_path)
        second = run_installer(hooks_path)
        data = json.loads(hooks_path.read_text(encoding="utf-8"))

    stop_commands = hook_commands(data, "Stop")
    prompt_commands = hook_commands(data, "UserPromptSubmit")

    test("installer prints success", "Overwatch installed for Codex" in first, first)
    test("installer is idempotent", "already registered" in second, second)
    test("preserves existing stop hook", "/bin/echo existing-stop" in stop_commands, str(stop_commands))
    test("registers codex stop hook once", sum("hooks/codex_stop.sh" in cmd for cmd in stop_commands) == 1, str(stop_commands))
    test("registers codex prompt hook once", sum("hooks/codex_prompt.sh" in cmd for cmd in prompt_commands) == 1, str(prompt_commands))
    test("does not install Claude hooks into Codex", not any("claude_code_" in cmd for cmd in stop_commands + prompt_commands))


def test_codex_installer_can_configure_status_relay_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        hooks_path = Path(tmp) / "hooks.json"
        relay_dir = str(Path(tmp) / "relay")

        run_installer(hooks_path, relay_dir=relay_dir)
        data = json.loads(hooks_path.read_text(encoding="utf-8"))

    prompt_commands = hook_commands(data, "UserPromptSubmit")
    test("status relay env is included", any("OVERWATCH_CODEX_STATUS_RELAY_DIR=" in cmd for cmd in prompt_commands), str(prompt_commands))
    test("status relay path is included", any(relay_dir in cmd for cmd in prompt_commands), str(prompt_commands))


def test_release_check_runs_codex_installer_tests() -> None:
    text = (ROOT / "scripts" / "check_release.sh").read_text(encoding="utf-8")
    test("release check runs codex installer test", "test_codex_installer.py" in text)
    test("release check syntax-checks codex installer", "install_codex.sh" in text)


if __name__ == "__main__":
    test_codex_installer_registers_hooks_idempotently()
    test_codex_installer_can_configure_status_relay_dir()
    test_release_check_runs_codex_installer_tests()
    print("codex installer tests passed")
