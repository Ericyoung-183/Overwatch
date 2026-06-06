#!/usr/bin/env python3
"""Regression tests for Codex hook observability and session mapping."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import atexit
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="overwatch-hook-tests-"))
STATE_DIR = TEST_ROOT / "state"
LOG_FILE = TEST_ROOT / "overwatch.log"
atexit.register(lambda: shutil.rmtree(TEST_ROOT, ignore_errors=True))


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def run_hook(script: str, payload: dict[str, str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    merged_env = os.environ.copy()
    merged_env.update({
        "OVERWATCH_STATE_DIR": str(STATE_DIR),
        "OVERWATCH_LOG_FILE": str(LOG_FILE),
    })
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


def test_find_session_uses_codex_thread_id() -> None:
    sid = "codex-observability-find-session"
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / ".codex" / "sessions" / "2026" / "05" / "08" / f"rollout-test-{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}", encoding="utf-8")

        env = os.environ.copy()
        env["HOME"] = tmp
        env["CODEX_THREAD_ID"] = sid
        proc = subprocess.run(
            ["bash", str(ROOT / "hooks" / "find_session.sh"), "/tmp/any-project"],
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )

    test("find_session returns CODEX_THREAD_ID", proc.stdout.startswith(sid + " "), proc.stdout)
    test("find_session returns Codex transcript", "rollout-test-" + sid in proc.stdout, proc.stdout)


def test_prompt_hook_updates_session_map() -> None:
    sid = "codex-observability-prompt-map"
    cwd = "/tmp/codex-observability-project"
    map_file = STATE_DIR / "session_map.json"
    original = map_file.read_text(encoding="utf-8") if map_file.exists() else ""

    try:
        run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": cwd,
                "user_prompt": "normal message",
            },
        )
        mapping = json.loads(map_file.read_text(encoding="utf-8"))
        test("prompt hook maps project to current session", mapping.get(cwd) == sid, str(mapping.get(cwd)))
    finally:
        if original:
            map_file.write_text(original, encoding="utf-8")
        else:
            map_file.unlink(missing_ok=True)


def test_stop_hook_records_skip_reason_when_transcript_missing() -> None:
    sid = "codex-observability-no-transcript"
    status_file = STATE_DIR / f"stop_status_{sid}.json"
    status_file.unlink(missing_ok=True)

    try:
        run_hook(
            "codex_stop.sh",
            {
                "session_id": sid,
                "transcript_path": "",
                "cwd": "/tmp/codex-observability-project",
            },
        )
        status = json.loads(status_file.read_text(encoding="utf-8"))
        test("stop hook writes status file", status.get("session_id") == sid)
        test("stop hook records missing transcript", status.get("reason") == "missing_transcript", str(status))
    finally:
        status_file.unlink(missing_ok=True)


def test_stop_hook_discards_expired_pending_instead_of_skipping() -> None:
    sid = "codex-observability-expired-stop"
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    status_file = STATE_DIR / f"stop_status_{sid}.json"
    pending_file.unlink(missing_ok=True)
    status_file.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "codex.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "timestamp": "2026-06-06T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        pending_file.write_text(
            json.dumps(
                {
                    "review_path": str(Path(tmp) / "stale-review.md"),
                    "session_id": sid,
                    "created_at": 1,
                    "ttl_hours": 72,
                }
            ),
            encoding="utf-8",
        )

        run_hook(
            "codex_stop.sh",
            {
                "session_id": sid,
                "transcript_path": str(transcript),
                "cwd": "/tmp/codex-observability-project",
            },
        )
        status = json.loads(status_file.read_text(encoding="utf-8"))

    test("stop hook removes expired pending marker", not pending_file.exists(), str(status))
    test("stop hook does not skip because of expired pending", status.get("reason") != "pending_review", str(status))
    test("stop hook continues normal threshold path", status.get("reason") == "below_min_threshold", str(status))


def test_prompt_hook_surfaces_previous_stop_says_once() -> None:
    sid = "codex-observability-stop-says"
    cwd = "/tmp/codex-observability-project"
    with tempfile.TemporaryDirectory() as tmp:
        relay_dir = Path(tmp) / "status-relay"
        relay_dir.mkdir()
        stop_file = relay_dir / f"last_stop_says_{sid}.json"
        stop_file.write_text(
            json.dumps(
                {
                    "continue": True,
                    "systemMessage": "Stop Says TEST | Overwatch: active/global | DevGate: clean/no changes",
                }
            ),
            encoding="utf-8",
        )

        response = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": cwd,
                "user_prompt": "normal message",
            },
            env={"OVERWATCH_CODEX_STATUS_RELAY_DIR": str(relay_dir)},
        )
        context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
        test("prompt hook surfaces previous Stop Says", "[Stop Says Previous Turn]" in context, context)
        test("prompt hook includes Stop Says message", "Stop Says TEST" in context, context)
        test("prompt hook consumes previous Stop Says", not stop_file.exists(), "status file still exists")


def test_prompt_hook_delivers_fresh_pending_review() -> None:
    sid = "codex-observability-fresh-prompt"
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    review_file = STATE_DIR / f"{sid}-review.md"
    trigger_file = STATE_DIR / "latest_trigger.json"
    original_trigger = trigger_file.read_text(encoding="utf-8") if trigger_file.exists() else ""
    pending_file.unlink(missing_ok=True)
    review_file.write_text("FRESH AUTO REVIEW BODY", encoding="utf-8")
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
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": "/tmp/codex-observability-project",
                "user_prompt": "normal message",
            },
        )
    finally:
        review_file.unlink(missing_ok=True)
        if original_trigger:
            trigger_file.write_text(original_trigger, encoding="utf-8")
        else:
            trigger_file.unlink(missing_ok=True)

    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
    test("prompt hook consumes fresh pending marker", not pending_file.exists(), str(response))
    test("prompt hook marks auto-review delivery", "[Overwatch Auto-Review]" in context, context)
    test("prompt hook delivers fresh review body", "FRESH AUTO REVIEW BODY" in context, context)


def test_prompt_hook_discards_expired_pending_before_manual_trigger() -> None:
    sid = "codex-observability-expired-prompt"
    pending_file = STATE_DIR / f"auto_review_pending_{sid}.json"
    review_file = STATE_DIR / f"{sid}-stale-review.md"
    trigger_file = STATE_DIR / "latest_trigger.json"
    original_trigger = trigger_file.read_text(encoding="utf-8") if trigger_file.exists() else ""
    pending_file.unlink(missing_ok=True)
    review_file.write_text("STALE AUTO REVIEW BODY", encoding="utf-8")
    pending_file.write_text(
        json.dumps(
            {
                "review_path": str(review_file),
                "session_id": sid,
                "created_at": 1,
                "ttl_hours": 72,
            }
        ),
        encoding="utf-8",
    )

    try:
        response = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": "/tmp/codex-observability-project",
                "user_prompt": "overwatch",
            },
        )
    finally:
        review_file.unlink(missing_ok=True)
        if original_trigger:
            trigger_file.write_text(original_trigger, encoding="utf-8")
        else:
            trigger_file.unlink(missing_ok=True)

    context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
    test("prompt hook removes expired pending marker", not pending_file.exists(), context)
    test("prompt hook does not deliver stale auto-review", "STALE AUTO REVIEW BODY" not in context, context)
    test("prompt hook continues to manual trigger", "[Overwatch Manual Trigger]" in context, context)


def test_prompt_hook_injects_anchor_context_when_helper_is_configured() -> None:
    sid = "codex-observability-anchor"
    helper_source = os.environ.get("ANCHOR_TEST_HELPER", "")
    if not helper_source or not Path(helper_source).exists():
        print("  SKIP prompt hook injects Anchor context -- ANCHOR_TEST_HELPER not set")
        return

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        helper = home / ".codex" / "skills" / "anchor" / "scripts" / "anchor.py"
        helper.parent.mkdir(parents=True)
        shutil.copy2(helper_source, helper)
        project = Path(tmp) / "project"
        global_state = Path(tmp) / "global-state"
        project.mkdir()
        subprocess.run(["python3", str(helper), "init-project", str(project), "--name", "Hook Demo"], check=True)
        subprocess.run(
            [
                "python3",
                str(helper),
                "init",
                "--cwd",
                str(project),
                "--thread-id",
                sid,
                "--title",
                "Root agenda",
                "--item",
                "A",
                "--item",
                "B",
                "--global-state-root",
                str(global_state),
            ],
            check=True,
        )

        response = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": str(project),
                "user_prompt": "normal message",
            },
            env={
                "HOME": str(home),
                "ANCHOR_GLOBAL_STATE_ROOT": str(global_state),
            },
        )

        context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
        test("prompt hook injects Anchor marker", "[Anchor]" in context, context)
        test("prompt hook injects Anchor current path", "Current: A" in context, context)
        test("prompt hook injects Anchor state source", "State source: project-local" in context, context)

        disabled = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": str(project),
                "user_prompt": "normal message",
            },
            env={
                "HOME": str(home),
                "ANCHOR_GLOBAL_STATE_ROOT": str(global_state),
                "ANCHOR_DISABLE": "1",
            },
        )
        test("prompt hook respects ANCHOR_DISABLE", "hookSpecificOutput" not in disabled, str(disabled))

        capped = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": str(project),
                "user_prompt": "normal message",
            },
            env={
                "HOME": str(home),
                "ANCHOR_GLOBAL_STATE_ROOT": str(global_state),
                "ANCHOR_MAX_CONTEXT_CHARS": "120",
            },
        )
        capped_context = str(capped.get("hookSpecificOutput", {}).get("additionalContext", ""))
        test("prompt hook caps Anchor context", len(capped_context) <= 170, capped_context)

        other_project = Path(tmp) / "other-project"
        other_project.mkdir()
        unrelated = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": str(other_project),
                "user_prompt": "normal message",
            },
            env={
                "HOME": str(home),
                "ANCHOR_GLOBAL_STATE_ROOT": str(global_state),
            },
        )
        test("prompt hook ignores unrelated Anchor state", "hookSpecificOutput" not in unrelated, str(unrelated))


def test_prompt_hook_injects_todo_bridge_reminder_when_prompt_mentions_todo() -> None:
    sid = "codex-observability-todo-bridge"
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        helper = home / ".codex" / "skills" / "anchor" / "scripts" / "anchor.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("raise SystemExit(2)\n", encoding="utf-8")
        project = Path(tmp) / "project"
        project.mkdir()

        response = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid,
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": str(project),
                "user_prompt": "我们看看还有哪些 TODO 没做",
            },
            env={"HOME": str(home)},
        )

        context = str(response.get("hookSpecificOutput", {}).get("additionalContext", ""))
        test("prompt hook injects Todo Bridge marker", "[Anchor Todo Bridge]" in context, context)
        test("prompt hook tells session to run todo-status", "todo-status" in context, context)
        test("prompt hook tells session to use todo-start", "todo-start" in context, context)
        test("prompt hook does not create project anchor dir", not (project / ".anchor").exists(), str(list(project.iterdir())))
        test("prompt hook labels Todo Bridge system message", response.get("systemMessage") == "[Anchor] Todo Bridge reminder delivered.", str(response))

        unrelated = run_hook(
            "codex_prompt.sh",
            {
                "session_id": sid + "-unrelated",
                "transcript_path": "/tmp/codex-observability-transcript.jsonl",
                "cwd": str(project),
                "user_prompt": "我们看看还有哪些风险",
            },
            env={"HOME": str(home)},
        )
        unrelated_context = str(unrelated.get("hookSpecificOutput", {}).get("additionalContext", ""))
        test("prompt hook ignores non-TODO follow-up prompts", "[Anchor Todo Bridge]" not in unrelated_context, unrelated_context)


def test_codex_prompt_has_no_eric_local_status_path() -> None:
    text = (ROOT / "hooks" / "codex_prompt.sh").read_text(encoding="utf-8")
    test("codex prompt uses configurable status relay", "OVERWATCH_CODEX_STATUS_RELAY_DIR" in text)
    test("codex prompt does not hardcode Eric status path", "/Users/" + "eric" not in text, "hardcoded user path found")


if __name__ == "__main__":
    test_find_session_uses_codex_thread_id()
    test_prompt_hook_updates_session_map()
    test_stop_hook_records_skip_reason_when_transcript_missing()
    test_stop_hook_discards_expired_pending_instead_of_skipping()
    test_prompt_hook_surfaces_previous_stop_says_once()
    test_prompt_hook_delivers_fresh_pending_review()
    test_prompt_hook_discards_expired_pending_before_manual_trigger()
    test_prompt_hook_injects_anchor_context_when_helper_is_configured()
    test_prompt_hook_injects_todo_bridge_reminder_when_prompt_mentions_todo()
    test_codex_prompt_has_no_eric_local_status_path()
    print("codex hook observability tests passed")
