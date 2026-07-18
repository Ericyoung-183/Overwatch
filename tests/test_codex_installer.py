#!/usr/bin/env python3
"""Regression tests for the Codex installer."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_codex.sh"
UNINSTALLER = ROOT / "uninstall.sh"


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def run_installer(hooks_path: Path, *, relay_dir: str = "") -> str:
    env = os.environ.copy()
    env["CODEX_HOOKS_PATH"] = str(hooks_path)
    env["OVERWATCH_CODEX_COMMAND"] = sys.executable
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


def test_codex_installer_fails_before_mutation_when_codex_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        hooks_path = Path(tmp) / "hooks.json"
        hooks_path.write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")
        before = hooks_path.read_bytes()
        env = {
            **os.environ,
            "CODEX_HOOKS_PATH": str(hooks_path),
            "OVERWATCH_CODEX_COMMAND": str(Path(tmp) / "missing-codex"),
        }
        result = subprocess.run(
            ["bash", str(INSTALLER)],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        unchanged = hooks_path.read_bytes() == before

    test("missing Codex command fails installer", result.returncode != 0, result.stdout + result.stderr)
    test("missing Codex command leaves hooks unchanged", unchanged, result.stdout + result.stderr)


def test_codex_installer_preflights_every_runtime_module() -> None:
    required = [
        "overwatch.py",
        "config.py",
        "api_client.py",
        "codex_exec_client.py",
        "pending_review.py",
        "anchor_capture.py",
        "runtime_fs.py",
        "prompts.py",
        "anchor_drift.py",
        "trigger_policy.py",
        "response_protocol.py",
        "session_registry.py",
        "trigger_state.py",
        "adapters/__init__.py",
        "adapters/codex.py",
        "hooks/codex_stop.sh",
        "hooks/codex_prompt.sh",
        "hooks/find_review.sh",
        "hooks/find_session.sh",
        "hooks/run_manual_review.sh",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        install_root = Path(tmp) / "install"
        for relative in required:
            source = ROOT / relative
            target = install_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copy2(INSTALLER, install_root / "install_codex.sh")
        hooks_path = Path(tmp) / "hooks.json"
        hooks_path.write_text('{"hooks":{}}\n', encoding="utf-8")
        before = hooks_path.read_bytes()
        result = subprocess.run(
            ["bash", str(install_root / "install_codex.sh")],
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "CODEX_HOOKS_PATH": str(hooks_path),
                "OVERWATCH_CODEX_COMMAND": sys.executable,
            },
        )
        unchanged = hooks_path.read_bytes() == before

    test("missing runtime module fails preflight", result.returncode != 0 and "context_manager.py" in result.stdout, result.stdout + result.stderr)
    test("runtime preflight failure leaves hooks unchanged", unchanged, result.stdout + result.stderr)


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
                                    },
                                    {
                                        "type": "command",
                                        "command": "/bin/echo " + shlex.quote(str(ROOT / "hooks" / "codex_stop.sh")),
                                        "timeout": 5,
                                    },
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
    expected_stop = "bash " + shlex.quote(str(ROOT / "hooks" / "codex_stop.sh"))
    expected_prompt = "bash " + shlex.quote(str(ROOT / "hooks" / "codex_prompt.sh"))

    test("installer prints success", "Overwatch installed for Codex" in first, first)
    test("installer prints Codex reasoning effort", "Reasoning effort: xhigh" in first, first)
    test("installer is idempotent", "already registered" in second, second)
    test("preserves existing stop hook", "/bin/echo existing-stop" in stop_commands, str(stop_commands))
    test("installer preserves hook path used only as an argument", any(cmd.startswith("/bin/echo ") and "codex_stop.sh" in cmd for cmd in stop_commands), str(stop_commands))
    test("registers codex stop hook once", stop_commands.count(expected_stop) == 1, str(stop_commands))
    test("registers codex prompt hook once", prompt_commands.count(expected_prompt) == 1, str(prompt_commands))
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


def test_codex_installer_preserves_concurrent_hook_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "install"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(".git", "state", "reviews", "__pycache__", "*.pyc"),
        )
        installer = install_root / "install_codex.sh"
        text = installer.read_text(encoding="utf-8")
        needle = "    displaced = commit_staged(\n"
        injected = (
            "    hooks_file.write_text('{\"hooks\": {\"External\": []}}\\n', encoding='utf-8')\n"
            + needle
        )
        installer.write_text(text.replace(needle, injected, 1), encoding="utf-8")
        hooks_path = base / "hooks.json"
        hooks_path.write_text('{"hooks": {}}\n', encoding="utf-8")

        result = subprocess.run(
            ["bash", str(installer)],
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "CODEX_HOOKS_PATH": str(hooks_path),
                "OVERWATCH_CODEX_COMMAND": sys.executable,
            },
        )
        current = json.loads(hooks_path.read_text(encoding="utf-8"))

    test("Codex installer rejects concurrent hook edit", result.returncode != 0 and "concurrently modified" in result.stderr, result.stdout + result.stderr)
    test("Codex installer preserves concurrent hook edit", "External" in current.get("hooks", {}), str(current))


def test_codex_installer_rejects_symlink_hook_config() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "real-hooks.json"
        target.write_text('{"hooks": {}}\n', encoding="utf-8")
        before = target.read_bytes()
        linked = root / "hooks.json"
        linked.symlink_to(target)
        result = subprocess.run(
            ["bash", str(INSTALLER)],
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "CODEX_HOOKS_PATH": str(linked),
                "OVERWATCH_CODEX_COMMAND": sys.executable,
            },
        )
        unchanged = target.read_bytes() == before

    test("Codex installer rejects symbolic-link hook config", result.returncode != 0 and "symbolic-link" in result.stdout + result.stderr, result.stdout + result.stderr)
    test("Codex installer leaves symbolic-link target unchanged", unchanged)


def test_codex_installer_replaces_relocated_and_duplicate_managed_hooks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hooks_path = root / "hooks.json"
        old_one = root / "old one"
        old_two = root / "old-two"
        for install_root in (old_one, old_two):
            (install_root / "hooks").mkdir(parents=True)
            (install_root / "overwatch.py").write_text("# marker\n", encoding="utf-8")
            (install_root / "config.py").write_text("# marker\n", encoding="utf-8")
        (old_one / "hooks" / "codex_stop.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (old_two / "hooks" / "codex_stop.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (old_two / "hooks" / "codex_prompt.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {"type": "command", "command": "bash " + shlex.quote(str(old_one / "hooks" / "codex_stop.sh")), "timeout": 45},
                                    {"type": "command", "command": "bash " + shlex.quote(str(old_two / "hooks" / "codex_stop.sh")), "timeout": 45},
                                    {"type": "command", "command": "/bin/echo keep", "timeout": 5},
                                    {"type": "command", "command": "bash /old/foreign/hooks/codex_stop.sh", "timeout": 45},
                                ],
                            }
                        ],
                        "UserPromptSubmit": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    {"type": "command", "command": "bash " + shlex.quote(str(old_two / "hooks" / "codex_prompt.sh")), "timeout": 120}
                                ],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        run_installer(hooks_path)
        data = json.loads(hooks_path.read_text(encoding="utf-8"))

    stop_commands = hook_commands(data, "Stop")
    prompt_commands = hook_commands(data, "UserPromptSubmit")
    expected_stop = "bash " + shlex.quote(str(ROOT / "hooks" / "codex_stop.sh"))
    expected_prompt = "bash " + shlex.quote(str(ROOT / "hooks" / "codex_prompt.sh"))
    test("Codex relocation keeps one exact stop hook", stop_commands.count(expected_stop) == 1, str(stop_commands))
    test("Codex relocation keeps one exact prompt hook", prompt_commands.count(expected_prompt) == 1, str(prompt_commands))
    test("Codex relocation removes verified old installs", not any(str(old_one) in cmd or str(old_two) in cmd for cmd in stop_commands + prompt_commands), str(data))
    test("Codex relocation preserves unrelated hooks", "/bin/echo keep" in stop_commands, str(stop_commands))
    test("Codex installer preserves unverified similarly named hook", "bash /old/foreign/hooks/codex_stop.sh" in stop_commands, str(stop_commands))


def test_codex_uninstall_removes_current_hooks_and_preserves_foreign_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hooks_path = root / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "/bin/echo keep", "timeout": 5},
                                    {"type": "command", "command": "bash /old/hooks/codex_stop.sh", "timeout": 5},
                                ],
                            }
                        ],
                        "UserPromptSubmit": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "bash /old/hooks/codex_prompt.sh", "timeout": 120}
                                ],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        run_installer(hooks_path)
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        data["hooks"]["Stop"][0]["hooks"].append(
            {"type": "command", "command": "bash '/another install/hooks/codex_stop.sh'", "timeout": 5}
        )
        data["hooks"]["Stop"][0]["hooks"].append(
            {"type": "command", "command": "/bin/echo bash " + shlex.quote(str(ROOT / "hooks" / "codex_stop.sh")), "timeout": 5}
        )
        hooks_path.write_text(json.dumps(data), encoding="utf-8")
        env = {
            **os.environ,
            "HOME": str(root / "home"),
            "CODEX_HOOKS_PATH": str(hooks_path),
            "CC_SETTINGS_PATH": str(root / "missing-claude-settings.json"),
        }
        subprocess.run(
            ["bash", str(UNINSTALLER)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        result = json.loads(hooks_path.read_text(encoding="utf-8"))

    commands = hook_commands(result, "Stop") + hook_commands(result, "UserPromptSubmit")
    current_stop = "bash " + shlex.quote(str(ROOT / "hooks" / "codex_stop.sh"))
    current_prompt = "bash " + shlex.quote(str(ROOT / "hooks" / "codex_prompt.sh"))
    test("Codex uninstall removes current managed stop hook", current_stop not in commands, str(commands))
    test("Codex uninstall removes current managed prompt hook", current_prompt not in commands, str(commands))
    test("Codex uninstall preserves unrelated hooks", "/bin/echo keep" in commands, str(commands))
    test(
        "Codex uninstall preserves similarly named foreign hook",
        "bash '/another install/hooks/codex_stop.sh'" in commands,
        str(commands),
    )
    test("Codex uninstall preserves managed path used only as an argument", any(cmd.startswith("/bin/echo bash ") for cmd in commands), str(commands))


def test_release_check_runs_codex_installer_tests() -> None:
    text = (ROOT / "scripts" / "check_release.sh").read_text(encoding="utf-8")
    test("release check runs codex installer test", "test_codex_installer.py" in text)
    test("release check runs codex installer runtime smoke", "test_codex_installer_runtime_smoke.py" in text)
    test("release check syntax-checks codex installer", "install_codex.sh" in text)


if __name__ == "__main__":
    test_codex_installer_fails_before_mutation_when_codex_is_missing()
    test_codex_installer_preflights_every_runtime_module()
    test_codex_installer_registers_hooks_idempotently()
    test_codex_installer_can_configure_status_relay_dir()
    test_codex_installer_preserves_concurrent_hook_edit()
    test_codex_installer_rejects_symlink_hook_config()
    test_codex_installer_replaces_relocated_and_duplicate_managed_hooks()
    test_codex_uninstall_removes_current_hooks_and_preserves_foreign_names()
    test_release_check_runs_codex_installer_tests()
    print("codex installer tests passed")
