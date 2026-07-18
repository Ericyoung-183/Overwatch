#!/usr/bin/env python3
"""Security regressions for reviewer-invoked read-only tools."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import execute_tool


def test(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"  PASS {name}")


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_git_diff_ref_is_resolved_before_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        repo.mkdir()
        run_git(repo, "init")
        run_git(repo, "config", "user.email", "overwatch@example.test")
        run_git(repo, "config", "user.name", "Overwatch Test")
        tracked = repo / "tracked.txt"
        tracked.write_text("before\n", encoding="utf-8")
        run_git(repo, "add", "tracked.txt")
        run_git(repo, "commit", "-m", "baseline")
        tracked.write_text("after\n", encoding="utf-8")

        outside = base / "attacker-output"
        malicious = execute_tool(
            "git_diff",
            {"ref": f"--output={outside}"},
            str(repo),
        )
        valid = execute_tool("git_diff", {"ref": "HEAD"}, str(repo))

    test("option-like git refs are rejected", "invalid git ref" in malicious, malicious)
    test("malicious git ref cannot create an external file", not outside.exists(), str(outside))
    test("a validated symbolic ref still returns the working-tree diff", "-before" in valid and "+after" in valid, valid)


def test_grep_pattern_cannot_be_reinterpreted_as_an_option() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "tracked.txt").write_text("--help\nordinary\n", encoding="utf-8")
        result = execute_tool("grep_codebase", {"pattern": "--help"}, str(repo))

    test("option-like grep patterns remain literal patterns", "tracked.txt" in result, result)


if __name__ == "__main__":
    test_git_diff_ref_is_resolved_before_execution()
    test_grep_pattern_cannot_be_reinterpreted_as_an_option()
    print("review tool security tests passed")
