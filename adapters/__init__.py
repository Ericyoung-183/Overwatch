"""Overwatch adapter interface.

An adapter converts tool-specific session transcripts into a common Turn list.
Currently supported: claude_code (Claude Code JSONL format).
"""
from dataclasses import dataclass, field


@dataclass
class Turn:
    """A single conversational turn extracted from a session transcript."""
    index: int
    role: str       # "user" | "assistant" | "tool_use"
    content: str
    thinking: str = ""
    tool_name: str = ""
    timestamp: str = ""
    line_number: int = 0


def get_adapter(name: str = "claude_code"):
    """Return the parse function for the specified adapter.

    Args:
        name: Adapter name. Currently only "claude_code" is supported.

    Returns:
        A parse(transcript_path, offset) -> list[Turn] function.
    """
    if name == "claude_code":
        from adapters.claude_code import parse
        return parse
    raise ValueError(f"Unknown adapter: {name}. Available: claude_code")


def format_turn(turn: Turn) -> str:
    """Format a Turn into readable text for context building."""
    if turn.role == "user":
        return f"[User] {turn.content}"
    elif turn.role == "assistant":
        return f"[Assistant] {turn.content}"
    elif turn.role == "tool_use":
        return f"[Tool: {turn.tool_name}] {turn.content}"
    return f"[{turn.role}] {turn.content}"
