#!/usr/bin/env python3
"""Regression tests for shared auto-review trigger policy."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import Turn  # noqa: E402
from trigger_policy import evaluate_trigger, summarize_turns_for_policy  # noqa: E402


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def decision(**overrides):
    kwargs = {
        "current_turns": 6,
        "last_reviewed_turn": 0,
        "review_count": 0,
        "tool_names": [],
        "user_contents": ["normal message"],
        "command_contents": [],
        "turn_threshold": 10,
        "smart_trigger": True,
        "turn_min": 5,
        "turn_max": 15,
    }
    kwargs.update(overrides)
    return evaluate_trigger(**kwargs)


def test_below_min_never_triggers() -> None:
    result = decision(current_turns=4, user_contents=["请完整检查"])
    test("below min does not trigger", not result["should_trigger"], str(result))
    test("below min reason is stable", result["reason"] == "below_min_threshold", str(result))


def test_hard_ceiling_triggers() -> None:
    result = decision(current_turns=15)
    test("hard ceiling triggers", bool(result["should_trigger"]), str(result))
    test("hard ceiling reason is stable", result["reason"] == "hard_ceiling", str(result))


def test_smart_review_request_triggers_between_min_and_max() -> None:
    result = decision(user_contents=["请完整检查一下这轮修改"])
    test("review request triggers", bool(result["should_trigger"]), str(result))
    test("review request signal is named", result["signal"] == "user_review_request", str(result))


def test_smart_correction_triggers_between_min_and_max() -> None:
    result = decision(user_contents=["不对，重新来"])
    test("correction triggers", bool(result["should_trigger"]), str(result))
    test("correction signal is named", result["signal"] == "user_correction", str(result))


def test_smart_file_change_density_supports_claude_and_codex_tools() -> None:
    claude = decision(tool_names=["Read", "Edit", "Edit", "Write", "Edit", "Write"])
    codex = decision(tool_names=[
        "functions.apply_patch",
        "functions.apply_patch",
        "functions.apply_patch",
        "functions.apply_patch",
        "functions.apply_patch",
    ])

    test("Claude edit density triggers", bool(claude["should_trigger"]), str(claude))
    test("Claude edit density signal is named", claude["signal"] == "file_change_density", str(claude))
    test("Codex apply_patch density triggers", bool(codex["should_trigger"]), str(codex))
    test("Codex apply_patch density signal is named", codex["signal"] == "file_change_density", str(codex))


def test_smart_git_command_triggers() -> None:
    result = decision(command_contents=["cmd: git commit -m test\nstatus: completed"])
    test("git command triggers", bool(result["should_trigger"]), str(result))
    test("git command signal is named", result["signal"] == "git_boundary", str(result))


def test_no_signal_between_min_and_max_waits() -> None:
    result = decision()
    test("no signal waits", not result["should_trigger"], str(result))
    test("no signal reason is below max", result["reason"] == "below_max_threshold", str(result))


def test_smart_disabled_uses_baseline_threshold() -> None:
    below = decision(current_turns=9, smart_trigger=False)
    at_threshold = decision(current_turns=10, smart_trigger=False)

    test("smart disabled waits below baseline", not below["should_trigger"], str(below))
    test("smart disabled triggers at baseline", bool(at_threshold["should_trigger"]), str(at_threshold))
    test("smart disabled baseline reason", at_threshold["reason"] == "baseline_threshold", str(at_threshold))


def test_summarize_turns_extracts_policy_inputs() -> None:
    turns = [
        Turn(index=0, role="user", content="hello"),
        Turn(index=1, role="tool_use", tool_name="functions.exec_command", content='{"cmd": "git push origin main"}'),
        Turn(index=2, role="tool_use", tool_name="functions.apply_patch", content="patch"),
    ]
    summary = summarize_turns_for_policy(turns)

    test("summary counts users", summary["user_count"] == 1, str(summary))
    test("summary includes tool names", "functions.apply_patch" in summary["tool_names"], str(summary))
    test("summary includes command-like content", any("git push" in c for c in summary["command_contents"]), str(summary))


def test_stop_hooks_use_shared_policy() -> None:
    for hook_name in ["codex_stop.sh", "claude_code_stop.sh"]:
        text = (ROOT / "hooks" / hook_name).read_text(encoding="utf-8")
        test(f"{hook_name} imports shared trigger policy", "from trigger_policy import" in text)
        test(f"{hook_name} does not embed review patterns", "review_patterns =" not in text)
        test(f"{hook_name} does not embed correction patterns", "correction_patterns =" not in text)


if __name__ == "__main__":
    test_below_min_never_triggers()
    test_hard_ceiling_triggers()
    test_smart_review_request_triggers_between_min_and_max()
    test_smart_correction_triggers_between_min_and_max()
    test_smart_file_change_density_supports_claude_and_codex_tools()
    test_smart_git_command_triggers()
    test_no_signal_between_min_and_max_waits()
    test_smart_disabled_uses_baseline_threshold()
    test_summarize_turns_extracts_policy_inputs()
    test_stop_hooks_use_shared_policy()
    print("trigger policy tests passed")
