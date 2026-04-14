"""Claude Code transcript adapter.

Parses Claude Code's JSONL session transcripts into Turn objects.
Each line in the JSONL is a JSON object with a 'type' field indicating the message type.
"""
import json

from config import SKIP_TYPES, SKIP_USER_PATTERNS, MAX_TURN_CONTENT_CHARS
from adapters import Turn


def _truncate(text: str, max_chars: int = MAX_TURN_CONTENT_CHARS) -> str:
    """Truncate long text, preserving head and tail."""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head - 60
    return (
        text[:head]
        + f"\n\n... [truncated {len(text) - head - tail} chars, total {len(text)}] ...\n\n"
        + text[-tail:]
    )


def _is_skip_user_message(content: str) -> bool:
    """Check if a user message is meta/system and should be skipped."""
    for pattern in SKIP_USER_PATTERNS:
        if content.strip().startswith(pattern):
            return True
    return False


def _extract_assistant_blocks(content_list: list) -> tuple[str, str, list[dict]]:
    """Extract text, thinking, and tool_use blocks from assistant message content array."""
    texts = []
    thinkings = []
    tool_uses = []

    for block in content_list:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")
        if block_type == "text":
            texts.append(block.get("text", ""))
        elif block_type == "thinking":
            thinkings.append(block.get("thinking", ""))
        elif block_type == "tool_use":
            name = block.get("name", "")
            tool_uses.append({
                "name": name,
                "input_summary": _summarize_tool_input(name, block.get("input", {})),
            })

    return "\n".join(texts), "\n".join(thinkings), tool_uses


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Extract key info from tool input. Preserves detail for code-modifying tools."""
    if not tool_input:
        return ""

    if tool_name == "Write":
        path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        return f"file: {path}\n```\n{_truncate(content, 1000)}\n```"

    if tool_name == "Edit":
        path = tool_input.get("file_path", "")
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        return (
            f"file: {path}\n"
            f"--- old ---\n{_truncate(old, 500)}\n"
            f"+++ new +++\n{_truncate(new, 500)}"
        )

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return f"cmd: {_truncate(cmd, 500)}"

    if "file_path" in tool_input:
        return f"file: {tool_input['file_path']}"
    if "pattern" in tool_input:
        path = tool_input.get("path", "")
        return f"pattern: {tool_input['pattern']}" + (f" in {path}" if path else "")
    if "query" in tool_input:
        return f"query: {tool_input['query'][:200]}"
    if "prompt" in tool_input:
        return f"prompt: {tool_input['prompt'][:200]}"

    keys = list(tool_input.keys())[:5]
    return f"keys: {', '.join(keys)}"


def parse(transcript_path: str, offset: int = 0) -> list[Turn]:
    """Parse a Claude Code JSONL transcript file.

    Args:
        transcript_path: Path to the JSONL file.
        offset: Line number to start reading from (for incremental parsing).

    Returns:
        List of Turn objects.
    """
    turns: list[Turn] = []
    turn_index = offset

    with open(transcript_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            if line_num < offset:
                continue

            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")

            if msg_type in SKIP_TYPES:
                continue

            if msg_type == "user":
                message = obj.get("message", {})
                content = message.get("content", "")
                if not isinstance(content, str):
                    continue
                if obj.get("isMeta") or _is_skip_user_message(content):
                    continue
                if not content.strip():
                    continue

                turns.append(Turn(
                    index=turn_index,
                    role="user",
                    content=_truncate(content),
                    timestamp=obj.get("timestamp", ""),
                    line_number=line_num,
                ))
                turn_index += 1

            elif msg_type == "assistant":
                message = obj.get("message", {})
                content_list = message.get("content", [])
                if not isinstance(content_list, list):
                    continue

                text, thinking, tool_uses = _extract_assistant_blocks(content_list)

                if text.strip():
                    turns.append(Turn(
                        index=turn_index,
                        role="assistant",
                        content=_truncate(text),
                        thinking=_truncate(thinking, 500),
                        timestamp=obj.get("timestamp", ""),
                        line_number=line_num,
                    ))
                    turn_index += 1

                for tu in tool_uses:
                    turns.append(Turn(
                        index=turn_index,
                        role="tool_use",
                        content=tu["input_summary"],
                        tool_name=tu["name"],
                        timestamp=obj.get("timestamp", ""),
                        line_number=line_num,
                    ))
                    turn_index += 1

    return turns


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m adapters.claude_code <transcript.jsonl> [offset]")
        sys.exit(1)

    path = sys.argv[1]
    off = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    from adapters import format_turn
    result = parse(path, off)

    print(f"Parsed {len(result)} turns (offset={off}):\n")
    for t in result:
        preview = format_turn(t)
        if len(preview) > 200:
            preview = preview[:200] + "..."
        print(f"  #{t.index} [line {t.line_number}] {preview}")
