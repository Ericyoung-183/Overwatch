#!/usr/bin/env python3
"""Regression tests for the Codex exec Overwatch backend."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codex_exec_client import build_isolated_review_prompt, build_codex_exec_command


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def test_prompt_has_review_payload_boundary() -> None:
    prompt = build_isolated_review_prompt(
        "SYSTEM RULES",
        "TRANSCRIPT PAYLOAD",
        nonce="test-nonce",
    )

    test("prompt contains payload start marker", "<<<OVERWATCH_REVIEW_PAYLOAD:test-nonce>>>" in prompt)
    test("prompt contains payload end marker", "<<<END_OVERWATCH_REVIEW_PAYLOAD:test-nonce>>>" in prompt)
    test("system prompt is inside payload", "SYSTEM RULES" in prompt)
    test("session transcript is inside payload", "TRANSCRIPT PAYLOAD" in prompt)
    test("outside runtime context is excluded", "Anything outside this payload" in prompt)
    test("skill/context noise is named as non-evidence", "skills, AGENTS files, hooks" in prompt)


def test_codex_command_disables_user_runtime_features() -> None:
    cmd = build_codex_exec_command("/tmp/review.txt", "/tmp/project")
    joined = " ".join(cmd)

    test("codex exec uses ephemeral mode", "--ephemeral" in cmd)
    test("codex exec ignores user config", "--ignore-user-config" in cmd)
    test("codex exec disables hooks", "--disable codex_hooks" in joined)
    test("codex exec disables plugins", "--disable plugins" in joined)
    test("codex exec disables memories", "--disable memories" in joined)
    test("codex exec disables tool search", "--disable tool_search" in joined)
    test("codex exec uses read-only sandbox", "-s read-only" in joined)


if __name__ == "__main__":
    test_prompt_has_review_payload_boundary()
    test_codex_command_disables_user_runtime_features()
    print("codex_exec_client tests passed")
