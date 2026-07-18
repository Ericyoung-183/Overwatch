#!/usr/bin/env python3
"""Crash, concurrency, and session-isolation regression tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import context_manager
import config_transaction
import overwatch
import trigger_state
from runtime_fs import canonical_project_root, project_identity_sha256
from session_registry import (
    SessionProjectMismatchError,
    record_session,
    session_lock_active,
    sessions_for_project,
)
from trigger_state import (
    auto_review_metadata,
    read_auto_review_bytes,
    trigger_path,
    write_trigger,
)


def test(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"  PASS {name}")


def test_session_registry_preserves_concurrent_same_project_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        project = Path(tmp) / "project"
        project.mkdir()
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from session_registry import record_session; "
            "record_session(sys.argv[2], sys.argv[3], sys.argv[4])"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(ROOT), str(state), str(project), f"session-{index}"]
            )
            for index in range(8)
        ]
        for process in processes:
            process.wait(timeout=10)
            if process.returncode:
                raise AssertionError(f"registry writer exited {process.returncode}")
        payload = json.loads((state / "session_map.json").read_text(encoding="utf-8"))
        matches = sessions_for_project(str(state), str(project))

    test("session registry keeps every concurrent session", len(payload["sessions"]) == 8, str(payload))
    test("same-project lookup represents every session", set(matches) == {f"session-{index}" for index in range(8)}, str(matches))


def test_session_registry_does_not_bind_descendant_projects() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        parent = Path(tmp) / "project"
        child = parent / "nested"
        child.mkdir(parents=True)
        record_session(str(state), str(parent), "parent-session")
        exact_parent = sessions_for_project(str(state), str(parent))
        descendant = sessions_for_project(str(state), str(child))

    test("registry returns the exact project session", exact_parent == ["parent-session"], str(exact_parent))
    test("registry refuses ancestor session binding for a descendant", descendant == [], str(descendant))


def test_session_registry_refuses_same_session_in_another_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        first = Path(tmp) / "first"
        second = Path(tmp) / "second"
        first.mkdir()
        second.mkdir()
        record_session(str(state), str(first), "project-bound-session")
        try:
            record_session(str(state), str(second), "project-bound-session")
        except SessionProjectMismatchError as exc:
            error = str(exc)
        else:
            error = ""
        still_first = sessions_for_project(str(state), str(first))
        second_empty = sessions_for_project(str(state), str(second))

    test("registry rejects same session in another project", "already bound" in error, error)
    test("registry preserves original project binding", still_first == ["project-bound-session"], str(still_first))
    test("registry does not create a second binding", second_empty == [], str(second_empty))


def test_session_registry_rejects_empty_project_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            record_session(str(Path(tmp) / "state"), "", "empty-project-session")
        except ValueError as exc:
            error = str(exc)
        else:
            error = ""

    test("registry rejects an empty project identity", "project root is required" in error, error)


def test_engine_rejects_transcript_from_another_project_before_lock() -> None:
    session_id = "project-mismatch-engine"
    first = canonical_project_root("/tmp/overwatch-project-first")
    second = canonical_project_root("/tmp/overwatch-project-second")
    with (
        mock.patch.object(overwatch, "project_is_allowed", return_value=True),
        mock.patch.object(overwatch, "get_transcript_session_ids", return_value={session_id}),
        mock.patch.object(overwatch, "get_transcript_project_cwds", return_value={first}),
        mock.patch.object(overwatch, "_acquire_lock") as acquire_lock,
    ):
        result = overwatch.run(session_id, "/unused/transcript.jsonl", True, second)

    test("engine rejects transcript project mismatch", result is None, str(result))
    test("engine project rejection happens before review lock", acquire_lock.call_count == 0, str(acquire_lock.call_args_list))


def test_config_exchange_preserves_original_after_post_commit_race() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "hooks.json"
        target.write_bytes(b"original\n")
        os.chmod(target, 0o600)
        staged = config_transaction.stage_bytes(target, b"managed\n", 0o600)
        real_rename = config_transaction._atomic_rename
        exchanges = 0

        def raced_rename(source: Path, destination: Path, *, exchange: bool) -> None:
            nonlocal exchanges
            real_rename(source, destination, exchange=exchange)
            if exchange and destination == target and exchanges == 0:
                exchanges += 1
                target.write_bytes(b"external\n")

        with mock.patch.object(config_transaction, "_atomic_rename", side_effect=raced_rename):
            try:
                config_transaction.commit_staged(
                    target,
                    staged,
                    expected_original=b"original\n",
                    expected_mode=0o600,
                )
            except config_transaction.ConfigConflictError as exc:
                error = str(exc)
            else:
                error = ""
        recoveries = list(Path(tmp).glob(".hooks.json.overwatch-recovery.*.bak"))
        recovery_bytes = recoveries[0].read_bytes() if len(recoveries) == 1 else b""
        current_bytes = target.read_bytes()

    test("post-commit config race is rejected", "original preserved" in error, error)
    test("post-commit external edit remains current", current_bytes == b"external\n", repr(current_bytes))
    test("post-commit original config is recoverable", recovery_bytes == b"original\n", repr(recovery_bytes))


def test_config_rollback_preserves_external_edit_and_original() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "settings.json"
        target.write_bytes(b"managed\n")
        os.chmod(target, 0o600)
        displaced = Path(tmp) / "displaced-original"
        displaced.write_bytes(b"original\n")
        os.chmod(displaced, 0o640)
        real_rename = config_transaction._atomic_rename
        exchanges = 0

        def raced_rename(source: Path, destination: Path, *, exchange: bool) -> None:
            nonlocal exchanges
            real_rename(source, destination, exchange=exchange)
            if exchange and destination == target and exchanges == 0:
                exchanges += 1
                target.write_bytes(b"external\n")

        with mock.patch.object(config_transaction, "_atomic_rename", side_effect=raced_rename):
            try:
                config_transaction.rollback_commit(
                    target,
                    displaced,
                    expected_current=b"managed\n",
                    expected_current_mode=0o600,
                )
            except config_transaction.ConfigConflictError as exc:
                error = str(exc)
            else:
                error = ""
        recoveries = list(Path(tmp).glob(".settings.json.overwatch-recovery.*.bak"))
        recovery_bytes = recoveries[0].read_bytes() if len(recoveries) == 1 else b""
        current_bytes = target.read_bytes()
        displaced_bytes = displaced.read_bytes()

    test("post-rollback config race is rejected", "original preserved" in error, error)
    test("post-rollback external edit remains current", current_bytes == b"external\n", repr(current_bytes))
    test("post-rollback original config is recoverable", recovery_bytes == b"original\n", repr(recovery_bytes))
    test("post-rollback managed bytes are not discarded", displaced_bytes == b"managed\n", repr(displaced_bytes))


def test_session_triggers_are_isolated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        first = write_trigger(tmp, "session-a", {"type": "manual_trigger", "cwd": "/a"})
        second = write_trigger(tmp, "session-b", {"type": "manual_trigger", "cwd": "/b"})
        first_payload = json.loads(Path(first).read_text(encoding="utf-8"))
        second_payload = json.loads(Path(second).read_text(encoding="utf-8"))

    test("session trigger paths are distinct", first != second, f"{first} {second}")
    test("first trigger cannot be overwritten by second", first_payload["session_id"] == "session-a" and second_payload["session_id"] == "session-b", str((first_payload, second_payload)))
    test("no global latest trigger is used", Path(first).name == "session-a.json" and "latest_trigger" not in first, first)


def test_auto_review_trigger_streams_only_hash_verified_bytes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        review = Path(tmp) / "review.md"
        project = str(Path(tmp) / "project")
        Path(project).mkdir()
        review.write_text(
            f"<!-- Overwatch Review #1 | now | session: session-auto | project-sha256: {project_identity_sha256(project)} | project: {project} -->\n"
            "<!-- META_END -->\n\nexact review bytes\n",
            encoding="utf-8",
        )
        original_review = review.read_bytes()
        write_trigger(
            str(state),
            "session-auto",
            {
                "type": "auto_review",
                "review_path": str(review),
                "review_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
                "project_root": project,
                "pending_path": str(state / "pending.json"),
                "marker_sha256": "1" * 64,
            },
        )
        verified = read_auto_review_bytes(str(state), "session-auto", project)
        metadata = auto_review_metadata(str(state), "session-auto", project)
        cli = subprocess.run(
            [
                sys.executable,
                str(ROOT / "trigger_state.py"),
                "read-auto-review",
                "--state-dir",
                str(state),
                "--session-id",
                "session-auto",
                "--project-root",
                project,
            ],
            capture_output=True,
            check=False,
        )
        review.write_bytes(b"replaced review bytes\n")
        try:
            read_auto_review_bytes(str(state), "session-auto", project)
        except ValueError as exc:
            mismatch = str(exc)
        else:
            mismatch = ""
        try:
            read_auto_review_bytes(str(state), "session-auto", str(Path(tmp) / "other"))
        except ValueError as exc:
            project_mismatch = str(exc)
        else:
            project_mismatch = ""

    test("auto trigger reader preserves exact verified bytes", verified == original_review)
    test("auto trigger CLI streams the same verified bytes", cli.returncode == 0 and cli.stdout == verified, cli.stderr.decode())
    test("auto trigger reader rejects replaced review", "hash mismatch" in mismatch, mismatch)
    test("auto trigger metadata exposes exact acknowledgement inputs", metadata.get("pending_path") == str(state / "pending.json") and metadata.get("trigger_path") == trigger_path(str(state), "session-auto"), str(metadata))
    test("auto trigger reader rejects another project", "project does not match" in project_mismatch, project_mismatch)


def test_find_review_refuses_ambiguous_project_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        reviews = root / "reviews"
        project = root / "project"
        project.mkdir()
        for index in (1, 2):
            sid = f"ambiguous-{index}"
            record_session(str(state), str(project), sid, now=float(index))
            latest = reviews / sid / "latest.md"
            latest.parent.mkdir(parents=True)
            latest.write_text(
                f"<!-- Overwatch Review #1 | now | session: {sid} | project-sha256: {project_identity_sha256(project)} | project: {project} -->\n"
                "<!-- META_END -->\n",
                encoding="utf-8",
            )
        result = subprocess.run(
            ["bash", str(ROOT / "hooks" / "find_review.sh"), str(project)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "OVERWATCH_STATE_DIR": str(state),
                "OVERWATCH_REVIEWS_DIR": str(reviews),
            },
        )

    test("ambiguous project review lookup fails closed", result.returncode != 0 and not result.stdout, result.stderr)


def test_find_session_refuses_ambiguous_project_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        project = root / "project"
        project.mkdir()
        record_session(str(state), str(project), "find-session-a", now=1)
        record_session(str(state), str(project), "find-session-b", now=2)
        env = {**os.environ, "OVERWATCH_STATE_DIR": str(state)}
        env.pop("CODEX_THREAD_ID", None)
        result = subprocess.run(
            ["bash", str(ROOT / "hooks" / "find_session.sh"), str(project)],
            capture_output=True,
            text=True,
            env=env,
        )

    test("ambiguous session discovery fails closed", result.returncode != 0 and not result.stdout, result.stderr)


def test_stable_lock_distinguishes_active_from_residual_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        sid = "lock-session"
        lock_path = state / f"{sid}.lock"
        ready = state / "ready"
        code = """
import fcntl, pathlib, sys, time
lock = open(sys.argv[1], 'a+')
fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
pathlib.Path(sys.argv[2]).write_text('ready')
time.sleep(1.0)
"""
        holder = subprocess.Popen([sys.executable, "-c", code, str(lock_path), str(ready)])
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.01)
        active = session_lock_active(str(state), sid)
        holder.wait(timeout=5)
        residual = lock_path.exists()
        inactive = not session_lock_active(str(state), sid)

    test("held session lock is active", active)
    test("stable residual lock file remains", residual)
    test("unheld residual lock file does not suppress reviews", inactive)


def test_state_save_is_atomic_on_replace_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_state_dir = context_manager.STATE_DIR
        context_manager.STATE_DIR = tmp
        try:
            context_manager.save_state("atomic-session", {"value": "old"})
            with mock.patch.object(context_manager.os, "replace", side_effect=OSError("injected")):
                try:
                    context_manager.save_state("atomic-session", {"value": "new"})
                except OSError:
                    pass
            payload = json.loads((Path(tmp) / "atomic-session.json").read_text(encoding="utf-8"))
            leftovers = list(Path(tmp).glob("*.tmp"))
        finally:
            context_manager.STATE_DIR = old_state_dir

    test("failed state replace preserves previous JSON", payload == {"value": "old"}, str(payload))
    test("failed state replace removes temp file", leftovers == [], str(leftovers))


def test_atomic_state_and_trigger_writers_fsync_parent_directories() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        old_state = context_manager.STATE_DIR
        state_syncs = []
        trigger_syncs = []
        context_manager.STATE_DIR = str(state)
        try:
            with mock.patch.object(
                context_manager,
                "fsync_directory",
                side_effect=lambda path: state_syncs.append(Path(path)),
            ):
                context_manager.save_state("fsync-session", {"session_id": "fsync-session"})
            with mock.patch.object(
                trigger_state,
                "fsync_directory",
                side_effect=lambda path: trigger_syncs.append(Path(path)),
            ):
                trigger_state.write_trigger(
                    str(state),
                    "fsync-session",
                    {"type": "manual_trigger", "cwd": str(Path(tmp) / "project")},
                )
            state_mode = state.stat().st_mode & 0o777
        finally:
            context_manager.STATE_DIR = old_state

    test("state replacement fsyncs its parent directory", state_syncs == [state], str(state_syncs))
    test("trigger replacement fsyncs its parent directory", trigger_syncs == [state / "triggers"], str(trigger_syncs))
    test("runtime state directory is private", state_mode == 0o700, oct(state_mode))


def test_deterministic_summary_rolls_forward_to_recent_evidence() -> None:
    old_limit = context_manager.MAX_SUMMARY_CHARS
    context_manager.MAX_SUMMARY_CHARS = 180
    try:
        summary = "EARLY-EVIDENCE " + ("a" * 100)
        for index in range(8):
            summary = context_manager._truncate_summary(
                summary,
                f"MIDDLE-{index} " + (str(index) * 55),
            )
        summary = context_manager._truncate_summary(
            summary,
            "LATEST-REQUIRED-EVIDENCE " + ("z" * 80),
        )
    finally:
        context_manager.MAX_SUMMARY_CHARS = old_limit

    test("deterministic summary stays within its cap", len(summary) <= 180, summary)
    test("deterministic summary preserves newest old evidence", "LATEST-REQUIRED-EVIDENCE" in summary, summary)
    test("deterministic summary discloses dropped earlier context", "earlier deterministic context dropped" in summary, summary)
    test("deterministic summary no longer freezes on earliest evidence", "EARLY-EVIDENCE" not in summary, summary)


def test_api_summary_pretruncate_preserves_newest_old_evidence() -> None:
    old_limit = context_manager.MAX_SUMMARY_INPUT_CHARS
    captured: dict[str, str] = {}

    def fake_summary(_system, user_message, **_kwargs):
        captured["user_message"] = user_message
        return "summary"

    context_manager.MAX_SUMMARY_INPUT_CHARS = 1800
    try:
        with (
            mock.patch("config.REVIEW_BACKEND", "api"),
            mock.patch("api_client.call_claude", side_effect=fake_summary),
        ):
            result = context_manager._call_summary_model(
                "existing summary",
                "EARLIEST-OLD-EVIDENCE " + ("x" * 3000) + " LATEST-OLD-EVIDENCE",
            )
    finally:
        context_manager.MAX_SUMMARY_INPUT_CHARS = old_limit

    model_input = captured.get("user_message", "")
    test("API summary pretruncate still calls the summary model", result == "summary", result)
    test("API summary pretruncate preserves newest old evidence", "LATEST-OLD-EVIDENCE" in model_input, model_input[-300:])
    test("API summary pretruncate drops oldest overflow", "EARLIEST-OLD-EVIDENCE" not in model_input, model_input[:300])
    test("API summary pretruncate discloses the omitted prefix", "earlier summary input dropped" in model_input, model_input[:300])


def test_review_history_refuses_overwrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reviews = Path(tmp) / "reviews"
        old_reviews = overwatch.REVIEWS_DIR
        old_current = overwatch.CURRENT_REVIEW_LINK
        overwatch.REVIEWS_DIR = str(reviews)
        overwatch.CURRENT_REVIEW_LINK = str(reviews / "_current.md")
        try:
            archive = Path(overwatch.write_review("history-session", "first", 1, "/tmp/project"))
            original = archive.read_bytes()
            latest = reviews / "history-session" / "latest.md"
            directory_modes = {
                path: path.stat().st_mode & 0o777
                for path in [
                    reviews,
                    reviews / "history-session",
                    reviews / "history-session" / "history",
                ]
            }
            file_modes = {
                archive: archive.stat().st_mode & 0o777,
                latest: latest.stat().st_mode & 0o777,
            }
            try:
                overwatch.write_review("history-session", "replacement", 1, "/tmp/project")
                refused = False
            except FileExistsError:
                refused = True
            unchanged = archive.read_bytes() == original
        finally:
            overwatch.REVIEWS_DIR = old_reviews
            overwatch.CURRENT_REVIEW_LINK = old_current

    test("review history refuses an existing sequence number", refused)
    test("review history bytes remain immutable", unchanged)
    test("review directories are private", set(directory_modes.values()) == {0o700}, str(directory_modes))
    test("review files are private", set(file_modes.values()) == {0o600}, str(file_modes))


def test_review_history_rejects_symlinked_directories() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        reviews = base / "reviews"
        outside = base / "outside"
        reviews.mkdir()
        outside.mkdir()
        old_reviews = overwatch.REVIEWS_DIR
        old_current = overwatch.CURRENT_REVIEW_LINK
        overwatch.REVIEWS_DIR = str(reviews)
        overwatch.CURRENT_REVIEW_LINK = str(reviews / "_current.md")
        try:
            (reviews / "linked-session").symlink_to(outside, target_is_directory=True)
            try:
                overwatch.write_review("linked-session", "must not escape", 1, "/tmp/project")
                session_refused = False
            except OSError:
                session_refused = True

            real_session = reviews / "real-session"
            real_session.mkdir()
            (real_session / "history").symlink_to(outside, target_is_directory=True)
            try:
                overwatch.write_review("real-session", "must not escape", 1, "/tmp/project")
                history_refused = False
            except OSError:
                history_refused = True
        finally:
            overwatch.REVIEWS_DIR = old_reviews
            overwatch.CURRENT_REVIEW_LINK = old_current

        outside_files = list(outside.rglob("*"))

    test("review writer rejects a symlinked session directory", session_refused)
    test("review writer rejects a symlinked history directory", history_refused)
    test("symlink rejection leaves the external directory untouched", not outside_files, str(outside_files))


def test_failed_review_attempt_preserves_retry_cursor() -> None:
    state = {"last_reviewed_turn": 2, "review_count": 1}
    turns = [SimpleNamespace(role="user") for _ in range(3)]
    updated_state = {**state, "last_reviewed_turn": 3, "review_count": 2}
    saved: list[dict] = []

    with (
        mock.patch.object(overwatch, "REVIEW_BACKEND", "codex_exec"),
        mock.patch.object(overwatch, "load_state", return_value=state),
        mock.patch.object(overwatch, "save_state", side_effect=lambda _sid, payload: saved.append(dict(payload))),
        mock.patch.object(overwatch, "get_adapter", return_value=lambda _path, offset=0: turns),
        mock.patch.object(overwatch, "build_review_context", return_value=("context", updated_state)),
        mock.patch.object(overwatch, "build_review_prompt", return_value=("system", "user")),
        mock.patch.object(overwatch, "_read_last_review", return_value=""),
        mock.patch.object(overwatch, "_get_git_context", return_value=""),
        mock.patch.object(overwatch, "_read_user_context", return_value=""),
        mock.patch("codex_exec_client.call_codex_exec", return_value="[Overwatch Error] backend unavailable"),
    ):
        overwatch._run_inner(
            "retry-session", "/tmp/transcript.jsonl", False, "/tmp/project"
        )

    test("failed attempt writes attempt and failure states", len(saved) == 2, str(saved))
    test("attempt state keeps previous reviewed cursor", saved[0].get("last_reviewed_turn") == 2, str(saved[0]))
    test("failure state remains retryable without a new turn", saved[-1].get("last_reviewed_turn") == 2, str(saved[-1]))
    test("failed attempt does not persist success cursor", all(item.get("last_reviewed_turn") != 3 for item in saved), str(saved))


def test_pending_review_delivery_intent_recovers_without_backend_replay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reviews = Path(tmp) / "reviews"
        old_reviews = overwatch.REVIEWS_DIR
        old_current = overwatch.CURRENT_REVIEW_LINK
        overwatch.REVIEWS_DIR = str(reviews)
        overwatch.CURRENT_REVIEW_LINK = str(reviews / "_current.md")
        prepared = overwatch.prepare_review_document(
            "delivery-recovery",
            "recoverable review body with enough detail for delivery recovery",
            3,
            "/tmp/project",
            timestamp="2026-07-18 10:00:00",
        )
        review = Path(str(prepared["review_path"]))
        success_state = {
            "session_id": "delivery-recovery",
            "project_root": canonical_project_root("/tmp/project"),
            "last_reviewed_turn": 9,
            "review_count": 3,
            "last_review_status": "success",
        }
        intent_state = {
            "session_id": "delivery-recovery",
            "project_root": canonical_project_root("/tmp/project"),
            "last_reviewed_turn": 4,
            "review_count": 3,
            "pending_review_delivery": {
                **prepared,
                "review_number": 3,
                "session_id": "delivery-recovery",
                "project_cwd": "/tmp/project",
                "project_root": canonical_project_root("/tmp/project"),
                "delivery_mode": "auto",
                "success_state": success_state,
            },
        }
        saved: list[dict] = []
        old_state_dir = overwatch.STATE_DIR
        overwatch.STATE_DIR = tmp
        try:
            with (
                mock.patch.object(overwatch, "REVIEW_BACKEND", "api"),
                mock.patch("config.API_AUTH_TOKEN", ""),
                mock.patch.object(overwatch, "load_state", return_value=intent_state),
                mock.patch.object(
                    overwatch,
                    "save_state",
                    side_effect=lambda _sid, payload: saved.append(dict(payload)),
                ),
                mock.patch.object(overwatch, "_write_pending_marker") as marker_write,
                mock.patch.object(overwatch, "get_adapter") as adapter_lookup,
            ):
                recovered = overwatch._run_inner(
                    "delivery-recovery", "/unused/transcript.jsonl", False, "/tmp/project"
                )
            review_exists = review.is_file()
            review_hash_matches = (
                hashlib.sha256(review.read_bytes()).hexdigest()
                == prepared["review_sha256"]
            )
        finally:
            overwatch.STATE_DIR = old_state_dir
            overwatch.REVIEWS_DIR = old_reviews
            overwatch.CURRENT_REVIEW_LINK = old_current

    test("delivery intent returns the existing review", recovered == str(review), str(recovered))
    test("delivery intent reconstructs the missing immutable archive", review_exists, str(review))
    test("reconstructed archive has the prepared hash", review_hash_matches, str(prepared))
    test("delivery intent recreates a missing marker", marker_write.call_count == 1, str(marker_write.call_args_list))
    test("delivery intent finalizes the saved success cursor", saved == [success_state], str(saved))
    test("delivery recovery does not parse or call the backend", adapter_lookup.call_count == 0, str(adapter_lookup.call_args_list))
    test("delivery recovery does not require backend credentials", recovered == str(review), str(recovered))


def test_manual_result_crash_recovers_without_backend_replay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reviews = root / "reviews"
        result_file = root / "manual_review_result.test.json"
        old_reviews = overwatch.REVIEWS_DIR
        old_current = overwatch.CURRENT_REVIEW_LINK
        overwatch.REVIEWS_DIR = str(reviews)
        overwatch.CURRENT_REVIEW_LINK = str(reviews / "_current.md")
        prepared = overwatch.prepare_review_document(
            "manual-recovery",
            "manual recoverable review body with exact session identity",
            4,
            "/tmp/project",
            timestamp="2026-07-18 10:00:00",
        )
        review = overwatch.publish_review_document(
            "manual-recovery",
            4,
            "/tmp/project",
            str(prepared["review_document"]),
            str(prepared["review_sha256"]),
        )
        success_state = {
            "session_id": "manual-recovery",
            "project_root": canonical_project_root("/tmp/project"),
            "last_reviewed_turn": 12,
            "review_count": 4,
            "last_review_status": "success",
        }
        intent_state = {
            "session_id": "manual-recovery",
            "project_root": canonical_project_root("/tmp/project"),
            "last_reviewed_turn": 7,
            "review_count": 4,
            "pending_review_delivery": {
                **prepared,
                "review_number": 4,
                "session_id": "manual-recovery",
                "project_cwd": "/tmp/project",
                "project_root": canonical_project_root("/tmp/project"),
                "delivery_mode": "manual",
                "manual_result_path": str(result_file),
                "success_state": success_state,
            },
        }
        try:
            with (
                mock.patch.object(overwatch, "load_state", return_value=intent_state),
                mock.patch.object(overwatch, "save_state", side_effect=OSError("fault after manual result replace")),
            ):
                try:
                    overwatch.write_manual_result(
                        str(result_file), "manual-recovery", review
                    )
                except OSError as exc:
                    injected = str(exc)
                else:
                    injected = ""

            saved: list[dict] = []
            with (
                mock.patch.object(overwatch, "REVIEW_BACKEND", "codex_exec"),
                mock.patch.object(overwatch, "load_state", return_value=intent_state),
                mock.patch.object(overwatch, "save_state", side_effect=lambda _sid, payload: saved.append(dict(payload))),
                mock.patch.object(overwatch, "get_adapter") as adapter_lookup,
            ):
                recovered = overwatch._run_inner(
                    "manual-recovery",
                    "/unused/transcript.jsonl",
                    True,
                    "/tmp/project",
                    str(result_file),
                )
            result_exists = result_file.is_file()
        finally:
            overwatch.REVIEWS_DIR = old_reviews
            overwatch.CURRENT_REVIEW_LINK = old_current

    test("manual fault occurs after durable result identity", "fault after manual result replace" in injected, injected)
    test("manual result identity survives the state fault", result_exists, str(result_file))
    test("manual recovery returns the same immutable review", recovered == review, str(recovered))
    test("manual recovery finalizes the success cursor", saved == [success_state], str(saved))
    test("manual recovery does not parse or replay the backend", adapter_lookup.call_count == 0, str(adapter_lookup.call_args_list))


if __name__ == "__main__":
    test_session_registry_preserves_concurrent_same_project_sessions()
    test_session_registry_does_not_bind_descendant_projects()
    test_session_registry_refuses_same_session_in_another_project()
    test_session_registry_rejects_empty_project_identity()
    test_engine_rejects_transcript_from_another_project_before_lock()
    test_config_exchange_preserves_original_after_post_commit_race()
    test_config_rollback_preserves_external_edit_and_original()
    test_session_triggers_are_isolated()
    test_auto_review_trigger_streams_only_hash_verified_bytes()
    test_find_review_refuses_ambiguous_project_session()
    test_find_session_refuses_ambiguous_project_session()
    test_stable_lock_distinguishes_active_from_residual_file()
    test_state_save_is_atomic_on_replace_failure()
    test_atomic_state_and_trigger_writers_fsync_parent_directories()
    test_deterministic_summary_rolls_forward_to_recent_evidence()
    test_api_summary_pretruncate_preserves_newest_old_evidence()
    test_review_history_refuses_overwrite()
    test_review_history_rejects_symlinked_directories()
    test_failed_review_attempt_preserves_retry_cursor()
    test_pending_review_delivery_intent_recovers_without_backend_replay()
    test_manual_result_crash_recovers_without_backend_replay()
    print("runtime integrity tests passed")
