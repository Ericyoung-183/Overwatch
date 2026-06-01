"""Anchor agenda drift categories and regression fixture classifier."""

from __future__ import annotations


ANCHOR_DRIFT_CATEGORIES = [
    {
        "id": "missed-root-capture",
        "severity": "HIGH",
        "signal": "A new ordered list enters sequential handling, but no Anchor tracker is created or read.",
        "suggestion": "Freeze the root list with Anchor before processing the first item.",
    },
    {
        "id": "missed-child-capture",
        "severity": "HIGH",
        "signal": "An active item creates a nested sequential list, but the child agenda is not pushed.",
        "suggestion": "Push a child agenda and keep advancing inside it until it closes, pauses, or defers.",
    },
    {
        "id": "wrong-next-target",
        "severity": "HIGH",
        "signal": "The user says next while a deeper agenda is active, but the Builder advances another level.",
        "suggestion": "Advance the deepest unfinished Anchor item only.",
    },
    {
        "id": "premature-parent-return",
        "severity": "HIGH",
        "signal": "The Builder returns to the parent before the child agenda is closed, paused, or deferred.",
        "suggestion": "Require explicit child closure or deferral before resuming the parent agenda.",
    },
    {
        "id": "stale-tracker-ignored",
        "severity": "MED",
        "signal": "A stale warning or active tracker exists, but the Builder ignores it and rebuilds context.",
        "suggestion": "Refresh or validate the tracker, then continue from the recorded current item.",
    },
    {
        "id": "prose-only-state-change",
        "severity": "MED",
        "signal": "The Builder says an item is done, deferred, blocked, or skipped without updating Anchor state.",
        "suggestion": "Persist the state transition through the Anchor helper before moving on.",
    },
    {
        "id": "recreated-agenda-from-search",
        "severity": "HIGH",
        "signal": "The Builder searches files or TODOs and replaces the frozen active agenda.",
        "suggestion": "Treat search results as evidence only; do not replace the active Anchor agenda.",
    },
]


def format_anchor_drift_rubric() -> str:
    """Return the compact rubric embedded in the Overwatch review prompt."""
    lines = ["Anchor drift categories and default severity:"]
    for category in ANCHOR_DRIFT_CATEGORIES:
        lines.append(
            "- [{severity}] {id}: {signal} Suggestion: {suggestion}".format(**category)
        )
    return "\n".join(lines)


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


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

    if not has_anchor and sequential_intent and rebuilds_from_search:
        add("missed-root-capture")

    if has_anchor and _has_any(text, ["又拆出", "子清单", "nested", "1 方案"]) and "push-child" not in text:
        add("missed-child-capture")

    if has_deep_current and _has_any(text, ["user: 下一个", "user: next"]) and _has_any(text, ["进入 d", "start d"]):
        add("wrong-next-target")

    if has_deep_current and _has_any(text, ["回到父清单", "return to parent", "继续处理 d"]):
        add("premature-parent-return")

    if "warning: tracker stale" in text and _has_any(text, ["不看之前的 tracker", "ignore", "重新扫"]):
        add("stale-tracker-ignored")

    if has_anchor and _has_any(text, ["记为 deferred", "mark deferred", "跳过"]) and not _has_any(
        text,
        ["anchor.py defer", " defer --", "status\": \"deferred\""],
    ):
        add("prose-only-state-change")

    if has_anchor and rebuilds_from_search and replaces_agenda:
        add("recreated-agenda-from-search")

    return findings
