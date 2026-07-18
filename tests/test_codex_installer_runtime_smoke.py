#!/usr/bin/env python3
"""Runtime smoke tests for the Codex installer output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_codex.sh"
RUNTIME_TMP = tempfile.TemporaryDirectory(prefix="overwatch-codex-installer-smoke-")
STATE_DIR = Path(RUNTIME_TMP.name) / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def run_installer(hooks_path: Path, relay_dir: Path) -> None:
    env = os.environ.copy()
    env["CODEX_HOOKS_PATH"] = str(hooks_path)
    env["OVERWATCH_CODEX_STATUS_RELAY_DIR"] = str(relay_dir)
    env["OVERWATCH_CODEX_COMMAND"] = sys.executable
    subprocess.run(
        ["bash", str(INSTALLER)],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


def hook_commands(data: dict, event: str) -> list[str]:
    commands: list[str] = []
    for matcher in data.get("hooks", {}).get(event, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if command:
                commands.append(command)
    return commands


def run_installed_command(command: str, payload: dict[str, str]) -> dict[str, object]:
    env = os.environ.copy()
    env["OVERWATCH_STATE_DIR"] = str(STATE_DIR)
    env["OVERWATCH_REVIEWS_DIR"] = str(Path(RUNTIME_TMP.name) / "reviews")
    env["OVERWATCH_LOG_FILE"] = str(Path(RUNTIME_TMP.name) / "overwatch.log")
    proc = subprocess.run(
        command,
        input=json.dumps(payload),
        text=True,
        shell=True,
        capture_output=True,
        check=True,
        env=env,
    )
    return json.loads(proc.stdout)


def preserve_file(path: Path):
    original = path.read_text(encoding="utf-8") if path.exists() else None

    def restore() -> None:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original, encoding="utf-8")

    return restore


def test_installed_codex_commands_execute_hook_contracts() -> None:
    sid = "codex-installer-runtime-smoke"
    status_file = STATE_DIR / f"stop_status_{sid}.json"
    map_file = STATE_DIR / "session_map.json"
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    restore_map = preserve_file(map_file)
    restore_trigger = preserve_file(trigger_file)
    status_file.unlink(missing_ok=True)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            hooks_path = tmp / "hooks.json"
            relay_dir = tmp / "relay"
            project = tmp / "project"
            transcript = tmp / "codex-session.jsonl"
            project.mkdir()
            relay_dir.mkdir()
            transcript.write_text("{}\n", encoding="utf-8")

            run_installer(hooks_path, relay_dir)
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            stop_command = next(cmd for cmd in hook_commands(hooks, "Stop") if "hooks/codex_stop.sh" in cmd)
            prompt_command = next(
                cmd for cmd in hook_commands(hooks, "UserPromptSubmit") if "hooks/codex_prompt.sh" in cmd
            )

            stop_payload = {
                "session_id": sid,
                "transcript_path": "",
                "cwd": str(project),
            }
            stop_response = run_installed_command(stop_command, stop_payload)
            status = json.loads(status_file.read_text(encoding="utf-8"))
            test("installed Stop command returns continue", stop_response.get("continue") is True, str(stop_response))
            test("installed Stop command records missing transcript", status.get("reason") == "missing_transcript", str(status))

            relay_file = relay_dir / f"last_stop_says_{sid}.json"
            relay_file.write_text(
                json.dumps(
                    {
                        "continue": True,
                        "session_id": sid,
                        "systemMessage": "Stop Says INSTALL SMOKE | Overwatch: active/global",
                    }
                ),
                encoding="utf-8",
            )
            prompt_payload = {
                "session_id": sid,
                "transcript_path": str(transcript),
                "cwd": str(project),
                "user_prompt": "continue",
            }
            prompt_response = run_installed_command(prompt_command, prompt_payload)
            prompt_context = str(prompt_response.get("hookSpecificOutput", {}).get("additionalContext", ""))
            test("installed Prompt command surfaces status relay", "[Stop Says Previous Turn]" in prompt_context, prompt_context)
            test("installed Prompt command includes relayed status", "Stop Says INSTALL SMOKE" in prompt_context, prompt_context)
            test("installed Prompt command consumes status relay", not relay_file.exists(), str(relay_file))

            manual_payload = dict(prompt_payload)
            manual_payload["user_prompt"] = "overwatch"
            manual_response = run_installed_command(prompt_command, manual_payload)
            manual_context = str(manual_response.get("hookSpecificOutput", {}).get("additionalContext", ""))
            test("installed Prompt command emits manual trigger context", "[Overwatch Manual Trigger]" in manual_context, manual_context)
            test("manual trigger uses Codex adapter", "OVERWATCH_ADAPTER=codex" in manual_context, manual_context)
            test("manual trigger uses codex_exec backend", "OVERWATCH_BACKEND=codex_exec" in manual_context, manual_context)
    finally:
        status_file.unlink(missing_ok=True)
        restore_map()
        restore_trigger()


def test_release_check_runs_codex_installer_runtime_smoke() -> None:
    text = (ROOT / "scripts" / "check_release.sh").read_text(encoding="utf-8")
    test("release check runs Codex installer runtime smoke", "test_codex_installer_runtime_smoke.py" in text)


def test_readme_documents_codex_installer_verification_scope() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    test("README documents temp Codex hook install smoke", "temporary Codex hooks file" in text)
    test("README documents installed hook command execution", "installed Stop and UserPromptSubmit commands" in text)
    test("README separates live Codex UI trigger", "manual release check" in text and "live Codex" in text)


if __name__ == "__main__":
    test_installed_codex_commands_execute_hook_contracts()
    test_release_check_runs_codex_installer_runtime_smoke()
    test_readme_documents_codex_installer_verification_scope()
    print("codex installer runtime smoke tests passed")
