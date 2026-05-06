"""Shared instructions for handling delivered Overwatch reviews."""

from __future__ import annotations


REVIEW_RESPONSE_PROTOCOL = """Review response protocol:
- First: Present the full review text verbatim (no rewriting, omitting, or merging).
- Then print a separator line.
- Then respond point by point with judgment, action, or pushback.
- For each Issue and Recommendation, choose one:
  - fix now and report the changed files plus verification evidence;
  - persist a TODO/backlog entry with owner/timeline or a concrete next checkpoint;
  - state why it is deferred, including the risk and the condition to revisit it.
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
