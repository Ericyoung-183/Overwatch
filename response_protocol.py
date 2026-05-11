"""Shared instructions for handling delivered Overwatch reviews."""

from __future__ import annotations


REVIEW_RESPONSE_PROTOCOL = """Review response protocol:
- First: Present the full review text verbatim (no rewriting, omitting, or merging).
- Then print a separator line.
- Then respond point by point with judgment, action, or pushback.
- For each Issue and Recommendation, first decide whether it is immediately actionable.
- Fix now is the default for actionable items: fix now in the current turn, then report the changed files plus verification evidence.
- Only use TODO/backlog when the item is explicitly blocked, requires user decision, or is outside the current task boundary.
- A TODO/backlog entry is a deferral record, not closure: include owner, blocking reason, risk, timeline or concrete trigger/checkpoint, and enough context so a future review can verify whether it was executed.
- If the item is not fixed now, choose one:
  - persist a TODO/backlog entry that satisfies the deferral requirements above;
  - state why it is deferred, including the risk and the condition to revisit it;
  - reject it as incorrect with evidence.
- Persistence is mandatory unless the item is fixed now or explicitly rejected as incorrect.
- Persist unresolved Issues and Recommendations to the current project's canonical TODO/backlog, and cite the exact file path in the response.
- If no project backlog exists, create or update a project memory TODO/feedback entry instead, and cite the exact file path.
- Do not treat every Recommendation as memory by default; memory is required for [LESSON] items and for recommendations that define a durable operating rule.
- If the review contains a [LESSON], save it to project memory before continuing.
- Clean up the trigger file after presenting the review.
- Do not continue the user's current request until this review handling is complete."""


def build_auto_review_context(review_text: str, *, cleanup_command: str | None = None) -> str:
    """Build additionalContext for a delivered auto-review."""

    cleanup_block = ""
    if cleanup_command:
        cleanup_block = f"\n\nAfter presenting the review, run:\n{cleanup_command}"

    return (
        "[Overwatch Auto-Review]\n"
        f"{REVIEW_RESPONSE_PROTOCOL}\n\n"
        "Review text to present verbatim:\n"
        "<<<OVERWATCH_REVIEW_TEXT>>>\n"
        f"{review_text}\n"
        "<<<END_OVERWATCH_REVIEW_TEXT>>>"
        f"{cleanup_block}"
    )


def build_manual_trigger_context(
    *,
    review_command: str,
    find_review_command: str,
    cleanup_command: str,
) -> str:
    """Build additionalContext for a manual review trigger."""

    return (
        "[Overwatch Manual Trigger] Run this review now:\n"
        f"{review_command}\n"
        "Then read the review:\n"
        f"{find_review_command}\n\n"
        f"{REVIEW_RESPONSE_PROTOCOL}\n\n"
        "After presenting the review, run:\n"
        f"{cleanup_command}"
    )
