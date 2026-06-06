#!/usr/bin/env python3
"""Compatibility checks for the Claude Code hook path."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"


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


def run_hook(script: str, payload: dict[str, str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    merged_env = without_codex_env()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        ["bash", str(ROOT / "hooks" / script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=merged_env,
    )
    return json.loads(proc.stdout)


def read_optional(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def restore_optional(path: Path, content: str | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(content, encoding="utf-8")


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


def test_claude_stop_does_not_dispatch_when_pending_review_exists() -> None:
    text = (ROOT / "hooks" / "claude_code_stop.sh").read_text(encoding="utf-8")
    pending_start = text.index('if [ -f "$PENDING_FILE" ]; then')
    lock_start = text.index('if [ -f "$LOCK_FILE" ]; then', pending_start)
    pending_branch = text[pending_start:lock_start]

    test("Claude stop exits after fresh pending review", "exit 0" in pending_branch, pending_branch)


def claude_user_event(text: str) -> str:
    return json.dumps({"type": "user", "message": {"content": text}}, ensure_ascii=False)


def test_claude_stop_uses_shared_smart_trigger_for_review_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "claude-smart.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    claude_user_event("normal 1"),
                    claude_user_event("normal 2"),
                    claude_user_event("normal 3"),
                    claude_user_event("normal 4"),
                    claude_user_event("normal 5"),
                    claude_user_event("请完整检查一下这轮修改"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        response = run_hook(
            "claude_code_stop.sh",
            {
                "session_id": "claude-compat-smart-trigger-" + uuid.uuid4().hex,
                "transcript_path": str(transcript),
                "cwd": tmp,
            },
            env={"OVERWATCH_TEST_DISABLE_DISPATCH": "1"},
        )

    message = str(response.get("systemMessage", ""))
    test("Claude smart trigger dispatches review request", "Review triggered" in message, message)


def test_claude_stop_waits_without_shared_smart_signal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "claude-wait.jsonl"
        transcript.write_text(
            "\n".join([claude_user_event(f"normal {i}") for i in range(1, 7)]) + "\n",
            encoding="utf-8",
        )
        response = run_hook(
            "claude_code_stop.sh",
            {
                "session_id": "claude-compat-no-smart-signal-" + uuid.uuid4().hex,
                "transcript_path": str(transcript),
                "cwd": tmp,
            },
            env={"OVERWATCH_TEST_DISABLE_DISPATCH": "1"},
        )

    message = str(response.get("systemMessage", ""))
    test("Claude waits without smart signal", "turns until next" in message, message)
    test("Claude no-signal path does not trigger", "Review triggered" not in message, message)


def test_claude_manual_trigger_uses_shared_additional_context_protocol() -> None:
    trigger_file = STATE_DIR / "latest_trigger.json"
    original_trigger = read_optional(trigger_file)
    sid = "claude-compat-manual-" + uuid.uuid4().hex

    try:
        response = run_hook(
            "claude_code_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/claude-compat.jsonl",
                "cwd": "/tmp/claude-project",
                "user_prompt": "overwatch",
            },
        )
    finally:
        restore_optional(trigger_file, original_trigger)
    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))

    test("Claude manual trigger delivers additionalContext", "[Overwatch Manual Trigger]" in context, context)
    test("Claude manual trigger uses shared protocol", "Review response protocol:" in context, context)
    test("Claude manual trigger does not force Codex backend", "OVERWATCH_BACKEND=codex_exec" not in context, context)
    test("Claude manual trigger test restores latest trigger", read_optional(trigger_file) == original_trigger)


def test_claude_prompt_delivers_fresh_pending_review() -> None:
    sid = "claude-compat-fresh-pending-" + uuid.uuid4().hex
    trigger_file = STATE_DIR / "latest_trigger.json"
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    review_file = STATE_DIR / f"{sid}-review.md"
    original_trigger = read_optional(trigger_file)
    original_pending = read_optional(pending_file)

    review_file.write_text("CLAUDE FRESH AUTO REVIEW BODY", encoding="utf-8")
    pending_file.write_text(
        json.dumps(
            {
                "review_path": str(review_file),
                "session_id": sid,
                "created_at": 4_102_444_800,
                "ttl_hours": 72,
            }
        ),
        encoding="utf-8",
    )

    try:
        response = run_hook(
            "claude_code_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/claude-compat.jsonl",
                "cwd": "/tmp/claude-project",
                "user_prompt": "normal message",
            },
        )
    finally:
        restore_optional(trigger_file, original_trigger)
        restore_optional(pending_file, original_pending)
        review_file.unlink(missing_ok=True)

    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
    test("Claude prompt consumes fresh pending marker", not pending_file.exists(), str(response))
    test("Claude prompt marks auto-review delivery", "[Overwatch Auto-Review]" in context, context)
    test("Claude prompt delivers fresh review body", "CLAUDE FRESH AUTO REVIEW BODY" in context, context)


if __name__ == "__main__":
    test_non_codex_config_defaults_are_claude_api()
    test_claude_stop_still_uses_system_message_status()
    test_claude_stop_does_not_dispatch_when_pending_review_exists()
    test_claude_stop_uses_shared_smart_trigger_for_review_request()
    test_claude_stop_waits_without_shared_smart_signal()
    test_claude_manual_trigger_uses_shared_additional_context_protocol()
    test_claude_prompt_delivers_fresh_pending_review()
    print("claude hook compatibility tests passed")
