#!/usr/bin/env python3
"""Relocation and quoting tests for the Claude Code installer."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"  PASS {name}")


def hook_commands(data: dict, event: str) -> list[str]:
    return [
        hook.get("command", "")
        for matcher in data.get("hooks", {}).get(event, [])
        for hook in matcher.get("hooks", [])
        if hook.get("command")
    ]


def run_installer(install_root: Path, home: Path, settings: Path) -> str:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CC_SETTINGS_PATH"] = str(settings)
    result = subprocess.run(
        ["bash", str(install_root / "install.sh")],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout


def test_claude_installer_preflights_every_runtime_module() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "install"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(
                ".git", "state", "reviews", "__pycache__", "*.pyc"
            ),
        )
        (install_root / "pending_review.py").unlink()
        home = base / "home"
        settings_dir = base / "claude"
        settings_dir.mkdir()
        settings = settings_dir / "settings.json"
        settings.write_text('{"hooks":{}}\n', encoding="utf-8")
        before = settings.read_bytes()
        result = subprocess.run(
            ["bash", str(install_root / "install.sh")],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(home),
                "CC_SETTINGS_PATH": str(settings),
            },
        )
        unchanged = settings.read_bytes() == before
        no_snippet = not (settings_dir / "CLAUDE.md").exists()

    test(
        "Claude missing runtime module fails preflight",
        result.returncode != 0 and "pending_review.py" in result.stdout,
        result.stdout + result.stderr,
    )
    test("Claude runtime preflight leaves settings unchanged", unchanged, result.stdout + result.stderr)
    test("Claude runtime preflight creates no snippet", no_snippet, result.stdout + result.stderr)


def test_claude_installer_requires_snippet_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "install"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(
                ".git", "state", "reviews", "__pycache__", "*.pyc"
            ),
        )
        (install_root / "claude_md_snippet.md").unlink()
        settings_dir = base / "claude"
        settings_dir.mkdir()
        settings = settings_dir / "settings.json"
        settings.write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")
        before = settings.read_bytes()
        result = subprocess.run(
            ["bash", str(install_root / "install.sh")],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(base / "home"),
                "CC_SETTINGS_PATH": str(settings),
            },
        )

        unchanged = settings.read_bytes() == before
        no_claude_md = not (settings_dir / "CLAUDE.md").exists()

    test("Claude missing snippet fails preflight", result.returncode != 0 and "claude_md_snippet.md" in result.stdout, result.stdout + result.stderr)
    test("Claude missing snippet leaves settings unchanged", unchanged, result.stdout + result.stderr)
    test("Claude missing snippet creates no CLAUDE.md", no_claude_md, result.stdout + result.stderr)


def test_claude_installer_relocates_managed_hooks_with_quoted_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_a = base / "Overwatch Install A"
        install_b = base / "Overwatch Install B"
        ignore = shutil.ignore_patterns(".git", "state", "reviews", "__pycache__", "*.pyc")
        shutil.copytree(ROOT, install_a, ignore=ignore)
        shutil.copytree(ROOT, install_b, ignore=ignore)
        home = base / "Home With Space"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "bash /old/hooks/claude_code_stop.sh", "timeout": 5},
                                    {"type": "command", "command": "/bin/echo keep", "timeout": 5},
                                ],
                            }
                        ],
                        "UserPromptSubmit": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "bash /old/hooks/claude_code_prompt.sh", "timeout": 120}
                                ],
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        run_installer(install_a, home, settings)
        second = run_installer(install_b, home, settings)
        data = json.loads(settings.read_text(encoding="utf-8"))
        claude_md = (claude_dir / "CLAUDE.md").read_text(encoding="utf-8")

    stop_commands = hook_commands(data, "Stop")
    prompt_commands = hook_commands(data, "UserPromptSubmit")
    expected_stop = "bash " + shlex.quote(str(install_b / "hooks" / "claude_code_stop.sh"))
    expected_prompt = "bash " + shlex.quote(str(install_b / "hooks" / "claude_code_prompt.sh"))
    test("Claude relocation keeps one exact stop hook", stop_commands.count(expected_stop) == 1, str(stop_commands))
    test("Claude relocation keeps one exact prompt hook", prompt_commands.count(expected_prompt) == 1, str(prompt_commands))
    test("Claude relocation removes prior install path", str(install_a) not in "\n".join(stop_commands + prompt_commands), str(data))
    test("Claude relocation preserves unrelated hooks", "/bin/echo keep" in stop_commands, str(stop_commands))
    test("Claude installer preserves unverified similarly named hook", "bash /old/hooks/claude_code_stop.sh" in stop_commands, str(stop_commands))
    test("Claude command quotes path with spaces", "'" in expected_stop and "'" in expected_prompt, expected_stop)
    test("Claude snippet points at relocated install", str(install_b) in claude_md and str(install_a) not in claude_md, claude_md)
    test("Claude second install reports updates", "hook: updated" in second, second)


def test_claude_installer_uses_custom_settings_as_the_config_authority() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home-without-default-claude"
        home.mkdir()
        custom_dir = base / "custom-claude-config"
        custom_dir.mkdir()
        settings = custom_dir / "settings.json"
        settings.write_text('{"hooks": {}}\n', encoding="utf-8")

        run_installer(ROOT, home, settings)

        data = json.loads(settings.read_text(encoding="utf-8"))
        snippet = custom_dir / "CLAUDE.md"
        snippet_exists = snippet.is_file()
        default_snippet_exists = (home / ".claude" / "CLAUDE.md").exists()

    test("custom-only Claude settings installs hooks", bool(hook_commands(data, "Stop")), str(data))
    test("custom settings keeps CLAUDE.md beside settings", snippet_exists, str(snippet))
    test(
        "custom settings does not write default CLAUDE.md",
        not default_snippet_exists,
        str(home),
    )


def test_claude_installer_preserves_concurrent_settings_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "install"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(".git", "state", "reviews", "__pycache__", "*.pyc"),
        )
        installer = install_root / "install.sh"
        text = installer.read_text(encoding="utf-8")
        needle = "    settings_displaced = commit_staged(\n"
        injected = (
            "    settings_file.write_text('{\"hooks\": {\"External\": []}}\\n', encoding='utf-8')\n"
            + needle
        )
        installer.write_text(text.replace(needle, injected, 1), encoding="utf-8")
        config_dir = base / "claude"
        config_dir.mkdir()
        settings = config_dir / "settings.json"
        settings.write_text('{"hooks": {}}\n', encoding="utf-8")
        claude_md = config_dir / "CLAUDE.md"
        claude_md.write_text("user content\n", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(installer)],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(base / "home"),
                "CC_SETTINGS_PATH": str(settings),
            },
        )
        current = json.loads(settings.read_text(encoding="utf-8"))
        claude_current = claude_md.read_text(encoding="utf-8")

    test("Claude installer rejects concurrent settings edit", result.returncode != 0 and "concurrently modified" in result.stderr, result.stdout + result.stderr)
    test("Claude installer preserves concurrent settings edit", "External" in current.get("hooks", {}), str(current))
    test("Claude installer leaves CLAUDE.md untouched on CAS failure", claude_current == "user content\n", claude_current)


def test_claude_installer_rejects_symlink_settings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_dir = root / "claude"
        config_dir.mkdir()
        target = config_dir / "real-settings.json"
        target.write_text('{"hooks": {}}\n', encoding="utf-8")
        before = target.read_bytes()
        linked = config_dir / "settings.json"
        linked.symlink_to(target)
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh")],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(root / "home"),
                "CC_SETTINGS_PATH": str(linked),
            },
        )
        unchanged = target.read_bytes() == before
        claude_absent = not (config_dir / "CLAUDE.md").exists()

    test("Claude installer rejects symbolic-link settings", result.returncode != 0 and "symbolic-link" in result.stderr, result.stdout + result.stderr)
    test("Claude installer leaves symbolic-link target unchanged", unchanged)
    test("Claude installer creates no CLAUDE.md after symlink rejection", claude_absent)


def test_installed_hook_runs_when_overwatch_path_contains_single_quote() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "Eric's Overwatch"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(".git", "state", "reviews", "__pycache__", "*.pyc"),
        )
        home = base / "home"
        config_dir = base / "claude-config"
        config_dir.mkdir()
        settings = config_dir / "settings.json"
        settings.write_text('{"hooks": {}}\n', encoding="utf-8")
        run_installer(install_root, home, settings)
        data = json.loads(settings.read_text(encoding="utf-8"))
        prompt_command = hook_commands(data, "UserPromptSubmit")[0]
        result = subprocess.run(
            prompt_command,
            shell=True,
            executable="/bin/bash",
            input=json.dumps(
                {
                    "session_id": "single-quote-install",
                    "cwd": str(base),
                    "user_prompt": "normal message",
                }
            ),
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "HOME": str(home)},
        )
        response = json.loads(result.stdout)

    test("single-quote install hook remains executable", response.get("continue") is True, result.stdout)


def test_claude_uninstall_removes_bash_prefixed_managed_hooks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "Overwatch Install"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(".git", "state", "reviews", "__pycache__", "*.pyc"),
        )
        home = base / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "/bin/echo keep", "timeout": 5}]}],
                        "UserPromptSubmit": [],
                    }
                }
            ),
            encoding="utf-8",
        )
        run_installer(install_root, home, settings)
        installed = json.loads(settings.read_text(encoding="utf-8"))
        installed["hooks"]["Stop"][0]["hooks"].append(
            {
                "type": "command",
                "command": "bash /tmp/other-product/hooks/claude_code_stop.sh",
                "timeout": 5,
            }
        )
        installed["hooks"]["Stop"][0]["hooks"].append(
            {
                "type": "command",
                "command": "/bin/echo bash " + shlex.quote(str(install_root / "hooks" / "claude_code_stop.sh")),
                "timeout": 5,
            }
        )
        settings.write_text(json.dumps(installed), encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["CC_SETTINGS_PATH"] = str(settings)
        subprocess.run(
            ["bash", str(install_root / "uninstall.sh")],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        data = json.loads(settings.read_text(encoding="utf-8"))

    commands = hook_commands(data, "Stop") + hook_commands(data, "UserPromptSubmit")
    current_stop = "bash " + shlex.quote(str(install_root / "hooks" / "claude_code_stop.sh"))
    current_prompt = "bash " + shlex.quote(str(install_root / "hooks" / "claude_code_prompt.sh"))
    test("Claude uninstall removes managed stop hook", current_stop not in commands, str(commands))
    test("Claude uninstall removes managed prompt hook", current_prompt not in commands, str(commands))
    test("Claude uninstall preserves unrelated hooks", "/bin/echo keep" in commands, str(commands))
    test(
        "Claude uninstall preserves similarly named foreign hook",
        "bash /tmp/other-product/hooks/claude_code_stop.sh" in commands,
        str(commands),
    )
    test("Claude uninstall preserves managed path used only as an argument", any(cmd.startswith("/bin/echo bash ") for cmd in commands), str(commands))


def test_claude_uninstall_removes_snippet_without_settings_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text(
            "before\n<!-- OVERWATCH:BEGIN -->\nmanaged\n<!-- OVERWATCH:END -->\nafter\n",
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "HOME": str(home),
            "CODEX_HOOKS_PATH": str(Path(tmp) / "missing-hooks.json"),
        }
        subprocess.run(
            ["bash", str(ROOT / "uninstall.sh")],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        result = claude_md.read_text(encoding="utf-8")

    test("Claude uninstall removes snippet without settings", "OVERWATCH:BEGIN" not in result, result)
    test("Claude uninstall preserves surrounding content", "before" in result and "after" in result, result)


def test_installer_refuses_incomplete_markers_without_changing_claude_md() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        managed_command = "bash " + shlex.quote(str(ROOT / "hooks" / "claude_code_stop.sh"))
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [{"type": "command", "command": managed_command}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        settings_original = settings.read_bytes()
        claude_md = claude_dir / "CLAUDE.md"
        original = "user content\n<!-- OVERWATCH:BEGIN -->\nunowned tail\n"
        claude_md.write_text(original, encoding="utf-8")
        env = {**os.environ, "HOME": str(home), "CC_SETTINGS_PATH": str(settings)}

        result = subprocess.run(
            ["bash", str(ROOT / "install.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        unchanged = claude_md.read_text(encoding="utf-8")
        settings_unchanged = settings.read_bytes() == settings_original

    test("Claude install rejects incomplete ownership markers", result.returncode != 0, result.stdout + result.stderr)
    test("Claude install preserves incomplete-marker content byte for byte", unchanged == original, unchanged)
    test("Claude install rejects markers before changing settings", settings_unchanged, result.stdout + result.stderr)


def test_uninstaller_refuses_incomplete_markers_without_changing_claude_md() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        managed_command = "bash " + shlex.quote(str(ROOT / "hooks" / "claude_code_stop.sh"))
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [{"type": "command", "command": managed_command}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        settings_original = settings.read_bytes()
        claude_md = claude_dir / "CLAUDE.md"
        original = "user content\n<!-- OVERWATCH:END -->\nunowned tail\n"
        claude_md.write_text(original, encoding="utf-8")
        env = {**os.environ, "HOME": str(home), "CC_SETTINGS_PATH": str(settings)}

        result = subprocess.run(
            ["bash", str(ROOT / "uninstall.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        unchanged = claude_md.read_text(encoding="utf-8")
        settings_unchanged = settings.read_bytes() == settings_original

    test("Claude uninstall rejects incomplete ownership markers", result.returncode != 0, result.stdout + result.stderr)
    test("Claude uninstall preserves incomplete-marker content byte for byte", unchanged == original, unchanged)
    test("Claude uninstall preflights markers before removing hooks", settings_unchanged, result.stdout + result.stderr)


def test_uninstaller_preflights_all_runtime_configs_before_any_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        claude_settings = claude_dir / "settings.json"
        managed = "bash " + shlex.quote(str(ROOT / "hooks" / "claude_code_stop.sh"))
        claude_settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"matcher": "", "hooks": [{"type": "command", "command": managed}]}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text(
            "before\n<!-- OVERWATCH:BEGIN -->\nmanaged\n<!-- OVERWATCH:END -->\nafter\n",
            encoding="utf-8",
        )
        codex_config = base / "hooks.json"
        codex_config.write_text("{broken", encoding="utf-8")
        settings_before = claude_settings.read_bytes()
        claude_before = claude_md.read_bytes()
        result = subprocess.run(
            ["bash", str(ROOT / "uninstall.sh")],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(home),
                "CC_SETTINGS_PATH": str(claude_settings),
                "CODEX_HOOKS_PATH": str(codex_config),
            },
        )

        settings_unchanged = claude_settings.read_bytes() == settings_before
        claude_unchanged = claude_md.read_bytes() == claude_before

    test("malformed second runtime config aborts uninstall", result.returncode != 0, result.stdout + result.stderr)
    test("cross-runtime preflight preserves the first hook config", settings_unchanged, result.stdout + result.stderr)
    test("cross-runtime preflight preserves CLAUDE.md", claude_unchanged, result.stdout + result.stderr)


def test_uninstaller_preserves_concurrent_config_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install_root = base / "install"
        shutil.copytree(
            ROOT,
            install_root,
            ignore=shutil.ignore_patterns(".git", "state", "reviews", "__pycache__", "*.pyc"),
        )
        uninstaller = install_root / "uninstall.sh"
        text = uninstaller.read_text(encoding="utf-8")
        needle = "        displaced = commit_staged(\n"
        injected = (
            "        if path == updates[0][0]:\n"
            "            path.write_text('{\"hooks\": {\"External\": []}}\\n', encoding='utf-8')\n"
            + needle
        )
        if needle not in text:
            raise AssertionError("uninstaller transaction injection point is missing")
        uninstaller.write_text(text.replace(needle, injected, 1), encoding="utf-8")
        config_dir = base / "claude"
        config_dir.mkdir()
        settings = config_dir / "settings.json"
        managed = "bash " + shlex.quote(str(install_root / "hooks" / "claude_code_stop.sh"))
        settings.write_text(
            json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [{"command": managed}]}]}}),
            encoding="utf-8",
        )

        result = subprocess.run(
            ["bash", str(uninstaller)],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(base / "home"),
                "CC_SETTINGS_PATH": str(settings),
                "CODEX_HOOKS_PATH": str(base / "missing-codex.json"),
            },
        )
        current = json.loads(settings.read_text(encoding="utf-8"))

    test("uninstaller rejects concurrent config edit", result.returncode != 0 and "concurrently modified" in result.stderr, result.stdout + result.stderr)
    test("uninstaller preserves concurrent config edit", "External" in current.get("hooks", {}), str(current))


def test_uninstaller_rejects_symlink_config() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_dir = root / "claude"
        config_dir.mkdir()
        target = config_dir / "real-settings.json"
        target.write_text('{"hooks": {"Stop": []}}\n', encoding="utf-8")
        before = target.read_bytes()
        linked = config_dir / "settings.json"
        linked.symlink_to(target)
        result = subprocess.run(
            ["bash", str(ROOT / "uninstall.sh")],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(root / "home"),
                "CC_SETTINGS_PATH": str(linked),
                "CODEX_HOOKS_PATH": str(root / "missing-codex.json"),
            },
        )
        unchanged = target.read_bytes() == before

    test("uninstaller rejects symbolic-link config", result.returncode != 0 and "symbolic-link" in result.stderr, result.stdout + result.stderr)
    test("uninstaller leaves symbolic-link target unchanged", unchanged)


def test_installer_preserves_unmarked_overwatch_like_user_section() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        settings.write_text('{"hooks": {}}\n', encoding="utf-8")
        claude_md = claude_dir / "CLAUDE.md"
        user_section = "## Overwatch System\nThis heading belongs to the user.\n"
        claude_md.write_text(user_section, encoding="utf-8")

        run_installer(ROOT, home, settings)
        installed = claude_md.read_text(encoding="utf-8")

    test("Claude install preserves unmarked lookalike section", user_section in installed, installed)
    test("Claude install appends exactly one owned section", installed.count("<!-- OVERWATCH:BEGIN -->") == 1, installed)


if __name__ == "__main__":
    test_claude_installer_preflights_every_runtime_module()
    test_claude_installer_requires_snippet_before_mutation()
    test_claude_installer_relocates_managed_hooks_with_quoted_paths()
    test_claude_installer_uses_custom_settings_as_the_config_authority()
    test_claude_installer_preserves_concurrent_settings_edit()
    test_claude_installer_rejects_symlink_settings()
    test_installed_hook_runs_when_overwatch_path_contains_single_quote()
    test_claude_uninstall_removes_bash_prefixed_managed_hooks()
    test_claude_uninstall_removes_snippet_without_settings_file()
    test_installer_refuses_incomplete_markers_without_changing_claude_md()
    test_uninstaller_refuses_incomplete_markers_without_changing_claude_md()
    test_uninstaller_preflights_all_runtime_configs_before_any_write()
    test_uninstaller_preserves_concurrent_config_edit()
    test_uninstaller_rejects_symlink_config()
    test_installer_preserves_unmarked_overwatch_like_user_section()
    print("claude installer tests passed")
