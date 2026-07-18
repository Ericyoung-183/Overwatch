#!/usr/bin/env python3
"""Overwatch main engine: parse transcript -> manage context -> run reviewer -> write review."""
import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
import tempfile
import uuid
from datetime import datetime, timedelta

# Ensure sibling modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    REVIEW_MODEL,
    REVIEW_BACKEND,
    API_FORMAT,
    REVIEWS_DIR,
    CURRENT_REVIEW_LINK,
    ADAPTER,
    MIN_REVIEW_CHARS,
    REVIEW_FAILURE_COOLDOWN_SECONDS,
    REVIEW_MAX_COOLDOWN_SECONDS,
    INCLUDE_LEGACY_CONTEXT,
    project_is_allowed,
    valid_session_id,
    STATE_DIR,
)
from api_client import call_claude, call_claude_with_tools
from adapters import (
    get_adapter,
    get_transcript_project_cwds,
    get_transcript_session_ids,
)
from context_manager import load_state, save_state, build_review_context
from pending_review import (
    delivery_receipt_matches,
    pending_status,
    review_artifact_identity,
    review_document_identity,
    write_pending_marker,
)
from prompts import build_review_prompt
from runtime_fs import (
    canonical_project_root,
    ensure_private_directory,
    fsync_directory,
    project_identity_sha256,
)


_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
_FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _fsync_directory_fd(directory_fd: int) -> None:
    os.fsync(directory_fd)


def _open_private_directory(path: str) -> int:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    directory_fd = os.open(path, _DIRECTORY_OPEN_FLAGS)
    os.fchmod(directory_fd, 0o700)
    return directory_fd


def _open_private_child_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        _fsync_directory_fd(parent_fd)
    except FileExistsError:
        pass
    directory_fd = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    os.fchmod(directory_fd, 0o700)
    return directory_fd


def _replace_symlink_at(target: str, link_name: str, directory_fd: int) -> None:
    temporary = f".{link_name}.tmp.{os.getpid()}.{time.time_ns()}"
    try:
        os.symlink(target, temporary, dir_fd=directory_fd)
        os.replace(
            temporary,
            link_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        _fsync_directory_fd(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def prepare_review_document(
    session_id: str,
    review_text: str,
    review_number: int,
    project_cwd: str = "",
    timestamp: str = "",
) -> dict[str, object]:
    if not valid_session_id(session_id):
        raise ValueError("invalid Overwatch session ID")
    project_root = canonical_project_root(project_cwd)
    if not project_root:
        raise ValueError("project root is required")
    timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"<!-- Overwatch Review #{review_number} | {timestamp} | "
        f"session: {session_id} | project-sha256: "
        f"{project_identity_sha256(project_root)} | project: {project_root} -->\n"
        "<!-- META_END -->\n\n"
    )
    document = header + review_text
    archive_path = os.path.abspath(
        os.path.join(REVIEWS_DIR, session_id, "history", f"review_{review_number:03d}.md")
    )
    return {
        "review_path": archive_path,
        "review_document": document,
        "review_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
    }


def publish_review_document(
    session_id: str,
    review_number: int,
    project_cwd: str,
    document: str,
    expected_sha256: str,
    *,
    allow_existing: bool = False,
) -> str:
    """Publish one immutable archive and refresh convenience links."""
    if not valid_session_id(session_id):
        raise ValueError("invalid Overwatch session ID")
    session_dir = os.path.join(REVIEWS_DIR, session_id)
    history_dir = os.path.join(session_dir, "history")
    archive_path = os.path.join(history_dir, f"review_{review_number:03d}.md")
    document_bytes = document.encode("utf-8")
    if hashlib.sha256(document_bytes).hexdigest() != expected_sha256:
        raise ValueError("prepared review document hash mismatch")
    reviews_fd = _open_private_directory(REVIEWS_DIR)
    session_fd = -1
    history_fd = -1
    try:
        session_fd = _open_private_child_directory(reviews_fd, session_id)
        history_fd = _open_private_child_directory(session_fd, "history")
        archive_name = f"review_{review_number:03d}.md"
        try:
            archive_fd = os.open(
                archive_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
                0o600,
                dir_fd=history_fd,
            )
        except FileExistsError:
            existing_fd = os.open(
                archive_name,
                os.O_RDONLY | _FILE_NOFOLLOW,
                dir_fd=history_fd,
            )
            with os.fdopen(existing_fd, "rb") as existing_stream:
                existing_hash = hashlib.sha256(existing_stream.read()).hexdigest()
            if not allow_existing or existing_hash != expected_sha256:
                raise
        else:
            with os.fdopen(archive_fd, "wb") as f:
                f.write(document_bytes)
                f.flush()
                os.fsync(f.fileno())
            _fsync_directory_fd(history_fd)

        latest_path = os.path.join(session_dir, "latest.md")
        tmp_name = f".latest.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        tmp_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
            0o600,
            dir_fd=session_fd,
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(document)
                f.flush()
                os.fsync(f.fileno())
            os.replace(
                tmp_name,
                "latest.md",
                src_dir_fd=session_fd,
                dst_dir_fd=session_fd,
            )
            _fsync_directory_fd(session_fd)
        finally:
            try:
                os.unlink(tmp_name, dir_fd=session_fd)
            except FileNotFoundError:
                pass

        current_parent = os.path.abspath(os.path.dirname(CURRENT_REVIEW_LINK))
        if current_parent != os.path.abspath(REVIEWS_DIR):
            raise ValueError("current review link must stay inside the reviews directory")
        _replace_symlink_at(latest_path, os.path.basename(CURRENT_REVIEW_LINK), reviews_fd)

        if project_cwd:
            project_name = os.path.basename(project_cwd.rstrip("/"))
            _replace_symlink_at(
                latest_path,
                f"_current_{project_name}.md",
                reviews_fd,
            )
    finally:
        if history_fd >= 0:
            os.close(history_fd)
        if session_fd >= 0:
            os.close(session_fd)
        os.close(reviews_fd)

    return archive_path


def write_review(session_id: str, review_text: str, review_number: int, project_cwd: str = ""):
    """Write review to file and update symlinks."""
    prepared = prepare_review_document(session_id, review_text, review_number, project_cwd)
    return publish_review_document(
        session_id,
        review_number,
        project_cwd,
        str(prepared["review_document"]),
        str(prepared["review_sha256"]),
    )


def _acquire_lock(session_id: str):
    """Acquire file lock to prevent concurrent execution. Returns lock file handle or None."""
    from config import STATE_DIR
    ensure_private_directory(STATE_DIR)
    lock_path = os.path.join(STATE_DIR, f"{session_id}.lock")
    lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except BlockingIOError:
        lock_file.close()
        return None


def _write_pending_marker(session_id: str, project_root: str, review_path: str):
    """Write pending auto-review for the UserPromptSubmit hook to deliver."""
    from config import STATE_DIR
    return write_pending_marker(
        state_dir=STATE_DIR,
        session_id=session_id,
        project_root=project_root,
        review_path=review_path,
    )


def _delivery_mode(intent: dict) -> str:
    mode = str(intent.get("delivery_mode") or "").strip()
    if mode in {"auto", "manual", "none"}:
        return mode
    return "auto" if intent.get("auto_delivery") else "none"


def _manual_result_matches(
    result_file: str,
    session_id: str,
    review_path: str,
    review_sha256: str,
) -> bool:
    if not result_file:
        return False
    try:
        with open(result_file, encoding="utf-8") as stream:
            result = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(result, dict)
        and result.get("status") == "success"
        and result.get("session_id") == session_id
        and result.get("review_path") == os.path.abspath(review_path)
        and result.get("review_sha256") == review_sha256
    )


def _materialize_review_delivery_intent(session_id: str, intent: dict) -> str:
    raw_review_path = str(intent.get("review_path") or "").strip()
    review_path = os.path.abspath(raw_review_path) if raw_review_path else ""
    expected_hash = str(intent.get("review_sha256") or "")
    review_number = intent.get("review_number")
    project_cwd = canonical_project_root(
        str(intent.get("project_root") or intent.get("project_cwd") or "")
    )
    document = intent.get("review_document")
    if (
        not review_path
        or not isinstance(review_number, int)
        or review_number < 1
        or len(expected_hash) != 64
    ):
        raise ValueError("pending review delivery intent is incomplete")

    expected_path = os.path.abspath(
        os.path.join(
            REVIEWS_DIR,
            session_id,
            "history",
            f"review_{review_number:03d}.md",
        )
    )
    if isinstance(document, str):
        if review_path != expected_path:
            raise ValueError("pending review delivery path is outside its session archive")
        document_session_id, document_project_sha256 = review_document_identity(document)
        if document_session_id != session_id:
            raise ValueError("pending review document session mismatch")
        if document_project_sha256 != project_identity_sha256(project_cwd):
            raise ValueError("pending review document project mismatch")
        if hashlib.sha256(document.encode("utf-8")).hexdigest() != expected_hash:
            raise ValueError("pending review document hash mismatch")
        publish_review_document(
            session_id,
            review_number,
            project_cwd,
            document,
            expected_hash,
            allow_existing=True,
        )
    elif not os.path.isfile(review_path):
        raise ValueError("pending review artifact is missing and cannot be reconstructed")

    with open(review_path, "rb") as review_stream:
        actual_hash = hashlib.sha256(review_stream.read()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("pending review artifact changed before delivery recovery")
    artifact_session_id, artifact_project_sha256 = review_artifact_identity(review_path)
    if artifact_session_id != session_id:
        raise ValueError("pending review artifact session mismatch")
    if artifact_project_sha256 != project_identity_sha256(project_cwd):
        raise ValueError("pending review artifact project mismatch")
    return review_path


def _recover_pending_review_delivery(session_id: str, state: dict, project_root: str):
    intent = state.get("pending_review_delivery")
    if not isinstance(intent, dict):
        return False, None

    success_state = intent.get("success_state")
    if not isinstance(success_state, dict):
        log(
            "pending_delivery_recovery_failed",
            session_id=session_id,
            reason="missing_success_state",
        )
        return True, None

    try:
        review_path = _materialize_review_delivery_intent(session_id, intent)
    except (FileExistsError, OSError, UnicodeDecodeError, ValueError) as exc:
        log(
            "pending_delivery_recovery_failed",
            session_id=session_id,
            path=str(intent.get("review_path") or ""),
            reason=str(exc),
        )
        return True, None

    with open(review_path, "rb") as review_stream:
        actual_hash = hashlib.sha256(review_stream.read()).hexdigest()
    mode = _delivery_mode(intent)

    if mode == "auto":
        delivered = delivery_receipt_matches(
            state_dir=STATE_DIR,
            session_id=session_id,
            project_root=project_root,
            review_path=review_path,
            review_sha256=actual_hash,
        )
        if not delivered:
            pending_path = os.path.join(
                STATE_DIR, f"auto_review_pending_{session_id}.json"
            )
            status = pending_status(
                pending_path,
                expected_session_id=session_id,
                expected_project_root=project_root,
            )
            if status.get("exists"):
                same_pending = (
                    status.get("deliverable")
                    and status.get("review_path") == review_path
                    and status.get("review_sha256") == actual_hash
                )
                if not same_pending:
                    log(
                        "pending_delivery_recovery_conflict",
                        session_id=session_id,
                        path=pending_path,
                        reason=status.get("reason", "unknown"),
                    )
                    return True, None
            else:
                _write_pending_marker(session_id, project_root, review_path)
                log(
                    "pending_marker_recovered",
                    session_id=session_id,
                    path=review_path,
                )

    if mode == "manual":
        result_file = str(intent.get("manual_result_path") or "")
        if not _manual_result_matches(
            result_file,
            session_id,
            review_path,
            actual_hash,
        ):
            log(
                "pending_manual_result_required",
                session_id=session_id,
                path=review_path,
            )
            return True, review_path

    final_state = dict(success_state)
    final_state.pop("pending_review_delivery", None)
    final_state["project_root"] = project_root
    save_state(session_id, final_state)
    log("pending_delivery_recovered", session_id=session_id, path=review_path)
    return True, review_path


def _read_last_review(session_id: str) -> str:
    """Read last review content (without metadata header) for incremental review."""
    session_dir = os.path.join(REVIEWS_DIR, session_id)
    latest_path = os.path.join(session_dir, "latest.md")
    if not os.path.exists(latest_path):
        return ""
    try:
        with open(latest_path, "r", encoding="utf-8") as f:
            text = f.read()
        META_MARKER = "<!-- META_END -->"
        if META_MARKER in text:
            text = text.split(META_MARKER, 1)[1].strip()
        elif text.startswith("<!--"):
            text = text.split("-->\n", 1)[-1].strip()
        return text
    except Exception:
        return ""


def _read_user_context(project_cwd: str) -> str:
    """Auto-discover and read user's memory files for personalized review context.

    Reads runtime-native context by default:
    - Codex runtime: global/project AGENTS.md
    - Claude Code runtime: global/project CLAUDE.md

    Legacy cross-runtime context and Claude project feedback memory are included
    only when OVERWATCH_INCLUDE_LEGACY_CONTEXT is enabled.

    Returns formatted context string, or empty if nothing found.
    """
    from config import CC_PROJECTS_BASE, CC_PROJECTS_FALLBACKS
    import glob
    import hashlib

    sections = []
    extra_paths = os.environ.get("OVERWATCH_CONTEXT_PATHS", "")

    def _with_optional_legacy(primary, legacy):
        sources = list(primary)
        if INCLUDE_LEGACY_CONTEXT:
            seen = {path for _, path in sources}
            sources.extend((label, path) for label, path in legacy if path not in seen)
        return sources

    is_codex = ADAPTER == "codex"
    global_sources = _with_optional_legacy(
        [(
            "Codex User Engineering Standards" if is_codex else "Claude Code User Engineering Standards",
            os.path.expanduser("~/.codex/AGENTS.md" if is_codex else "~/.claude/CLAUDE.md"),
        )],
        [
            ("Legacy Codex User Engineering Standards", os.path.expanduser("~/.codex/AGENTS.md")),
            ("Legacy Claude Code User Engineering Standards", os.path.expanduser("~/.claude/CLAUDE.md")),
        ],
    )

    # 1. Global user standards.
    for label, global_path in global_sources:
        if not os.path.isfile(global_path):
            continue
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                if len(content) > 3000:
                    content = content[:3000] + "\n\n... [truncated]"
                sections.append(f"### {label}\n{content}")
        except Exception:
            pass

    # 2. Project context.
    if project_cwd:
        project_sources = _with_optional_legacy(
            [(
                "Codex Project Context" if is_codex else "Claude Code Project Context",
                os.path.join(project_cwd, "AGENTS.md" if is_codex else os.path.join(".claude", "CLAUDE.md")),
            )],
            [
                ("Legacy Codex Project Context", os.path.join(project_cwd, "AGENTS.md")),
                ("Legacy Claude Code Project Context", os.path.join(project_cwd, ".claude", "CLAUDE.md")),
            ],
        )
        for label, project_path in project_sources:
            if not os.path.isfile(project_path):
                continue
            try:
                with open(project_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    if len(content) > 2000:
                        content = content[:2000] + "\n\n... [truncated]"
                    sections.append(f"### {label}\n{content}")
            except Exception:
                pass

    # 3. Claude project memory feedback files (L4 — lessons learned).
    if project_cwd and (not is_codex or INCLUDE_LEGACY_CONTEXT):
        # Discover memory directory: try CC_PROJECTS_BASE + fallbacks
        # CC encodes paths: / → -, non-ASCII → -, leading - kept
        encoded = "".join(c if c.isascii() and c not in "/" else "-" for c in project_cwd)
        search_dirs = [CC_PROJECTS_BASE] + CC_PROJECTS_FALLBACKS
        feedback_texts = []

        for base in search_dirs:
            memory_dir = os.path.join(base, encoded, "memory")
            if not os.path.isdir(memory_dir):
                continue
            for fb_file in sorted(glob.glob(os.path.join(memory_dir, "feedback_*.md"))):
                try:
                    with open(fb_file, "r", encoding="utf-8") as f:
                        text = f.read().strip()
                    if text:
                        # Strip frontmatter, keep content
                        if text.startswith("---"):
                            parts = text.split("---", 2)
                            if len(parts) >= 3:
                                text = parts[2].strip()
                        name = os.path.basename(fb_file)
                        feedback_texts.append(f"**{name}**: {text[:500]}")
                except Exception:
                    continue
            if feedback_texts:
                break  # Found memory in this dir, don't check fallbacks

        if feedback_texts:
            combined = "\n\n".join(feedback_texts)
            if len(combined) > 3000:
                combined = combined[:3000] + "\n\n... [truncated]"
            sections.append(f"### Project Lessons (from memory)\n{combined}")

    # 4. Extra paths from env var (escape hatch, undocumented)
    if extra_paths:
        for p in extra_paths.split(":"):
            p = os.path.expanduser(p.strip())
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read()[:2000].strip()
                    if content:
                        sections.append(f"### {os.path.basename(p)}\n{content}")
                except Exception:
                    pass

    if not sections:
        return ""

    from config import MAX_USER_CONTEXT_CHARS
    result = "## User Context\n\n" + "\n\n---\n\n".join(sections)
    if len(result) > MAX_USER_CONTEXT_CHARS:
        result = result[:MAX_USER_CONTEXT_CHARS] + "\n\n... [user context truncated]"
    return result




def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_git_context(project_cwd: str) -> str:
    """Gather git diff and recent commits for review context. Returns empty string if not a git repo."""
    import subprocess
    from config import MAX_GIT_DIFF_CHARS

    if not project_cwd or not os.path.isdir(project_cwd):
        return ""

    def _run(cmd):
        try:
            result = subprocess.run(
                cmd, cwd=project_cwd, capture_output=True, text=True, timeout=10
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    # Check if it's a git repo
    if not _run(["git", "rev-parse", "--git-dir"]):
        return ""

    parts = []

    # Recent commits (last 5)
    log_output = _run(["git", "log", "--oneline", "-5"])
    if log_output:
        parts.append(f"### Recent Commits\n```\n{log_output}\n```")

    # Uncommitted changes
    diff_output = _run(["git", "diff", "HEAD"])
    if not diff_output:
        diff_output = _run(["git", "diff"])  # fallback for initial commits
    if diff_output:
        if len(diff_output) > MAX_GIT_DIFF_CHARS:
            diff_output = diff_output[:MAX_GIT_DIFF_CHARS] + "\n\n... [diff truncated]"
        parts.append(f"### Uncommitted Changes\n```diff\n{diff_output}\n```")

    # Staged but not committed
    staged = _run(["git", "diff", "--cached"])
    if staged and staged != diff_output:
        if len(staged) > MAX_GIT_DIFF_CHARS // 2:
            staged = staged[:MAX_GIT_DIFF_CHARS // 2] + "\n\n... [staged diff truncated]"
        parts.append(f"### Staged Changes\n```diff\n{staged}\n```")

    if not parts:
        return ""

    return "## Git Context\n\n" + "\n\n".join(parts)


def _compute_cooldown_seconds(consecutive_failures: int) -> int:
    base = max(1, REVIEW_FAILURE_COOLDOWN_SECONDS)
    cooldown = base * (2 ** max(0, consecutive_failures - 1))
    return min(cooldown, max(base, REVIEW_MAX_COOLDOWN_SECONDS))


MAX_CONSECUTIVE_FAILURES = 5  # After this many failures, reset and try fresh


def _is_in_cooldown(state: dict) -> bool:
    cooldown_until = state.get("cooldown_until", "")
    if not cooldown_until:
        return False
    try:
        if datetime.now() >= datetime.fromisoformat(cooldown_until):
            return False
        # Auto-reset after too many consecutive failures to avoid permanent lockout
        if int(state.get("consecutive_failures", 0)) >= MAX_CONSECUTIVE_FAILURES:
            return False
        return True
    except ValueError:
        return False


def _mark_attempt_started(state: dict) -> dict:
    return {
        **state,
        "last_attempt_at": _now_iso(),
    }


def _mark_review_success(state: dict) -> dict:
    return {
        **state,
        "last_review_status": "success",
        "last_error": "",
        "consecutive_failures": 0,
        "cooldown_until": "",
        "last_success_at": _now_iso(),
    }


def _mark_review_failure(state: dict, error_message: str) -> dict:
    failures = int(state.get("consecutive_failures", 0)) + 1
    if failures >= MAX_CONSECUTIVE_FAILURES:
        # Reset to give a fresh start after too many failures
        log("cooldown_reset", consecutive_failures=failures, reason="max_failures_reached")
        return {
            **state,
            "last_review_status": "failed",
            "last_error": error_message[:1000],
            "consecutive_failures": 0,
            "cooldown_until": "",
        }
    cooldown_seconds = _compute_cooldown_seconds(failures)
    cooldown_until = (datetime.now() + timedelta(seconds=cooldown_seconds)).isoformat(timespec="seconds")
    return {
        **state,
        "last_review_status": "failed",
        "last_error": error_message[:1000],
        "consecutive_failures": failures,
        "cooldown_until": cooldown_until,
    }


def _is_valid_review_text(review_text: str) -> bool:
    if not review_text or not review_text.strip():
        return False
    text = review_text.strip()
    if text.lower() in {"null", "none", "{}", "[]"}:
        return False
    if text.startswith("[Overwatch"):
        return False
    return len(text) >= MIN_REVIEW_CHARS


def transcript_identity_error(expected_session_id: str, native_session_ids: set[str]) -> str:
    if not native_session_ids:
        return "missing_native_session_id"
    if native_session_ids != {expected_session_id}:
        return "session_id_mismatch"
    return ""


def run(
    session_id: str,
    transcript_path: str,
    force: bool = False,
    project_cwd: str = "",
    result_file: str = "",
):
    """Overwatch main flow."""
    if not valid_session_id(session_id):
        log("run_rejected_session_id", session_id=session_id)
        return None
    effective_cwd = canonical_project_root(project_cwd or os.getcwd())
    if not effective_cwd or not project_is_allowed(effective_cwd):
        log(
            "run_rejected_project_allowlist",
            session_id=session_id,
            project_cwd=effective_cwd,
        )
        return None
    try:
        native_session_ids = get_transcript_session_ids(ADAPTER, transcript_path)
        transcript_roots = {
            canonical_project_root(cwd)
            for cwd in get_transcript_project_cwds(ADAPTER, transcript_path)
            if cwd
        }
    except OSError as exc:
        log(
            "run_rejected_transcript_identity",
            session_id=session_id,
            native_session_ids=[],
            reason="transcript_identity_unreadable",
            error=str(exc),
        )
        return None
    identity_error = transcript_identity_error(session_id, native_session_ids)
    if identity_error:
        log(
            "run_rejected_transcript_identity",
            session_id=session_id,
            native_session_ids=sorted(native_session_ids),
            reason=identity_error,
        )
        return None
    if transcript_roots != {effective_cwd}:
        log(
            "run_rejected_transcript_project",
            session_id=session_id,
            project_cwd=effective_cwd,
            transcript_project_roots=sorted(transcript_roots),
            reason=(
                "missing_transcript_project"
                if not transcript_roots
                else "transcript_project_mismatch"
            ),
        )
        return None
    lock = _acquire_lock(session_id)
    if lock is None:
        log("run_skipped_lock", session_id=session_id, reason="another_instance_running")
        return None

    try:
        return _run_inner(
            session_id,
            transcript_path,
            force,
            effective_cwd,
            result_file,
        )
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _run_inner(
    session_id: str,
    transcript_path: str,
    force: bool = False,
    project_cwd: str = "",
    result_file: str = "",
):
    """Core review logic."""
    project_cwd = canonical_project_root(project_cwd)
    state = load_state(session_id)
    state_project_root = canonical_project_root(
        str(state.get("project_root") or "")
    )
    if state_project_root and state_project_root != project_cwd:
        log(
            "run_rejected_state_project",
            session_id=session_id,
            project_cwd=project_cwd,
            state_project_root=state_project_root,
        )
        return None
    state = {**state, "project_root": project_cwd}

    # Delivery recovery is a local durability operation and must not depend on
    # credentials or backend availability after the review already succeeded.
    recovered, recovered_review_path = _recover_pending_review_delivery(
        session_id, state, project_cwd
    )
    if recovered:
        return recovered_review_path

    from config import API_AUTH_TOKEN
    if REVIEW_BACKEND == "api" and not API_AUTH_TOKEN:
        log("config_error", session_id=session_id, error="ANTHROPIC_API_KEY not set")
        return
    if REVIEW_BACKEND not in {"api", "codex_exec"}:
        log("config_error", session_id=session_id, error=f"Unknown OVERWATCH_BACKEND={REVIEW_BACKEND}")
        return

    if not force and _is_in_cooldown(state):
        log(
            "run_skipped_cooldown",
            session_id=session_id,
            cooldown_until=state.get("cooldown_until", ""),
            consecutive_failures=state.get("consecutive_failures", 0),
        )
        return

    parse = get_adapter(ADAPTER)
    turns = parse(transcript_path, offset=0)

    if not turns:
        log("run_skipped_empty_transcript", session_id=session_id)
        return

    current_turn_count = len([t for t in turns if t.role == "user"])
    last_reviewed = state.get("last_reviewed_turn", 0)

    if not force and current_turn_count <= last_reviewed:
        log("run_skipped_no_new_turns", session_id=session_id, current=current_turn_count, last=last_reviewed)
        return

    last_review = _read_last_review(session_id)
    git_context = _get_git_context(project_cwd)
    user_context = _read_user_context(project_cwd)

    context_text, updated_state = build_review_context(turns, state, "", git_context, user_context)
    updated_state["project_root"] = project_cwd
    review_number = updated_state["review_count"]
    history_dir = os.path.join(REVIEWS_DIR, session_id, "history")
    while os.path.exists(os.path.join(history_dir, f"review_{review_number:03d}.md")):
        review_number += 1
    updated_state["review_count"] = review_number
    attempt_state = _mark_attempt_started(
        {**state, "review_count": review_number, "project_root": project_cwd}
    )
    save_state(session_id, attempt_state)

    # Determine if agentic review (with tools) is supported
    use_tools = project_cwd and API_FORMAT == "anthropic"

    system_prompt, user_message = build_review_prompt(context_text, review_number, last_review, include_tools=use_tools)

    started = time.time()
    log("review_call_start", session_id=session_id, review=review_number, backend=REVIEW_BACKEND, model=REVIEW_MODEL)

    if REVIEW_BACKEND == "codex_exec":
        from codex_exec_client import call_codex_exec
        review_text = call_codex_exec(system_prompt, user_message, project_cwd=project_cwd)
    elif project_cwd:
        from tools import TOOL_DEFINITIONS, execute_tool
        review_text = call_claude_with_tools(
            system_prompt, user_message,
            tool_definitions=TOOL_DEFINITIONS,
            tool_executor=execute_tool,
            project_cwd=project_cwd,
        )
    else:
        review_text = call_claude(system_prompt, user_message)
    latency_ms = int((time.time() - started) * 1000)

    if review_text.startswith("[Overwatch") and ("Error" in review_text or "API Error" in review_text):
        failed_state = _mark_review_failure(attempt_state, review_text)
        save_state(session_id, failed_state)
        log(
            "review_call_failed",
            session_id=session_id,
            review=review_number,
            latency_ms=latency_ms,
            error=review_text[:300],
            cooldown_until=failed_state.get("cooldown_until", ""),
        )
        return

    if not _is_valid_review_text(review_text):
        error = f"Invalid review output (len={len(review_text.strip()) if review_text else 0})"
        failed_state = _mark_review_failure(attempt_state, error)
        save_state(session_id, failed_state)
        log(
            "review_validation_failed",
            session_id=session_id,
            review=review_number,
            latency_ms=latency_ms,
            error=error,
            cooldown_until=failed_state.get("cooldown_until", ""),
        )
        return

    success_state = _mark_review_success(
        {**updated_state, "last_attempt_at": attempt_state.get("last_attempt_at", "")}
    )
    prepared = prepare_review_document(
        session_id,
        review_text,
        review_number,
        project_cwd,
    )
    delivery_mode = "manual" if force and result_file else ("none" if force else "auto")
    delivery_intent_state = {
        **attempt_state,
        "pending_review_delivery": {
            **prepared,
            "review_number": review_number,
            "session_id": session_id,
            "project_cwd": project_cwd,
            "project_root": project_cwd,
            "delivery_mode": delivery_mode,
            "manual_result_path": os.path.abspath(result_file) if result_file else "",
            "success_state": success_state,
        },
    }
    save_state(session_id, delivery_intent_state)
    log("pending_delivery_intent_saved", session_id=session_id, review=review_number)

    review_path = _materialize_review_delivery_intent(
        session_id,
        delivery_intent_state["pending_review_delivery"],
    )
    log("review_written", session_id=session_id, review=review_number, latency_ms=latency_ms, path=review_path)

    if delivery_mode == "auto":
        _write_pending_marker(session_id, project_cwd, review_path)
        log("pending_marker_written", session_id=session_id, review=review_number)

    if delivery_mode == "manual":
        return review_path

    save_state(session_id, success_state)
    log("state_saved", session_id=session_id, review=review_number, next_after_turn=success_state['last_reviewed_turn'])
    return review_path


def write_manual_result(result_file: str, session_id: str, review_path: str) -> None:
    path = os.path.abspath(os.path.expanduser(result_file))
    parent = os.path.dirname(path)
    ensure_private_directory(parent)
    with open(review_path, "rb") as review_stream:
        review_sha256 = hashlib.sha256(review_stream.read()).hexdigest()
    state = load_state(session_id)
    intent = state.get("pending_review_delivery")
    success_state = None
    if isinstance(intent, dict):
        project_root = canonical_project_root(
            str(intent.get("project_root") or intent.get("project_cwd") or "")
        )
        artifact_session_id, artifact_project_sha256 = review_artifact_identity(
            review_path
        )
        if artifact_session_id != session_id:
            raise ValueError("manual review artifact session mismatch")
        if artifact_project_sha256 != project_identity_sha256(project_root):
            raise ValueError("manual review artifact project mismatch")
        if (
            _delivery_mode(intent) != "manual"
            or os.path.abspath(str(intent.get("manual_result_path") or "")) != path
            or os.path.abspath(str(intent.get("review_path") or "")) != os.path.abspath(review_path)
            or intent.get("review_sha256") != review_sha256
            or not isinstance(intent.get("success_state"), dict)
        ):
            raise ValueError("manual result does not match pending review delivery intent")
        success_state = dict(intent["success_state"])
        success_state["project_root"] = project_root
    else:
        raise ValueError("manual result has no pending review delivery intent")
    payload = {
        "status": "success",
        "session_id": session_id,
        "review_path": os.path.abspath(review_path),
        "review_sha256": review_sha256,
        "created_at": _now_iso(),
    }
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_directory(parent)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if success_state is not None:
        success_state.pop("pending_review_delivery", None)
        save_state(session_id, success_state)
        log("manual_result_committed", session_id=session_id, path=path)


def validated_manual_result_path(result_file: str) -> str:
    path = os.path.abspath(os.path.expanduser(result_file))
    state_root = os.path.realpath(os.path.abspath(os.path.expanduser(STATE_DIR)))
    parent = os.path.realpath(os.path.dirname(path))
    name = os.path.basename(path)
    if parent != state_root or not name.startswith(("manual_review_result_", "manual_review_result.")):
        raise ValueError("manual result file must be a managed file inside OVERWATCH_STATE_DIR")
    return path


def log(event: str, **fields):
    """Structured stderr logger (doesn't interfere with hook stdout)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    extras = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in fields.items())
    msg = f"[Overwatch {timestamp}] event={event}"
    if extras:
        msg += " " + extras
    print(msg, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Overwatch - Independent AI Session Reviewer")
    parser.add_argument("--session-id", required=True, help="Session UUID")
    parser.add_argument("--transcript", required=True, help="Path to session JSONL transcript")
    parser.add_argument("--force", action="store_true", help="Force review regardless of turn threshold")
    parser.add_argument("--cwd", default="", help="Project working directory")
    parser.add_argument("--result-file", default="", help="Write exact successful manual-review identity as JSON")
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        log("transcript_not_found", transcript=args.transcript)
        sys.exit(1)

    if args.result_file:
        try:
            args.result_file = validated_manual_result_path(args.result_file)
        except ValueError as exc:
            log("result_file_rejected", result_file=args.result_file, error=str(exc))
            sys.exit(1)
        if os.path.islink(args.result_file):
            log("result_file_rejected", result_file=args.result_file, error="symlinks are not allowed")
            sys.exit(1)

    review_path = run(
        args.session_id,
        args.transcript,
        args.force,
        args.cwd,
        args.result_file,
    )
    if review_path and args.result_file:
        write_manual_result(args.result_file, args.session_id, review_path)
    if args.force and not review_path:
        sys.exit(1)


if __name__ == "__main__":
    main()
