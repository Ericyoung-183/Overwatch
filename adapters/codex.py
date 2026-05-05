"""Codex Desktop transcript adapter.

Parses Codex Desktop JSONL session transcripts into Turn objects.
"""
import json

from config import SKIP_USER_PATTERNS, MAX_TURN_CONTENT_CHARS
from adapters import Turn


def _truncate(text: str, max_chars: int = MAX_TURN_CONTENT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head - 60
    return (
        text[:head]
        + f"\n\n... [truncated {len(text) - head - tail} chars, total {len(text)}] ...\n\n"
        + text[-tail:]
    )


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
            continue
        for key in ("input_text", "output_text"):
            value = block.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(p for p in parts if p)


def _skip_user_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith("# AGENTS.md instructions"):
        return True
    if stripped.startswith("<environment_context>"):
        return True
    return any(stripped.startswith(pattern) for pattern in SKIP_USER_PATTERNS)


def _summarize_tool_call(payload: dict) -> tuple[str, str]:
    name = payload.get("name", "")
    args = payload.get("arguments", "")
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return name, _truncate(args, 1500)


def _summarize_command_end(payload: dict) -> tuple[str, str]:
    parsed = payload.get("parsed_cmd") or []
    if parsed and isinstance(parsed[0], dict):
        name = parsed[0].get("type") or "command"
    else:
        name = "command"

    command = payload.get("command", "")
    if isinstance(command, list):
        command = " ".join(command)
    output = payload.get("aggregated_output", "")
    status = payload.get("status") or ""
    exit_code = payload.get("exit_code")
    text = f"cmd: {command}\nstatus: {status}\nexit_code: {exit_code}\noutput:\n{output}"
    return name, _truncate(text, 2000)


def parse(transcript_path: str, offset: int = 0) -> list[Turn]:
    """Parse a Codex Desktop JSONL transcript file."""
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

            outer_type = obj.get("type", "")
            payload = obj.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            payload_type = payload.get("type", "")
            timestamp = obj.get("timestamp", "")

            if outer_type == "response_item" and payload_type == "message":
                role = payload.get("role", "")
                text = _content_text(payload.get("content", []))
                if role == "user":
                    if _skip_user_text(text):
                        continue
                    turns.append(Turn(
                        index=turn_index,
                        role="user",
                        content=_truncate(text),
                        timestamp=timestamp,
                        line_number=line_num,
                    ))
                    turn_index += 1
                elif role == "assistant" and text.strip():
                    turns.append(Turn(
                        index=turn_index,
                        role="assistant",
                        content=_truncate(text),
                        timestamp=timestamp,
                        line_number=line_num,
                    ))
                    turn_index += 1

            elif outer_type == "response_item" and payload_type == "function_call":
                name, summary = _summarize_tool_call(payload)
                turns.append(Turn(
                    index=turn_index,
                    role="tool_use",
                    content=summary,
                    tool_name=name,
                    timestamp=timestamp,
                    line_number=line_num,
                ))
                turn_index += 1

            elif outer_type == "event_msg" and payload_type == "exec_command_end":
                name, summary = _summarize_command_end(payload)
                turns.append(Turn(
                    index=turn_index,
                    role="tool_use",
                    content=summary,
                    tool_name=name,
                    timestamp=timestamp,
                    line_number=line_num,
                ))
                turn_index += 1

    return turns


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m adapters.codex <transcript.jsonl> [offset]")
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
