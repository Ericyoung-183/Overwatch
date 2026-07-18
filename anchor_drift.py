"""Anchor agenda drift categories and regression fixture classifier."""

from __future__ import annotations

import re


ANCHOR_DRIFT_CATEGORIES = [
    {
        "id": "missed-root-capture",
        "severity": "HIGH",
        "evidence_level": "heuristic",
        "signal": "A new ordered list enters sequential handling, but no Anchor tracker is created or read.",
        "suggestion": "Freeze the root list with Anchor before processing the first item.",
    },
    {
        "id": "missed-child-capture",
        "severity": "HIGH",
        "evidence_level": "heuristic",
        "signal": "An active item creates a nested sequential list, but the child agenda is not pushed.",
        "suggestion": "Push a child agenda and keep advancing inside it until it closes, pauses, or defers.",
    },
    {
        "id": "wrong-next-target",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "The user says next while a deeper agenda is active, but the Builder advances another level.",
        "suggestion": "Advance the deepest unfinished Anchor item only.",
    },
    {
        "id": "premature-parent-return",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "The Builder returns to the parent before the child agenda is closed, paused, or deferred.",
        "suggestion": "Require explicit child closure or deferral before resuming the parent agenda.",
    },
    {
        "id": "missing-parent-synthesis",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "A child closes, but the Builder advances the returned parent item before recording its synthesis.",
        "suggestion": "Show and acknowledge the parent Whole Picture, then run revise-conclusion before next.",
    },
    {
        "id": "stale-tracker-ignored",
        "severity": "MED",
        "evidence_level": "correlated",
        "signal": "A stale warning or active tracker exists, but the Builder ignores it and rebuilds context.",
        "suggestion": "Refresh or validate the tracker, then continue from the recorded current item.",
    },
    {
        "id": "prose-only-state-change",
        "severity": "MED",
        "evidence_level": "heuristic",
        "signal": "The Builder says an item is done, deferred, blocked, or skipped without updating Anchor state.",
        "suggestion": "Persist the state transition through the Anchor helper before moving on.",
    },
    {
        "id": "recreated-agenda-from-search",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "The Builder searches files or TODOs and replaces the frozen active agenda.",
        "suggestion": "Treat search results as evidence only; do not replace the active Anchor agenda.",
    },
    {
        "id": "missing-whole-picture",
        "severity": "MED",
        "evidence_level": "correlated",
        "signal": "A successful Anchor start remains pending, but the Builder does not show the matching Whole Picture.",
        "suggestion": "Display the exact agenda title, ordered items, and current marker before substantive work.",
    },
    {
        "id": "false-presentation-ack",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "The Builder acknowledges a presentation without a matching user-visible Whole Picture.",
        "suggestion": "Treat helper output as non-visible and acknowledge only after assistant text shows the exact picture.",
    },
    {
        "id": "missing-presentation-ack",
        "severity": "MED",
        "evidence_level": "correlated",
        "signal": "The matching Whole Picture is visible, but its presentation ID is never acknowledged.",
        "suggestion": "Run ack-presented for the exact presentation ID before any agenda mutation.",
    },
    {
        "id": "topic-switch-without-interrupt",
        "severity": "HIGH",
        "evidence_level": "heuristic",
        "signal": "The user explicitly switches to another sequential topic, but no interrupt frame preserves the current agenda.",
        "suggestion": "Push an interrupt agenda and resume the recorded return cursor after it closes.",
    },
    {
        "id": "unsupported-todo-misread",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "An unsupported or ambiguous canonical TODO is described as empty or as having zero open items.",
        "suggestion": "Fail loud on format uncertainty and require explicit adapter configuration before writes.",
    },
    {
        "id": "unsynced-todo-agenda",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "A TODO-backed agenda closes, pauses, or is abandoned with a durable sync obligation still pending.",
        "suggestion": "Complete todo-sync for the exact tracker before reporting the TODO workflow complete.",
    },
    {
        "id": "source-agenda-mismatch",
        "severity": "HIGH",
        "evidence_level": "correlated",
        "signal": "The explicit source list and the items passed to Anchor differ in membership or order.",
        "suggestion": "Recapture the exact source list before beginning item-by-item work.",
    },
]


def format_anchor_drift_rubric() -> str:
    """Return the compact rubric embedded in the Overwatch review prompt."""
    lines = ["Anchor drift categories and default severity:"]
    for category in ANCHOR_DRIFT_CATEGORIES:
        lines.append(
            "- [{severity}][{evidence_level}] {id}: {signal} Suggestion: {suggestion}".format(**category)
        )
    return "\n".join(lines)


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _has_list_signal(text: str) -> bool:
    numbered = len(re.findall(r"(?:^|\s)(?:\d+\.|[A-Z][、,，])", text))
    explicit_count = re.search(r"(?:两个|三个|四个|五个|2\s*个|3\s*个|4\s*个|5\s*个)", text)
    return numbered >= 2 or explicit_count is not None


def _conversation_turns(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?im)^(user|assistant|builder):\s*", text))
    turns: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        turns.append((match.group(1).lower(), text[match.end():end].strip()))
    return turns


def classify_anchor_drift(transcript_text: str) -> list[dict[str, str]]:
    """Classify agenda-drift signals in deterministic regression fixtures.

    This is intentionally conservative and fixture-oriented. Production reviews
    still rely on the reviewer prompt, while this helper keeps the rubric and
    canonical failure examples from silently drifting.
    """
    text = transcript_text.lower()
    findings: list[dict[str, str]] = []

    def add(category_id: str) -> None:
        category = next(item for item in ANCHOR_DRIFT_CATEGORIES if item["id"] == category_id)
        if category not in findings:
            findings.append(category.copy())

    has_anchor = "[anchor]" in text or "tracker:" in text
    has_deep_current = "current:" in text and ">" in text
    rebuilds_from_search = _has_any(text, ["搜索", "搜了一轮", "重新扫", "search", "todo"])
    replaces_agenda = _has_any(text, ["新的讨论清单", "新的清单", "replacement list"])
    sequential_intent = _has_any(text, ["逐一", "一个一个", "下一个", "next/continue", "next"])
    assistant_tail = text.rsplit("assistant:", 1)[-1] if "assistant:" in text else ""
    turns = _conversation_turns(text)

    for index, (role, body) in enumerate(turns):
        if role not in {"assistant", "builder"} or not _has_list_signal(body):
            continue
        next_user = next(
            (
                user_body
                for user_role, user_body in turns[index + 1:index + 3]
                if user_role == "user"
            ),
            "",
        )
        if not _has_any(next_user, ["逐一", "一个一个", "逐条", "逐项", "one by one"]):
            continue
        if has_anchor and "push-child" not in text:
            add("missed-child-capture")
        elif not has_anchor and "anchor.py init" not in text:
            add("missed-root-capture")

    if not has_anchor and sequential_intent and rebuilds_from_search:
        add("missed-root-capture")

    if has_anchor and _has_any(text, ["又拆出", "子清单", "nested", "1 方案"]) and "push-child" not in text:
        add("missed-child-capture")

    if has_deep_current and _has_any(text, ["user: 下一个", "user: next"]) and _has_any(text, ["进入 d", "start d"]):
        add("wrong-next-target")

    if has_deep_current and _has_any(text, ["回到父清单", "return to parent", "继续处理 d"]):
        add("premature-parent-return")

    parent_synthesis_required = _has_any(
        text,
        ["requires_parent_synthesis", "parent synthesis required", "需要父项总结"],
    )
    if (
        has_anchor
        and parent_synthesis_required
        and "anchor.py next" in text
        and "anchor.py revise-conclusion" not in text
    ):
        add("missing-parent-synthesis")

    if "warning: tracker stale" in text and _has_any(text, ["不看之前的 tracker", "ignore", "重新扫"]):
        add("stale-tracker-ignored")

    if has_anchor and _has_any(text, ["记为 deferred", "mark deferred", "跳过"]) and not _has_any(
        text,
        ["anchor.py defer", " defer --", "status\": \"deferred\""],
    ):
        add("prose-only-state-change")

    if has_anchor and rebuilds_from_search and replaces_agenda:
        add("recreated-agenda-from-search")

    pending_picture = "pending whole picture" in text
    visible_picture = "whole picture:" in assistant_tail and _has_any(assistant_tail, ["当前", "current", "←"])
    if pending_picture and "assistant:" in text and not visible_picture:
        add("missing-whole-picture")

    if "ack-presented" in text and not visible_picture:
        add("false-presentation-ack")
    if pending_picture and visible_picture and "anchor.py ack-presented" not in text:
        add("missing-presentation-ack")

    explicit_switch = _has_any(text, ["先处理另一个", "打断一下", "switch to another", "temporary topic"])
    if has_anchor and explicit_switch and "anchor.py interrupt" not in text:
        add("topic-switch-without-interrupt")

    unsupported_todo = _has_any(text, ["unsupported_format", "ambiguous_format", "parse_error"])
    false_empty = _has_any(text, ["open_count: 0", '"open_count": 0', "没有 todo", "todo is empty"])
    if unsupported_todo and false_empty:
        add("unsupported-todo-misread")

    todo_sync_required = _has_any(text, ["[anchor todo sync required]", "todo_sync_required"])
    todo_sync_completed = "anchor.py todo-sync" in text and _has_any(
        text, ['"status": "synced"', '"status":"synced"', '"status": "no_changes"']
    )
    if todo_sync_required and not todo_sync_completed:
        add("unsynced-todo-agenda")

    source_match = re.search(r"source agenda:\s*(.+)", text)
    captured_match = re.search(r"captured agenda:\s*(.+)", text)
    if source_match and captured_match and source_match.group(1).strip() != captured_match.group(1).strip():
        add("source-agenda-mismatch")

    return findings
