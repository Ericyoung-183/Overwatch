#!/usr/bin/env python3
"""Compatibility checks for the Claude Code hook path."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def without_codex_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", None)
    env.pop("OVERWATCH_ADAPTER", None)
    env.pop("OVERWATCH_BACKEND", None)
    env.pop("OVERWATCH_REVIEW_MODEL", None)
    return env


def run_hook(script: str, payload: dict[str, str]) -> dict[str, object]:
    proc = subprocess.run(
        ["bash", str(ROOT / "hooks" / script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=without_codex_env(),
    )
    return json.loads(proc.stdout)


def test_non_codex_config_defaults_are_claude_api() -> None:
    code = """
import json
import sys
sys.path.insert(0, %r)
import config
print(json.dumps({
    "adapter": config.ADAPTER,
    "backend": config.REVIEW_BACKEND,
    "model": config.REVIEW_MODEL,
}, sort_keys=True))
""" % str(ROOT)
    output = subprocess.check_output(["python3", "-c", code], text=True, env=without_codex_env())
    cfg = json.loads(output)

    test("Claude/default adapter stays claude_code", cfg["adapter"] == "claude_code", str(cfg))
    test("Claude/default backend stays api", cfg["backend"] == "api", str(cfg))
    test("Claude/default model stays Claude", cfg["model"].startswith("claude-"), str(cfg))


def test_claude_stop_still_uses_system_message_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "claude.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "message": {"content": "hello"}}),
                    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
                ]
            ),
            encoding="utf-8",
        )
        response = run_hook(
            "claude_code_stop.sh",
            {
                "session_id": "claude-compat-stop",
                "transcript_path": str(transcript),
                "cwd": tmp,
            },
        )

    message = str(response.get("systemMessage", ""))
    test("Claude stop returns systemMessage", bool(message), str(response))
    test("Claude stop message mentions Overwatch", "[Overwatch]" in message, message)


def test_claude_manual_trigger_uses_shared_additional_context_protocol() -> None:
    response = run_hook(
        "claude_code_prompt.sh",
        {
            "session_id": "claude-compat-manual",
            "transcript_path": "/tmp/claude-compat.jsonl",
            "cwd": "/tmp/claude-project",
            "user_prompt": "overwatch",
        },
    )
    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))

    test("Claude manual trigger delivers additionalContext", "[Overwatch Manual Trigger]" in context, context)
    test("Claude manual trigger uses shared protocol", "Review response protocol:" in context, context)
    test("Claude manual trigger does not force Codex backend", "OVERWATCH_BACKEND=codex_exec" not in context, context)


if __name__ == "__main__":
    test_non_codex_config_defaults_are_claude_api()
    test_claude_stop_still_uses_system_message_status()
    test_claude_manual_trigger_uses_shared_additional_context_protocol()
    print("claude hook compatibility tests passed")
