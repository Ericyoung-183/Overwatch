#!/usr/bin/env python3
"""Public release hygiene checks for tracked Overwatch files."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BANNED_SUBSTRINGS = [
    "/Users/" + "eric",
    "AI" + "杂货",
    "Desktop/" + "AI",
]

ALLOWED_TRACKED_PATHS = {
    "tests/test_public_release_clean.py",
}


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files"],
        text=True,
    )
    return [line for line in output.splitlines() if line]


def test_public_files_have_no_eric_local_paths() -> None:
    offenders: list[str] = []
    for rel in tracked_files():
        if rel in ALLOWED_TRACKED_PATHS:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in BANNED_SUBSTRINGS:
            if needle in text:
                offenders.append(f"{rel}: contains {needle!r}")

    test("tracked public files avoid Eric-local paths", not offenders, "\n".join(offenders))


def test_release_check_script_exists() -> None:
    path = ROOT / "scripts" / "check_release.sh"
    test("release check script exists", path.exists(), str(path))
    if path.exists():
        text = path.read_text(encoding="utf-8")
        test("release check includes Claude compatibility", "test_claude_hook_compat.py" in text)
        test("release check includes Codex compatibility", "test_codex_hook_observability.py" in text)
        test("release check includes public hygiene", "test_public_release_clean.py" in text)


if __name__ == "__main__":
    test_public_files_have_no_eric_local_paths()
    test_release_check_script_exists()
    print("public release clean tests passed")
