#!/usr/bin/env python3
"""Regression tests for Codex hook observability and session mapping."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def run_hook(script: str, payload: dict[str, str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    merged_env = os.environ.copy()
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


def test_codex_prompt_has_no_eric_local_status_path() -> None:
    text = (ROOT / "hooks" / "codex_prompt.sh").read_text(encoding="utf-8")
    test("codex prompt uses configurable status relay", "OVERWATCH_CODEX_STATUS_RELAY_DIR" in text)
    test("codex prompt does not hardcode Eric status path", "/Users/" + "eric" not in text, "hardcoded user path found")


if __name__ == "__main__":
    test_find_session_uses_codex_thread_id()
    test_prompt_hook_updates_session_map()
    test_stop_hook_records_skip_reason_when_transcript_missing()
    test_prompt_hook_surfaces_previous_stop_says_once()
    test_prompt_hook_injects_anchor_context_when_helper_is_configured()
    test_codex_prompt_has_no_eric_local_status_path()
    print("codex hook observability tests passed")
