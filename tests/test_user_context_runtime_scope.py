#!/usr/bin/env python3
"""Regression tests for runtime-scoped Overwatch user context."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def _encoded_project_path(project_cwd: str) -> str:
    return "".join(c if c.isascii() and c not in "/" else "-" for c in project_cwd)


def _read_context(home: Path, project: Path, cc_projects: Path, *, adapter: str, legacy: bool = False) -> str:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "OVERWATCH_ADAPTER": adapter,
            "OVERWATCH_CC_PROJECTS": str(cc_projects),
            "OVERWATCH_INCLUDE_LEGACY_CONTEXT": "true" if legacy else "false",
            "OW_TEST_PROJECT": str(project),
            "PYTHONPATH": str(ROOT),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "from overwatch import _read_user_context; "
                "print(_read_user_context(os.environ['OW_TEST_PROJECT']))"
            ),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_codex_context_excludes_legacy_by_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        project = base / "project"
        cc_projects = base / "cc-projects"
        (home / ".codex").mkdir(parents=True)
        (home / ".claude").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)

        (home / ".codex" / "AGENTS.md").write_text("CODEX GLOBAL", encoding="utf-8")
        (home / ".claude" / "CLAUDE.md").write_text("CLAUDE GLOBAL", encoding="utf-8")
        (project / "AGENTS.md").write_text("CODEX PROJECT", encoding="utf-8")
        (project / ".claude" / "CLAUDE.md").write_text("CLAUDE PROJECT", encoding="utf-8")

        memory_dir = cc_projects / _encoded_project_path(str(project)) / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "feedback_noise.md").write_text("LEGACY MEMORY NOISE", encoding="utf-8")

        context = _read_context(home, project, cc_projects, adapter="codex")
        test("Codex context includes Codex global rules", "CODEX GLOBAL" in context)
        test("Codex context includes Codex project context", "CODEX PROJECT" in context)
        test("Codex context excludes Claude global by default", "CLAUDE GLOBAL" not in context)
        test("Codex context excludes Claude project by default", "CLAUDE PROJECT" not in context)
        test("Codex context excludes legacy feedback memory by default", "LEGACY MEMORY NOISE" not in context)

        legacy_context = _read_context(home, project, cc_projects, adapter="codex", legacy=True)
        test("Explicit legacy flag includes Claude global context", "CLAUDE GLOBAL" in legacy_context)
        test("Explicit legacy flag includes legacy feedback memory", "LEGACY MEMORY NOISE" in legacy_context)


def test_claude_context_uses_claude_sources() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        project = base / "project"
        cc_projects = base / "cc-projects"
        (home / ".codex").mkdir(parents=True)
        (home / ".claude").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)

        (home / ".codex" / "AGENTS.md").write_text("CODEX GLOBAL", encoding="utf-8")
        (home / ".claude" / "CLAUDE.md").write_text("CLAUDE GLOBAL", encoding="utf-8")
        (project / "AGENTS.md").write_text("CODEX PROJECT", encoding="utf-8")
        (project / ".claude" / "CLAUDE.md").write_text("CLAUDE PROJECT", encoding="utf-8")

        memory_dir = cc_projects / _encoded_project_path(str(project)) / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "feedback_signal.md").write_text("CLAUDE MEMORY SIGNAL", encoding="utf-8")

        context = _read_context(home, project, cc_projects, adapter="claude_code")
        test("Claude context includes Claude global rules", "CLAUDE GLOBAL" in context)
        test("Claude context includes Claude project context", "CLAUDE PROJECT" in context)
        test("Claude context includes Claude feedback memory", "CLAUDE MEMORY SIGNAL" in context)
        test("Claude context excludes Codex global by default", "CODEX GLOBAL" not in context)
        test("Claude context excludes Codex project by default", "CODEX PROJECT" not in context)


if __name__ == "__main__":
    test_codex_context_excludes_legacy_by_default()
    test_claude_context_uses_claude_sources()
    print("user context runtime scope tests passed")
