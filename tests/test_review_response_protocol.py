#!/usr/bin/env python3
"""Regression tests for Overwatch review delivery instructions."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from response_protocol import (  # noqa: E402
    REVIEW_RESPONSE_PROTOCOL,
    build_auto_review_context,
    build_manual_trigger_context,
)
from prompts import OVERWATCH_SYSTEM_PROMPT, build_review_prompt  # noqa: E402
import overwatch  # noqa: E402
from runtime_fs import canonical_project_root, project_identity_sha256  # noqa: E402


REQUIRED_PROTOCOL_PHRASES = [
    "Present the full review text verbatim",
    "no rewriting, omitting, or merging",
    "separator line",
    "Honor an explicit read-only",
    "this exception overrides the fix-now and persistence defaults",
    "For each Issue and Recommendation",
    "Fix now is the default",
    "Only use TODO/backlog when",
    "explicitly blocked",
    "requires user decision",
    "outside the current task boundary",
    "deferral record, not closure",
    "trigger/checkpoint",
    "future review can verify",
    "Persistence is mandatory unless",
    "canonical TODO/backlog",
    "If no project backlog exists",
    "cite the exact file path",
    "Do not treat every Recommendation as memory by default",
    "fix now",
    "persist a TODO/backlog entry",
    "state why it is deferred",
    "[LESSON]",
    "save it to project memory",
    "durable operating rule",
    "Clean up the trigger file",
]


REQUIRED_REVIEW_PROMPT_PHRASES = [
    "Deferred Recommendation escalation",
    "previously converted to TODO/backlog",
    "still has not been executed",
    "upgrade it to an Issue",
    "Do not keep repeating it as a Recommendation",
    "TODO/backlog entry is not closure",
    "Active artifact pollution",
    "non-operational context",
    "rationale, audit/debug notes",
    "discarded approaches",
    "runtime code/config/tests",
    "do not change future behavior, checks, or decisions",
    "Active artifacts should contain minimal forward behavior",
    "decision logic",
    "explicitly gated compatibility",
    "move background to docs, history, or audit records",
    "Anchor agenda drift",
    "active Anchor agenda",
    "without reading the tracker",
    "re-searches TODOs",
    "jumps back to the parent agenda",
    "fails to update Anchor state",
]


@contextmanager
def temporary_env(updates: dict[str, str], remove: list[str] | None = None):
    remove = remove or []
    keys = set(updates) | set(remove)
    old = {key: os.environ.get(key) for key in keys}
    try:
        for key in remove:
            os.environ.pop(key, None)
        os.environ.update(updates)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def assert_protocol_present(name: str, text: str) -> None:
    missing = [phrase for phrase in REQUIRED_PROTOCOL_PHRASES if phrase not in text]
    test(name, not missing, f"missing phrases: {missing}")


def assert_review_prompt_present(name: str, text: str) -> None:
    missing = [phrase for phrase in REQUIRED_REVIEW_PROMPT_PHRASES if phrase not in text]
    test(name, not missing, f"missing phrases: {missing}")


def test_protocol_defines_closed_loop_handling() -> None:
    assert_protocol_present("shared protocol has closed-loop handling", REVIEW_RESPONSE_PROTOCOL)


def test_reviewer_prompt_escalates_unexecuted_todo_recommendations() -> None:
    assert_review_prompt_present(
        "reviewer prompt escalates repeated deferred recommendations",
        OVERWATCH_SYSTEM_PROMPT,
    )


def test_incremental_review_message_tracks_prior_deferred_items() -> None:
    system, user_message = build_review_prompt(
        "RECENT CONTEXT",
        review_number=3,
        last_review="Recommendation: persist TODO/backlog entry for X",
        include_tools=False,
    )

    test("incremental review includes previous review", "Previous Review (Review #2)" in user_message)
    test("incremental review keeps escalation rule in system prompt", "upgrade it to an Issue" in system)
    test("incremental review asks whether prior issues were resolved", "Were the above issues resolved?" in user_message)


def test_anchor_drift_prompt_can_be_disabled_for_non_anchor_users() -> None:
    with temporary_env({"OVERWATCH_ENABLE_ANCHOR_DRIFT": "false"}):
        system, _ = build_review_prompt("RECENT CONTEXT", review_number=1, include_tools=False)

    test("generic list drift remains enabled", "Sequential list drift" in system)
    test("disabled prompt omits Anchor-specific rule", "Anchor agenda drift" not in system)
    test("disabled prompt omits Anchor category ids", "missed-root-capture" not in system)


def test_anchor_drift_auto_requires_anchor_signal() -> None:
    with tempfile.TemporaryDirectory() as home:
        with temporary_env(
            {"OVERWATCH_ENABLE_ANCHOR_DRIFT": "auto", "HOME": home},
            remove=["ANCHOR_HELPER"],
        ):
            system_without_anchor, _ = build_review_prompt("RECENT CONTEXT", review_number=1, include_tools=False)
            system_with_context, _ = build_review_prompt("[Anchor]\nCurrent: A", review_number=1, include_tools=False)
            helper = Path(home) / ".codex" / "skills" / "anchor" / "scripts" / "anchor.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("# helper marker\n", encoding="utf-8")
            system_with_helper, _ = build_review_prompt("RECENT CONTEXT", review_number=1, include_tools=False)
            system_with_capture, _ = build_review_prompt(
                "[Anchor Capture Required]\nTarget: init root agenda",
                review_number=1,
                include_tools=False,
            )

    test("auto prompt omits Anchor rule without helper or context", "Anchor agenda drift" not in system_without_anchor)
    test("auto prompt includes Anchor rule when context has Anchor", "Anchor agenda drift" in system_with_context)
    test("auto prompt includes rubric when context has Anchor", "missed-root-capture" in system_with_context)
    test("installed helper alone does not enable Anchor review noise", "Anchor agenda drift" not in system_with_helper)
    test("capture gate enables Anchor review", "Anchor agenda drift" in system_with_capture)


def test_anchor_drift_prompt_can_be_forced_on() -> None:
    with temporary_env({"OVERWATCH_ENABLE_ANCHOR_DRIFT": "true"}):
        system, _ = build_review_prompt("RECENT CONTEXT", review_number=1, include_tools=False)

    test("forced prompt includes Anchor rule", "Anchor agenda drift" in system)
    test("forced prompt includes Anchor rubric", "missed-root-capture" in system)


def test_auto_context_embeds_protocol_and_review_text() -> None:
    context = build_auto_review_context(
        "REVIEW BODY",
        cleanup_command="rm -f state/triggers/session-a.json",
    )

    test("auto context has auto-review marker", "[Overwatch Auto-Review]" in context)
    test("auto context includes review body", "REVIEW BODY" in context)
    test("auto context includes cleanup command", "rm -f state/triggers/session-a.json" in context)
    assert_protocol_present("auto context includes full protocol", context)


def test_manual_context_embeds_protocol_and_commands() -> None:
    context = build_manual_trigger_context(
        review_command="python3 overwatch.py --force",
        find_review_command="bash hooks/find_review.sh",
        cleanup_command="rm -f state/triggers/session-a.json",
    )

    test("manual context has manual trigger marker", "[Overwatch Manual Trigger]" in context)
    test("manual context includes review command", "python3 overwatch.py --force" in context)
    test("manual context includes find-review command", "bash hooks/find_review.sh" in context)
    test("manual context includes cleanup command", "rm -f state/triggers/session-a.json" in context)
    assert_protocol_present("manual context includes full protocol", context)


def test_hooks_use_shared_protocol_builders() -> None:
    for hook in [
        ROOT / "hooks" / "codex_prompt.sh",
        ROOT / "hooks" / "claude_code_prompt.sh",
    ]:
        text = hook.read_text(encoding="utf-8")
        test(f"{hook.name} imports shared protocol", "from response_protocol import" in text)
        test(f"{hook.name} avoids weak legacy instruction", "Present this review verbatim, then respond point by point" not in text)
        test(f"{hook.name} avoids weak manual instruction", "Present the full review verbatim, then respond point by point" not in text)


def test_manual_result_lookup_rejects_stale_or_wrong_session_review() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        review = root / "review.md"
        result_file = root / "result.json"
        session_id = "session-current"
        project_root = canonical_project_root("/tmp/p")
        review_text = (
            f"<!-- Overwatch Review #1 | 2026-07-18 10:00:00 | session: {session_id} | "
            f"project-sha256: {project_identity_sha256(project_root)} | project: {project_root} -->\n"
            "<!-- META_END -->\n\nreview body\n"
        )
        review.write_text(review_text, encoding="utf-8")
        review_hash = __import__("hashlib").sha256(review.read_bytes()).hexdigest()
        intent_state = {
            "project_root": project_root,
            "pending_review_delivery": {
                "delivery_mode": "manual",
                "manual_result_path": str(result_file),
                "review_path": str(review),
                "review_sha256": review_hash,
                "project_root": project_root,
                "success_state": {"project_root": project_root},
            },
        }
        with (
            mock.patch.object(overwatch, "load_state", return_value=intent_state),
            mock.patch.object(overwatch, "save_state"),
        ):
            overwatch.write_manual_result(str(result_file), session_id, str(review))
        success = subprocess.run(
            [
                "bash",
                str(ROOT / "hooks" / "find_review.sh"),
                "--result-file",
                str(result_file),
                "--session-id",
                session_id,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        wrong_session = subprocess.run(
            [
                "bash",
                str(ROOT / "hooks" / "find_review.sh"),
                "--result-file",
                str(result_file),
                "--session-id",
                "session-stale",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        review.write_text(review.read_text(encoding="utf-8") + "replaced\n", encoding="utf-8")
        replaced_review = subprocess.run(
            [
                "bash",
                str(ROOT / "hooks" / "find_review.sh"),
                "--result-file",
                str(result_file),
                "--session-id",
                session_id,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    test("manual result records exact session", payload["session_id"] == session_id, str(payload))
    test("manual result records exact review path", payload["review_path"] == str(review), str(payload))
    test("manual result records exact review hash", len(payload.get("review_sha256", "")) == 64, str(payload))
    test("exact result lookup streams current review bytes", success.returncode == 0 and success.stdout == review_text, success.stderr)
    test("exact result lookup rejects wrong session", wrong_session.returncode != 0, wrong_session.stdout)
    test("exact result lookup rejects replaced review bytes", replaced_review.returncode != 0, replaced_review.stdout)


def test_install_snippet_uses_protocol_placeholder() -> None:
    text = (ROOT / "claude_md_snippet.md").read_text(encoding="utf-8")
    test("install snippet has protocol placeholder", "{{REVIEW_RESPONSE_PROTOCOL}}" in text)
    test("install snippet uses exact manual wrapper", "hooks/run_manual_review.sh" in text)
    test("install snippet rejects stale latest fallback", "do not fall back to an older `latest.md`" in text)
    test("install snippet binds fallback review to project root", "read-auto-review" in text and "--project-root \"$PROJECT_ROOT\"" in text, text)
    test("install snippet obtains exact fallback acknowledgement metadata", "auto-review-metadata" in text and "marker_sha256" in text, text)
    test("install snippet acknowledges only after verbatim presentation", "After verbatim presentation" in text and "pending_review.py" in text, text)


def test_manual_wrapper_does_not_return_stale_review_after_identity_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_dir = root / "state"
        reviews_dir = root / "reviews"
        stale = reviews_dir / "requested-session" / "latest.md"
        transcript = root / "codex.jsonl"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale review", encoding="utf-8")
        transcript.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "different-session"}}) + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["OVERWATCH_ADAPTER"] = "codex"
        env["OVERWATCH_BACKEND"] = "codex_exec"
        env["OVERWATCH_STATE_DIR"] = str(state_dir)
        env["OVERWATCH_REVIEWS_DIR"] = str(reviews_dir)
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "hooks" / "run_manual_review.sh"),
                "--session-id",
                "requested-session",
                "--transcript",
                str(transcript),
                "--cwd",
                str(root),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    test("manual wrapper fails on transcript identity mismatch", result.returncode != 0, result.stdout)
    test("manual wrapper does not print stale review", str(stale) not in result.stdout, result.stdout)


def test_prompts_can_load_from_file_without_caller_pythonpath() -> None:
    spec = importlib.util.spec_from_file_location("standalone_prompts", ROOT / "prompts.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    test("standalone prompts import exposes review prompt", "Anchor agenda drift" in module.OVERWATCH_SYSTEM_PROMPT)


def test_find_review_uses_project_boundaries_and_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        reviews = root / "reviews"
        allowed = root / "client-a"
        sibling = root / "client-a-secret"
        allowed.mkdir()
        sibling.mkdir()
        state.mkdir()
        reviews.mkdir()
        sid = "safe-session"
        latest = reviews / sid / "latest.md"
        latest.parent.mkdir()
        latest.write_text(
            f"<!-- Overwatch Review #1 | now | session: {sid} | project-sha256: {project_identity_sha256(allowed)} | project: {allowed} -->\n",
            encoding="utf-8",
        )
        (state / "session_map.json").write_text(
            json.dumps({str(allowed): sid}),
            encoding="utf-8",
        )
        colliding_fallback = reviews / f"_current_{sibling.name}.md"
        colliding_fallback.write_text(
            f"<!-- Overwatch Review #1 | now | session: other | project-sha256: {project_identity_sha256(allowed)} | project: {allowed} -->\n",
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "OVERWATCH_STATE_DIR": str(state),
            "OVERWATCH_REVIEWS_DIR": str(reviews),
        }
        result = subprocess.run(
            ["bash", str(ROOT / "hooks" / "find_review.sh"), str(sibling)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

    test("find_review rejects sibling-prefix session map", result.stdout.strip() == "", result.stdout)


if __name__ == "__main__":
    test_protocol_defines_closed_loop_handling()
    test_reviewer_prompt_escalates_unexecuted_todo_recommendations()
    test_incremental_review_message_tracks_prior_deferred_items()
    test_anchor_drift_prompt_can_be_disabled_for_non_anchor_users()
    test_anchor_drift_auto_requires_anchor_signal()
    test_anchor_drift_prompt_can_be_forced_on()
    test_auto_context_embeds_protocol_and_review_text()
    test_manual_context_embeds_protocol_and_commands()
    test_hooks_use_shared_protocol_builders()
    test_manual_result_lookup_rejects_stale_or_wrong_session_review()
    test_install_snippet_uses_protocol_placeholder()
    test_manual_wrapper_does_not_return_stale_review_after_identity_failure()
    test_prompts_can_load_from_file_without_caller_pythonpath()
    test_find_review_uses_project_boundaries_and_metadata()
    print("review_response_protocol tests passed")
