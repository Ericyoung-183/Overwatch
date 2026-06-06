"""Shared auto-review trigger policy for Overwatch hooks."""

from __future__ import annotations

import re
from typing import Iterable, Sequence


REVIEW_PATTERNS = [
    r"\breview\b",
    r"\b审查\b",
    r"\b诊断\b",
    r"检查",
    r"确认一下",
    r"看看对不对",
    r"完整检查",
]

CORRECTION_PATTERNS = [
    r"不对",
    r"错了",
    r"搞错",
    r"不是这样",
    r"重新来",
    r"再想想",
    r"\bwrong\b",
    r"\bnot what i\b",
    r"\bstill broken\b",
    r"\bredo\b",
]

FILE_CHANGE_TOOL_NAMES = {
    "edit",
    "multiedit",
    "write",
}


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _matches_any(text: str, patterns: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def _is_file_change_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    leaf = lowered.rsplit(".", 1)[-1]
    if leaf in FILE_CHANGE_TOOL_NAMES:
        return True
    return "apply_patch" in lowered


def _smart_signal(
    *,
    tool_names: Sequence[str],
    user_contents: Sequence[str],
    command_contents: Sequence[str],
) -> str:
    for text in user_contents[-3:]:
        if _matches_any(text, REVIEW_PATTERNS):
            return "user_review_request"

    for text in user_contents[-3:]:
        if _matches_any(text, CORRECTION_PATTERNS):
            return "user_correction"

    recent_tools = tool_names[-15:]
    if sum(1 for name in recent_tools if _is_file_change_tool(name)) >= 5:
        return "file_change_density"

    for command in command_contents[-5:]:
        lowered = command.lower()
        if "git commit" in lowered or "git push" in lowered:
            return "git_boundary"

    return ""


def evaluate_trigger(
    *,
    current_turns: int,
    last_reviewed_turn: int,
    review_count: int,
    tool_names: Sequence[str],
    user_contents: Sequence[str],
    command_contents: Sequence[str],
    turn_threshold: int,
    smart_trigger: object,
    turn_min: int,
    turn_max: int,
) -> dict[str, object]:
    """Decide whether a Stop hook should dispatch an auto-review."""
    current = _as_int(current_turns)
    last_reviewed = _as_int(last_reviewed_turn)
    review_count_value = _as_int(review_count)
    threshold = max(1, _as_int(turn_threshold, 10))
    floor = max(0, _as_int(turn_min, 5))
    ceiling = max(floor + 1, _as_int(turn_max, 15))
    diff = current - last_reviewed

    base = {
        "current_turns": current,
        "last_reviewed_turn": last_reviewed,
        "review_count": review_count_value,
        "diff": diff,
        "remaining": 0,
        "signal": "",
    }

    if diff < floor:
        return {
            **base,
            "should_trigger": False,
            "reason": "below_min_threshold",
            "remaining": floor - diff,
        }

    if diff >= ceiling:
        return {
            **base,
            "should_trigger": True,
            "reason": "hard_ceiling",
        }

    if not _as_bool(smart_trigger):
        if diff >= threshold:
            return {
                **base,
                "should_trigger": True,
                "reason": "baseline_threshold",
            }
        return {
            **base,
            "should_trigger": False,
            "reason": "below_baseline_threshold",
            "remaining": threshold - diff,
        }

    signal = _smart_signal(
        tool_names=tool_names,
        user_contents=user_contents,
        command_contents=command_contents,
    )
    if signal:
        return {
            **base,
            "should_trigger": True,
            "reason": "smart_signal",
            "signal": signal,
        }

    return {
        **base,
        "should_trigger": False,
        "reason": "below_max_threshold",
        "remaining": ceiling - diff,
    }


def summarize_turns_for_policy(turns: Iterable[object]) -> dict[str, object]:
    """Extract lightweight policy inputs from parsed transcript turns."""
    tool_names: list[str] = []
    user_contents: list[str] = []
    command_contents: list[str] = []
    user_count = 0

    for turn in turns:
        role = getattr(turn, "role", "")
        content = str(getattr(turn, "content", "") or "")
        if role == "user":
            user_count += 1
            user_contents.append(content[:500])
        elif role == "tool_use":
            tool_name = str(getattr(turn, "tool_name", "") or "")
            tool_names.append(tool_name)
            command_contents.append(content[:1000])

    return {
        "user_count": user_count,
        "tool_names": tool_names,
        "user_contents": user_contents,
        "command_contents": command_contents,
    }
