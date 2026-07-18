#!/usr/bin/env python3
"""Public release hygiene checks for the full Overwatch release candidate."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPRECATED_CODEX_HOOK_FEATURE = "codex" + "_hooks"
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


def candidate_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
        text=True,
    )
    return [line for line in output.splitlines() if line]


def test_public_files_have_no_eric_local_paths() -> None:
    offenders: list[str] = []
    for rel in candidate_files():
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

    test("candidate public files avoid Eric-local paths", not offenders, "\n".join(offenders))


def test_public_files_avoid_deprecated_codex_hook_feature_name() -> None:
    offenders: list[str] = []
    for rel in candidate_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if DEPRECATED_CODEX_HOOK_FEATURE in text:
            offenders.append(f"{rel}: contains deprecated Codex hook feature name")

    test("candidate public files avoid deprecated Codex hook feature name", not offenders, "\n".join(offenders))


def test_candidate_tree_contains_no_symbolic_links() -> None:
    offenders = [rel for rel in candidate_files() if (ROOT / rel).is_symlink()]
    test("candidate public files contain no symbolic links", not offenders, "\n".join(offenders))


def test_hooks_do_not_interpolate_install_path_into_python_source() -> None:
    offenders = []
    for name in (
        "claude_code_prompt.sh",
        "claude_code_stop.sh",
        "codex_prompt.sh",
        "codex_stop.sh",
    ):
        text = (ROOT / "hooks" / name).read_text(encoding="utf-8")
        if "sys.path.insert(0, '$OVERWATCH_DIR')" in text:
            offenders.append(name)
    test("hook Python imports survive quoted install paths", not offenders, "\n".join(offenders))


def test_runtime_hooks_have_no_global_latest_trigger() -> None:
    runtime_files = [
        ROOT / "hooks" / "codex_prompt.sh",
        ROOT / "hooks" / "claude_code_prompt.sh",
        ROOT / "claude_md_snippet.md",
    ]
    offenders = [
        str(path)
        for path in runtime_files
        if "latest_trigger.json" in path.read_text(encoding="utf-8")
    ]
    test("runtime hooks use only session-bound triggers", not offenders, "\n".join(offenders))


def test_release_check_script_exists() -> None:
    path = ROOT / "scripts" / "check_release.sh"
    test("release check script exists", path.exists(), str(path))
    if path.exists():
        text = path.read_text(encoding="utf-8")
        test("release check includes Claude compatibility", "test_claude_hook_compat.py" in text)
        test("release check includes Codex compatibility", "test_codex_hook_observability.py" in text)
        test("release check includes public hygiene", "test_public_release_clean.py" in text)
        test(
            "release check isolates runtime state",
            all(
                marker in text
                for marker in [
                    'OVERWATCH_STATE_DIR="$RUNTIME_TMP/state"',
                    'OVERWATCH_REVIEWS_DIR="$RUNTIME_TMP/reviews"',
                    'OVERWATCH_LOG_FILE="$RUNTIME_TMP/overwatch.log"',
                    'PYTHONPYCACHEPREFIX="$RUNTIME_TMP/pycache"',
                ]
            ),
            text,
        )
        test("release check snapshots candidate tree", text.count("candidate_manifest") >= 3, text)
        test("release check verifies candidate bytes and modes", "candidate.before" in text and "candidate.after" in text, text)
        test("release mutation gate includes ignored files", 'root.rglob("*")' in text and "git ls-files" not in text, text)
        test(
            "release mutation gate excludes only named runtime artifacts",
            all(name in text for name in ["overwatch.log", '"state"', '"reviews"', '"__pycache__"']),
            text,
        )


if __name__ == "__main__":
    test_public_files_have_no_eric_local_paths()
    test_public_files_avoid_deprecated_codex_hook_feature_name()
    test_candidate_tree_contains_no_symbolic_links()
    test_hooks_do_not_interpolate_install_path_into_python_source()
    test_runtime_hooks_have_no_global_latest_trigger()
    test_release_check_script_exists()
    print("public release clean tests passed")
