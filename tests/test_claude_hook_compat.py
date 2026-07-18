#!/usr/bin/env python3
"""Compatibility checks for the Claude Code hook path."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TMP = tempfile.TemporaryDirectory(prefix="overwatch-claude-hook-tests-")
STATE_DIR = Path(RUNTIME_TMP.name) / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def write_fresh_pending(pending_file: Path, review_file: Path, session_id: str) -> None:
    pending_file.write_text(
        json.dumps(
            {
                "review_path": str(review_file.resolve()),
                "review_sha256": hashlib.sha256(review_file.read_bytes()).hexdigest(),
                "session_id": session_id,
                "created_at": 4_102_444_800,
                "ttl_hours": 72,
            }
        ),
        encoding="utf-8",
    )


def without_codex_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", None)
    env.pop("OVERWATCH_ADAPTER", None)
    env.pop("OVERWATCH_BACKEND", None)
    env.pop("OVERWATCH_REVIEW_MODEL", None)
    env["OVERWATCH_STATE_DIR"] = str(STATE_DIR)
    env["OVERWATCH_REVIEWS_DIR"] = str(Path(RUNTIME_TMP.name) / "reviews")
    env["OVERWATCH_LOG_FILE"] = str(Path(RUNTIME_TMP.name) / "overwatch.log")
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
    lock_start = text.index('from session_registry import session_lock_active', pending_start)
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
    sid = "claude-compat-manual-" + uuid.uuid4().hex
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    original_trigger = read_optional(trigger_file)

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
    test("Claude manual trigger test restores session trigger", read_optional(trigger_file) == original_trigger)


def test_claude_manual_trigger_shell_quotes_dynamic_arguments() -> None:
    sid = "claude-shell-quote"
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    original_trigger = read_optional(trigger_file)
    transcript = "/tmp/transcript'; printf injected; #.jsonl"
    cwd = "/tmp/project\"; printf injected; #"

    try:
        response = run_hook(
            "claude_code_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": transcript,
                "cwd": cwd,
                "user_prompt": "overwatch",
            },
        )
    finally:
        restore_optional(trigger_file, original_trigger)

    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
    review_command = next(line for line in context.splitlines() if line.startswith("python3 "))
    find_command = next(line for line in context.splitlines() if line.startswith("bash "))
    review_args = shlex.split(review_command, posix=True)
    find_args = shlex.split(find_command, posix=True)

    test("Claude manual review keeps session id in one shell token", review_args[3] == sid, review_command)
    test("Claude manual review keeps transcript in one shell token", review_args[5] == transcript, review_command)
    test("Claude manual review keeps cwd in one shell token", review_args[7] == cwd, review_command)
    result_path = review_args[review_args.index("--result-file") + 1]
    test("Claude manual review has unique result path", result_path.endswith(".json"), review_command)
    test("Claude exact-result lookup keeps session id in one shell token", find_args[-1] == sid, find_command)
    test("Claude exact-result lookup uses same result path", result_path in find_args, find_command)
    test("Claude manual review contains no separator token", ";" not in review_args, str(review_args))


def test_claude_prompt_delivers_fresh_pending_review() -> None:
    sid = "claude-compat-fresh-pending-" + uuid.uuid4().hex
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    review_file = STATE_DIR / f"{sid}-review.md"
    original_trigger = read_optional(trigger_file)
    original_pending = read_optional(pending_file)

    review_file.write_text(
        f"<!-- Overwatch Review #1 | 2099-01-01 00:00 | session: {sid} | project: /tmp/claude-project -->\n"
        "<!-- META_END -->\n\n"
        "CLAUDE FRESH AUTO REVIEW BODY\n"
        + ("X" * 9000)
        + "\nCLAUDE TAIL FINDING",
        encoding="utf-8",
    )
    expected_review_hash = hashlib.sha256(review_file.read_bytes()).hexdigest()
    write_fresh_pending(pending_file, review_file, sid)
    receipt_file = STATE_DIR / f"auto_review_delivered_{sid}.json"

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
        trigger_payload = json.loads(trigger_file.read_text(encoding="utf-8"))
        context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
        cleanup_line = next(
            line
            for line in context.splitlines()
            if line.startswith("python3 ") and "pending_review.py" in line
        )
        pending_before_ack = pending_file.exists()
        acknowledge_command = cleanup_line.split(" && ", 1)[0]
        acknowledge = subprocess.run(
            shlex.split(acknowledge_command),
            text=True,
            capture_output=True,
            check=False,
        )
        pending_after_ack = pending_file.exists()
    finally:
        restore_optional(trigger_file, original_trigger)
        restore_optional(pending_file, original_pending)
        review_file.unlink(missing_ok=True)
        receipt_file.unlink(missing_ok=True)

    test("Claude prompt preserves pending marker until Builder acknowledgement", pending_before_ack, str(response))
    test("Claude acknowledgement command succeeds", acknowledge.returncode == 0, acknowledge.stderr)
    test("Claude acknowledgement removes the exact pending marker", not pending_after_ack, cleanup_line)
    test("Claude prompt marks auto-review delivery", "[Overwatch Auto-Review]" in context, context)
    test("Claude prompt delivers fresh review body", "CLAUDE FRESH AUTO REVIEW BODY" in context, context)
    test("Claude prompt preserves long-review tail", "CLAUDE TAIL FINDING" in context, "tail missing")
    test("Claude prompt does not truncate long review", "... [truncated]" not in context, "review truncated")
    test(
        "Claude fallback trigger binds exact review hash",
        trigger_payload.get("review_sha256") == expected_review_hash,
        str(trigger_payload),
    )


def test_claude_hooks_surface_missing_and_invalid_pending_evidence() -> None:
    sid = "claude-compat-missing-pending-" + uuid.uuid4().hex
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    original_trigger = read_optional(trigger_file)
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    missing_review = STATE_DIR / f"{sid}-missing.md"
    pending_file.write_text(
        json.dumps(
            {
                "review_path": str(missing_review),
                "session_id": sid,
                "created_at": 4_102_444_800,
                "ttl_hours": 72,
            }
        ),
        encoding="utf-8",
    )

    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "claude.jsonl"
        transcript.write_text(claude_user_event("normal message") + "\n", encoding="utf-8")
        try:
            prompt_response = run_hook(
                "claude_code_prompt.sh",
                {
                    "session_id": sid,
                    "transcript_path": str(transcript),
                    "cwd": tmp,
                    "user_prompt": "normal message",
                },
            )
            stop_response = run_hook(
                "claude_code_stop.sh",
                {
                    "session_id": sid,
                    "transcript_path": str(transcript),
                    "cwd": tmp,
                },
            )
            missing_preserved = pending_file.exists()
            pending_file.write_text("{broken", encoding="utf-8")
            invalid_response = run_hook(
                "claude_code_prompt.sh",
                {
                    "session_id": sid,
                    "transcript_path": str(transcript),
                    "cwd": tmp,
                    "user_prompt": "normal message",
                },
            )
            invalid_preserved = pending_file.exists()
        finally:
            restore_optional(trigger_file, original_trigger)
            pending_file.unlink(missing_ok=True)

    prompt_context = str(prompt_response.get("hookSpecificOutput", {}).get("additionalContext", ""))
    test("Claude prompt surfaces missing review evidence", "Review file missing" in prompt_context, prompt_context)
    test("Claude stop surfaces missing review evidence", "Review file missing" in str(stop_response.get("systemMessage", "")), str(stop_response))
    test("Claude missing-review marker is preserved", missing_preserved)
    test("Claude prompt surfaces invalid marker evidence", "marker unreadable" in str(invalid_response), str(invalid_response))
    test("Claude invalid marker is preserved", invalid_preserved)


def test_claude_prompt_preserves_pending_when_delivery_composition_fails() -> None:
    sid = "claude-compat-compose-failure-" + uuid.uuid4().hex
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    review_file = STATE_DIR / f"{sid}-review.md"
    original_trigger = read_optional(trigger_file)
    trigger_file.parent.mkdir(parents=True, exist_ok=True)
    trigger_file.unlink(missing_ok=True)
    trigger_file.mkdir()
    review_file.write_text(
        f"<!-- Overwatch Review #1 | 2099-01-01 00:00 | session: {sid} | project: /tmp/claude-project -->\n"
        "<!-- META_END -->\n\n"
        "review body",
        encoding="utf-8",
    )
    write_fresh_pending(pending_file, review_file, sid)
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
        marker_preserved = pending_file.exists()
    finally:
        pending_file.unlink(missing_ok=True)
        review_file.unlink(missing_ok=True)
        trigger_file.rmdir()
        restore_optional(trigger_file, original_trigger)

    test("Claude compose failure preserves pending marker", marker_preserved, str(response))
    test("Claude compose failure is visible", "delivery failed" in str(response), str(response))


def test_claude_pending_marker_requires_builder_acknowledgement() -> None:
    text = (ROOT / "hooks" / "claude_code_prompt.sh").read_text(encoding="utf-8")
    cleanup_start = text.index("cleanup() {")
    cleanup_end = text.index("}\ntrap cleanup EXIT", cleanup_start)
    cleanup = text[cleanup_start:cleanup_end]

    test("Claude hook cleanup only writes the composed response", "acknowledge_pending_delivery" not in cleanup, cleanup)
    test("Claude hook does not schedule automatic pending removal", "PENDING_FILE_TO_REMOVE" not in text, text)
    test("Claude delivered context carries explicit acknowledgement", "pending_review.py" in text and " acknowledge --state-dir " in text, text)
    test("Claude acknowledgement is marker-hash bound", "--expected-marker-sha256" in text, text)


def test_claude_pending_review_and_capture_are_composed_together() -> None:
    sid = "claude-pending-capture-" + uuid.uuid4().hex
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    review_file = STATE_DIR / f"{sid}-review.md"
    trigger_file = STATE_DIR / "triggers" / f"{sid}.json"
    marker = STATE_DIR / f"anchor_capture_{sid}.json"
    helper = Path(RUNTIME_TMP.name) / "pending-capture-anchor.py"
    helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
    review_file.write_text(
        f"<!-- Overwatch Review #1 | 2099-01-01 00:00 | session: {sid} | project: /tmp/claude-project -->\n"
        "<!-- META_END -->\n\nPENDING REVIEW AND CAPTURE",
        encoding="utf-8",
    )
    write_fresh_pending(pending_file, review_file, sid)
    try:
        response = run_hook(
            "claude_code_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "",
                "cwd": "/tmp/claude-pending-capture",
                "user_prompt": "逐条处理：\n1. First issue\n2. Second issue",
            },
            env={"ANCHOR_HELPER": str(helper)},
        )
        context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
        pending_preserved = pending_file.exists()
        capture_persisted = marker.exists()
    finally:
        pending_file.unlink(missing_ok=True)
        review_file.unlink(missing_ok=True)
        trigger_file.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)

    test("Claude pending review remains in combined context", "PENDING REVIEW AND CAPTURE" in context, context)
    test("Claude same-turn capture remains in combined context", "[Anchor Capture Required]" in context, context)
    test("Claude combined delivery preserves pending until acknowledgement", pending_preserved, str(response))
    test("Claude combined delivery persists the capture candidate", capture_persisted, str(response))


def test_claude_prompt_injects_two_signal_capture_gate() -> None:
    sid = "claude-capture-gate"
    marker = STATE_DIR / f"anchor_capture_{sid}.json"
    helper = Path(RUNTIME_TMP.name) / "capture-anchor.py"
    helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
    marker.unlink(missing_ok=True)
    response = run_hook(
        "claude_code_prompt.sh",
        {
            "session_id": sid,
            "transcript_path": "",
            "cwd": "/tmp/claude-capture-project",
            "prompt": "逐条处理：\n- First issue\n- Second issue",
        },
        env={"ANCHOR_HELPER": str(helper)},
    )
    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
    marker_created = marker.is_file()
    marker.unlink(missing_ok=True)

    test("Claude hook injects the two-signal capture gate", "[Anchor Capture Required]" in context, context)
    test("Claude hook persists the capture candidate", marker_created, str(marker))


if __name__ == "__main__":
    test_non_codex_config_defaults_are_claude_api()
    test_claude_stop_still_uses_system_message_status()
    test_claude_stop_does_not_dispatch_when_pending_review_exists()
    test_claude_stop_uses_shared_smart_trigger_for_review_request()
    test_claude_stop_waits_without_shared_smart_signal()
    test_claude_manual_trigger_uses_shared_additional_context_protocol()
    test_claude_manual_trigger_shell_quotes_dynamic_arguments()
    test_claude_prompt_delivers_fresh_pending_review()
    test_claude_hooks_surface_missing_and_invalid_pending_evidence()
    test_claude_prompt_preserves_pending_when_delivery_composition_fails()
    test_claude_pending_marker_requires_builder_acknowledgement()
    test_claude_prompt_injects_two_signal_capture_gate()
    test_claude_pending_review_and_capture_are_composed_together()
    print("claude hook compatibility tests passed")
