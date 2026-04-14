"""Context window management: rolling summary + recent window."""
import json
import os
import sys

from config import (
    RECENT_WINDOW_SIZE,
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_TOKENS,
    MAX_SUMMARY_INPUT_CHARS,
    SUMMARY_MODEL,
    STATE_DIR,
)
from adapters import Turn, format_turn


def load_state(session_id: str) -> dict:
    """Load or initialize session state."""
    path = os.path.join(STATE_DIR, f"{session_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "session_id": session_id,
        "last_reviewed_turn": 0,
        "last_parsed_line": 0,
        "last_summarized_turn_index": 0,
        "running_summary": "",
        "project_context": "",
        "review_count": 0,
    }


def save_state(session_id: str, state: dict):
    """Persist session state to disk."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{session_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def split_context_window(turns: list[Turn]) -> tuple[list[Turn], list[Turn]]:
    """Split turns into old (to be summarized) and recent (kept verbatim).

    Recent = last RECENT_WINDOW_SIZE user messages and all subsequent assistant/tool turns.
    Old = everything before that.
    """
    user_indices = [i for i, t in enumerate(turns) if t.role == "user"]

    if len(user_indices) <= RECENT_WINDOW_SIZE:
        return [], turns

    split_point = user_indices[-RECENT_WINDOW_SIZE]
    old = turns[:split_point]
    recent = turns[split_point:]
    return old, recent


def _call_summary_model(existing_summary: str, new_text: str) -> str:
    """Call the summary model to compress old summary + new conversation into an updated summary.

    Falls back to truncation if the API call fails.
    """
    from api_client import call_claude

    system_prompt = (
        "You are a conversation summarizer. Your task is to compress AI-assisted work sessions "
        "into concise summaries, preserving key information.\n\n"
        "Must preserve:\n"
        "- Key design decisions and their rationale\n"
        "- Files created/modified and what changed\n"
        "- Bugs encountered and their solutions\n"
        "- User preferences or constraints explicitly stated\n"
        "- TODOs and unfinished plans\n\n"
        "Can omit:\n"
        "- Detailed tool call parameters\n"
        "- Repeated trial-and-error (keep only the final solution)\n"
        "- Greetings and confirmatory dialogue\n\n"
        "Output the summary directly, no titles or prefixes. Keep under 1500 characters. "
        "Use the same language as the conversation."
    )

    # Pre-truncate to prevent exceeding model context
    budget = MAX_SUMMARY_INPUT_CHARS - len(existing_summary) - 500
    if budget < 1000:
        return _truncate_summary(existing_summary, new_text)
    if len(new_text) > budget:
        print(f"[Overwatch] new_text too large ({len(new_text)} chars), pre-truncating to {budget}", file=sys.stderr)
        new_text = new_text[:budget]

    if existing_summary:
        user_message = (
            f"## Existing Summary\n{existing_summary}\n\n"
            f"## New Conversation\n{new_text}\n\n"
            "Merge the existing summary with the new conversation into one updated summary."
        )
    else:
        user_message = (
            f"## Conversation\n{new_text}\n\n"
            "Summarize this conversation."
        )

    try:
        result = call_claude(system_prompt, user_message, model=SUMMARY_MODEL, max_tokens=MAX_SUMMARY_TOKENS, thinking=False)
        if result.startswith("[Overwatch") and ("Error" in result or "API Error" in result):
            raise RuntimeError(result)
        return result
    except Exception as e:
        print(f"[Overwatch] Summary model failed ({e}), falling back to truncation", file=sys.stderr)
        return _truncate_summary(existing_summary, new_text)


def _truncate_summary(existing_summary: str, new_text: str) -> str:
    """Fallback truncation when summary model call fails."""
    if not existing_summary:
        if len(new_text) > MAX_SUMMARY_CHARS:
            return new_text[:MAX_SUMMARY_CHARS - 30] + "\n\n... [truncated] ..."
        return new_text

    separator = "\n\n---\n\n"
    remaining = MAX_SUMMARY_CHARS - len(existing_summary) - len(separator)

    if remaining <= 100:
        return existing_summary

    if len(new_text) > remaining:
        new_text = new_text[:remaining - 30] + "\n\n... [new content truncated] ..."

    return f"{existing_summary}{separator}{new_text}"


def summarize_turns(turns: list[Turn], existing_summary: str = "") -> str:
    """Compress old turns into a summary. Uses summary model, falls back to truncation."""
    if not turns:
        return existing_summary

    new_text = "\n".join(format_turn(t) for t in turns)

    if len(new_text) < 500 and len(existing_summary) + len(new_text) < MAX_SUMMARY_CHARS:
        if existing_summary:
            return f"{existing_summary}\n\n---\n\n{new_text}"
        return new_text

    return _call_summary_model(existing_summary, new_text)


def build_review_context(
    turns: list[Turn],
    state: dict,
    project_description: str = "",
) -> tuple[str, dict]:
    """Build the complete context for the review API call.

    Returns:
        (context_text, updated_state)
    """
    old_turns, recent_turns = split_context_window(turns)

    # Incremental summarization: only process turns that newly fell out of the recent window
    last_summarized = state.get("last_summarized_turn_index", 0)
    new_old_turns = [t for t in old_turns if t.index >= last_summarized]
    running_summary = summarize_turns(new_old_turns, state.get("running_summary", ""))

    recent_text = "\n\n".join(format_turn(t) for t in recent_turns)

    # Project context: prefer externally-provided description, fall back to first user message
    project_context = project_description or state.get("project_context", "")
    if not project_context and turns:
        for t in turns:
            if t.role == "user":
                project_context = t.content[:300]
                break

    sections = []

    if project_context:
        sections.append(f"## Project Background\n{project_context}")

    if running_summary:
        sections.append(f"## Earlier Conversation Summary\n{running_summary}")

    sections.append(f"## Recent Conversation (verbatim)\n{recent_text}")

    context_text = "\n\n---\n\n".join(sections)

    user_turn_count = len([t for t in turns if t.role == "user"])
    last_summarized_index = old_turns[-1].index + 1 if old_turns else state.get("last_summarized_turn_index", 0)

    updated_state = {
        **state,
        "running_summary": running_summary,
        "project_context": project_context,
        "last_reviewed_turn": user_turn_count,
        "last_summarized_turn_index": last_summarized_index,
        "last_parsed_line": turns[-1].line_number + 1 if turns else state.get("last_parsed_line", 0),
        "review_count": state.get("review_count", 0) + 1,
    }

    return context_text, updated_state
