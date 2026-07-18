#!/usr/bin/env python3
"""Real-format regression tests for transcript adapters."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.claude_code import parse as parse_claude, transcript_session_ids as claude_session_ids
from adapters.codex import parse as parse_codex, transcript_session_ids as codex_session_ids
from overwatch import transcript_identity_error


def test(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"  PASS {name}")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def test_codex_current_response_items_are_preserved() -> None:
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "[Anchor]\nCurrent: B\nCursor token: c1"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "functions.exec",
                "input": "python3 anchor.py finish --thread-id t",
                "call_id": "call-1",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-1",
                "output": [{"type": "text", "text": '{"success":true,"current_path":["C"]}'}],
            },
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "codex.jsonl"
        write_jsonl(path, records)
        turns = parse_codex(str(path))

    test("Codex adapter keeps Anchor developer context", turns[0].tool_name == "anchor_context", str(turns))
    test("Codex adapter keeps custom tool input", "anchor.py finish" in turns[1].content, str(turns))
    test("Codex adapter keeps correlated tool output", turns[2].tool_name == "functions.exec_output", str(turns))
    test("Codex adapter keeps tool output evidence", "current_path" in turns[2].content, str(turns))


def test_claude_block_user_and_tool_result_are_preserved() -> None:
    records = [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "请检查这张图"},
                    {"type": "image", "source": {"type": "base64", "data": "..."}},
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "done"},
                ]
            },
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude.jsonl"
        write_jsonl(path, records)
        turns = parse_claude(str(path))

    test("Claude adapter keeps block user text", turns[0].role == "user" and "请检查" in turns[0].content, str(turns))
    test("Claude adapter marks image input", "[image]" in turns[0].content, str(turns))
    test("Claude adapter keeps tool result", turns[1].role == "tool_use" and turns[1].content == "done", str(turns))


def test_native_transcript_identity_is_extracted_and_enforced() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        codex_path = Path(tmp) / "codex.jsonl"
        claude_path = Path(tmp) / "claude.jsonl"
        write_jsonl(
            codex_path,
            [{"type": "session_meta", "payload": {"id": "codex-native"}}],
        )
        write_jsonl(
            claude_path,
            [{"type": "user", "sessionId": "claude-native", "message": {"content": "hi"}}],
        )

        codex_ids = codex_session_ids(str(codex_path))
        claude_ids = claude_session_ids(str(claude_path))

    test("Codex adapter extracts native session id", codex_ids == {"codex-native"}, str(codex_ids))
    test("Claude adapter extracts native session id", claude_ids == {"claude-native"}, str(claude_ids))
    test("matching transcript identity passes", transcript_identity_error("codex-native", codex_ids) == "")
    test("wrong transcript identity is rejected", transcript_identity_error("wrong", codex_ids) == "session_id_mismatch")
    test("missing transcript identity is rejected", transcript_identity_error("expected", set()) == "missing_native_session_id")


def test_codex_anchor_warning_context_is_preserved() -> None:
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "[Anchor Context Boundary]\n[Anchor Warning]\nState unreadable.",
                    }
                ],
            },
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "warning.jsonl"
        write_jsonl(path, records)
        turns = parse_codex(str(path))

    test("Codex adapter keeps Anchor warning evidence", len(turns) == 1 and "State unreadable" in turns[0].content, str(turns))


def test_codex_subagent_notifications_are_not_user_turns() -> None:
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "<subagent_notification>\n"
                            '{"agent_id":"agent-1","status":"completed"}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "请继续检查"}],
            },
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "subagent-notification.jsonl"
        write_jsonl(path, records)
        turns = parse_codex(str(path))

    test(
        "Codex adapter excludes internal subagent notification",
        len(turns) == 1 and turns[0].role == "user" and turns[0].content == "请继续检查",
        str(turns),
    )


def test_adapters_ignore_valid_json_scalar_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        codex_path = Path(tmp) / "codex-scalars.jsonl"
        claude_path = Path(tmp) / "claude-scalars.jsonl"
        scalar_prefix = "[]\n\"text\"\nnull\n"
        codex_path.write_text(
            scalar_prefix
            + json.dumps({"type": "session_meta", "payload": {"id": "codex-scalar-safe"}})
            + "\n",
            encoding="utf-8",
        )
        claude_path.write_text(
            scalar_prefix
            + json.dumps({"type": "user", "sessionId": "claude-scalar-safe", "message": {"content": "hi"}})
            + "\n",
            encoding="utf-8",
        )

        codex_turns = parse_codex(str(codex_path))
        claude_turns = parse_claude(str(claude_path))
        codex_ids = codex_session_ids(str(codex_path))
        claude_ids = claude_session_ids(str(claude_path))

    test("Codex adapter ignores JSON scalar records", codex_turns == [] and codex_ids == {"codex-scalar-safe"}, str(codex_turns))
    test("Claude adapter ignores JSON scalar records", len(claude_turns) == 1 and claude_ids == {"claude-scalar-safe"}, str(claude_turns))


def test_claude_adapter_ignores_scalar_messages_and_safely_summarizes_scalar_input() -> None:
    records = [
        {"type": "user", "message": None},
        {"type": "assistant", "message": "not-an-object"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": "echo unsafe-shape",
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": None, "content": ["nested"]},
                    },
                    {
                        "type": "tool_use",
                        "name": "Search",
                        "input": {"query": 42},
                    },
                ]
            },
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude-nested-scalars.jsonl"
        write_jsonl(path, records)
        turns = parse_claude(str(path))

    test(
        "Claude adapter skips scalar message containers",
        len(turns) == 3,
        str(turns),
    )
    test(
        "Claude adapter normalizes nested scalar tool fields",
        '"nested"' in turns[1].content and "query: 42" in turns[2].content,
        str(turns),
    )
    test(
        "Claude adapter safely summarizes scalar tool input",
        turns[0].role == "tool_use"
        and turns[0].tool_name == "Bash"
        and "echo unsafe-shape" in turns[0].content,
        str(turns),
    )


def test_recent_user_and_assistant_messages_are_not_per_turn_truncated() -> None:
    long_text = "HEAD-REQUIREMENT\n" + ("a" * 5000) + "\nMIDDLE-REQUIREMENT\n" + ("b" * 5000) + "\nTAIL-REQUIREMENT"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        codex_path = root / "codex-long.jsonl"
        claude_path = root / "claude-long.jsonl"
        write_jsonl(
            codex_path,
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": long_text}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": long_text}],
                    },
                },
            ],
        )
        write_jsonl(
            claude_path,
            [
                {"type": "user", "message": {"content": long_text}},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": long_text}]},
                },
            ],
        )
        codex_turns = parse_codex(str(codex_path))
        claude_turns = parse_claude(str(claude_path))

    test("Codex keeps complete recent user and assistant messages", [turn.content for turn in codex_turns] == [long_text, long_text], str([len(turn.content) for turn in codex_turns]))
    test("Claude keeps complete recent user and assistant messages", [turn.content for turn in claude_turns] == [long_text, long_text], str([len(turn.content) for turn in claude_turns]))
    test("middle-of-message requirements survive both adapters", all("MIDDLE-REQUIREMENT" in turn.content for turn in [*codex_turns, *claude_turns]))


if __name__ == "__main__":
    test_codex_current_response_items_are_preserved()
    test_claude_block_user_and_tool_result_are_preserved()
    test_native_transcript_identity_is_extracted_and_enforced()
    test_codex_anchor_warning_context_is_preserved()
    test_codex_subagent_notifications_are_not_user_turns()
    test_adapters_ignore_valid_json_scalar_records()
    test_claude_adapter_ignores_scalar_messages_and_safely_summarizes_scalar_input()
    test_recent_user_and_assistant_messages_are_not_per_turn_truncated()
    print("adapter tests passed")
