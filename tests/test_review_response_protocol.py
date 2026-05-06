#!/usr/bin/env python3
"""Regression tests for Overwatch review delivery instructions."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from response_protocol import (  # noqa: E402
    REVIEW_RESPONSE_PROTOCOL,
    build_auto_review_context,
    build_manual_trigger_context,
)


REQUIRED_PROTOCOL_PHRASES = [
    "Present the full review text verbatim",
    "no rewriting, omitting, or merging",
    "separator line",
    "For each Issue and Recommendation",
    "Persistence is mandatory unless",
    "canonical TODO/backlog",
    "If no project backlog exists",
    "cite the exact file path",
    "Do not treat every Recommendation as memory by default",
    "fix now",
    "persist a TODO/backlog entry",
    "state why it is deferred",
    "[LESSON]",
    "save it to project memory",
    "durable operating rule",
    "Clean up the trigger file",
]


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def assert_protocol_present(name: str, text: str) -> None:
    missing = [phrase for phrase in REQUIRED_PROTOCOL_PHRASES if phrase not in text]
    test(name, not missing, f"missing phrases: {missing}")


def test_protocol_defines_closed_loop_handling() -> None:
    assert_protocol_present("shared protocol has closed-loop handling", REVIEW_RESPONSE_PROTOCOL)


def test_auto_context_embeds_protocol_and_review_text() -> None:
    context = build_auto_review_context(
        "REVIEW BODY",
        cleanup_command="rm -f state/latest_trigger.json",
    )

    test("auto context has auto-review marker", "[Overwatch Auto-Review]" in context)
    test("auto context includes review body", "REVIEW BODY" in context)
    test("auto context includes cleanup command", "rm -f state/latest_trigger.json" in context)
    assert_protocol_present("auto context includes full protocol", context)


def test_manual_context_embeds_protocol_and_commands() -> None:
    context = build_manual_trigger_context(
        review_command="python3 overwatch.py --force",
        find_review_command="bash hooks/find_review.sh",
        cleanup_command="rm -f state/latest_trigger.json",
    )

    test("manual context has manual trigger marker", "[Overwatch Manual Trigger]" in context)
    test("manual context includes review command", "python3 overwatch.py --force" in context)
    test("manual context includes find-review command", "bash hooks/find_review.sh" in context)
    test("manual context includes cleanup command", "rm -f state/latest_trigger.json" in context)
    assert_protocol_present("manual context includes full protocol", context)


def test_hooks_use_shared_protocol_builders() -> None:
    for hook in [
        ROOT / "hooks" / "codex_prompt.sh",
        ROOT / "hooks" / "claude_code_prompt.sh",
    ]:
        text = hook.read_text(encoding="utf-8")
        test(f"{hook.name} imports shared protocol", "from response_protocol import" in text)
        test(f"{hook.name} avoids weak legacy instruction", "Present this review verbatim, then respond point by point" not in text)
        test(f"{hook.name} avoids weak manual instruction", "Present the full review verbatim, then respond point by point" not in text)


def test_install_snippet_uses_protocol_placeholder() -> None:
    text = (ROOT / "claude_md_snippet.md").read_text(encoding="utf-8")
    test("install snippet has protocol placeholder", "{{REVIEW_RESPONSE_PROTOCOL}}" in text)


if __name__ == "__main__":
    test_protocol_defines_closed_loop_handling()
    test_auto_context_embeds_protocol_and_review_text()
    test_manual_context_embeds_protocol_and_commands()
    test_hooks_use_shared_protocol_builders()
    test_install_snippet_uses_protocol_placeholder()
    print("review_response_protocol tests passed")
