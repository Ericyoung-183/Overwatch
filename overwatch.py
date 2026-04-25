#!/usr/bin/env python3
"""Overwatch main engine: parse transcript -> manage context -> call Claude API -> write review."""
import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta

# Ensure sibling modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    REVIEW_MODEL,
    API_FORMAT,
    REVIEWS_DIR,
    CURRENT_REVIEW_LINK,
    ADAPTER,
    MIN_REVIEW_CHARS,
    REVIEW_FAILURE_COOLDOWN_SECONDS,
    REVIEW_MAX_COOLDOWN_SECONDS,
)
from api_client import call_claude, call_claude_with_tools
from adapters import get_adapter
from context_manager import load_state, save_state, build_review_context
from prompts import build_review_prompt


def write_review(session_id: str, review_text: str, review_number: int, project_cwd: str = ""):
    """Write review to file and update symlinks."""
    session_dir = os.path.join(REVIEWS_DIR, session_id)
    history_dir = os.path.join(session_dir, "history")
    os.makedirs(history_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"<!-- Overwatch Review #{review_number} | {timestamp} | session: {session_id} | project: {project_cwd} -->\n<!-- META_END -->\n\n"
    full_text = header + review_text

    latest_path = os.path.join(session_dir, "latest.md")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    archive_path = os.path.join(history_dir, f"review_{review_number:03d}.md")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    if os.path.islink(CURRENT_REVIEW_LINK) or os.path.exists(CURRENT_REVIEW_LINK):
        os.remove(CURRENT_REVIEW_LINK)
    os.symlink(latest_path, CURRENT_REVIEW_LINK)

    if project_cwd:
        project_name = os.path.basename(project_cwd.rstrip("/"))
        project_link = os.path.join(REVIEWS_DIR, f"_current_{project_name}.md")
        if os.path.islink(project_link) or os.path.exists(project_link):
            os.remove(project_link)
        os.symlink(latest_path, project_link)

    return latest_path


def _acquire_lock(session_id: str):
    """Acquire file lock to prevent concurrent execution. Returns lock file handle or None."""
    from config import STATE_DIR
    os.makedirs(STATE_DIR, exist_ok=True)
    lock_path = os.path.join(STATE_DIR, f"{session_id}.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except BlockingIOError:
        lock_file.close()
        return None


def _write_pending_marker(session_id: str, review_path: str):
    """Write pending auto-review for the UserPromptSubmit hook to deliver."""
    from config import STATE_DIR
    import json as _json
    pending_path = os.path.join(STATE_DIR, f"auto_review_pending_{session_id}.json")
    with open(pending_path, "w") as f:
        _json.dump({"review_path": review_path, "session_id": session_id}, f)


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

    Reads (if they exist):
    - Global CLAUDE.md (~/.claude/CLAUDE.md) — user's engineering standards
    - Project CLAUDE.md (project/.claude/CLAUDE.md) — project context
    - Project memory feedback files (feedback_*.md) — lessons learned

    Returns formatted context string, or empty if nothing found.
    """
    from config import CC_PROJECTS_BASE, CC_PROJECTS_FALLBACKS
    import glob
    import hashlib

    sections = []
    extra_paths = os.environ.get("OVERWATCH_CONTEXT_PATHS", "")

    # 1. Global CLAUDE.md (L2 — user's engineering standards)
    global_claude = os.path.expanduser("~/.claude/CLAUDE.md")
    if os.path.isfile(global_claude):
        try:
            with open(global_claude, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                # Truncate to keep context manageable
                if len(content) > 3000:
                    content = content[:3000] + "\n\n... [truncated]"
                sections.append(f"### User Engineering Standards\n{content}")
        except Exception:
            pass

    # 2. Project CLAUDE.md (L3 — project context)
    if project_cwd:
        project_claude = os.path.join(project_cwd, ".claude", "CLAUDE.md")
        if os.path.isfile(project_claude):
            try:
                with open(project_claude, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    if len(content) > 2000:
                        content = content[:2000] + "\n\n... [truncated]"
                    sections.append(f"### Project Context\n{content}")
            except Exception:
                pass

    # 3. Project memory feedback files (L4 — lessons learned)
    if project_cwd:
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


def should_trigger_early(transcript_path: str = "", turns: list = None) -> bool:
    """Check if recent transcript content warrants an early review trigger.

    Parses the transcript via the adapter (structured, not raw text) and only
    scans user messages for signals. This avoids false positives from file paths,
    tool outputs, and JSONL structure that plagued the raw-text approach.

    Accepts either a transcript_path (will parse) or pre-parsed turns list
    (avoids double-parsing when the hook already has turns).

    Signals:
    1. User explicitly requests review/check
    2. User corrects or expresses frustration with the AI
    3. High file-change density (many Write/Edit in recent turns)
    4. git commit/push just happened
    """
    import re

    if turns is None:
        if not transcript_path:
            return False
        try:
            parse = get_adapter(ADAPTER)
            turns = parse(transcript_path, offset=0)
        except Exception:
            return False

    if not turns:
        return False

    user_turns = [t for t in turns if t.role == "user"]
    tool_turns = [t for t in turns if t.role == "tool_use"]

    if not user_turns:
        return False

    # Signal 1: User explicitly requests review (scan last 3 user messages)
    review_patterns = [
        r'\breview\b', r'\b审查\b', r'\b诊断\b',
        r'检查', r'确认一下', r'看看对不对', r'完整检查',
    ]
    for turn in user_turns[-3:]:
        text = turn.content.lower()
        for pattern in review_patterns:
            if re.search(pattern, text):
                log("smart_trigger_signal", signal="review_request", turn=turn.index, match=pattern)
                return True

    # Signal 2: User correction / frustration (scan last 3 user messages)
    correction_patterns = [
        r'不对', r'错了', r'搞错', r'不是这样', r'重新来', r'再想想',
        r'\bwrong\b', r'\bnot what i\b', r'\bstill broken\b', r'\bredo\b',
    ]
    for turn in user_turns[-3:]:
        text = turn.content.lower()
        for pattern in correction_patterns:
            if re.search(pattern, text):
                log("smart_trigger_signal", signal="user_correction", turn=turn.index, match=pattern)
                return True

    # Signal 3: High file-change density (5+ Write/Edit in last 15 tool_use turns)
    recent_tool_names = [t.tool_name for t in tool_turns[-15:]]
    write_edit_count = sum(1 for n in recent_tool_names if n in ("Write", "Edit"))
    if write_edit_count >= 5:
        log("smart_trigger_signal", signal="high_edit_density", write_edit_count=write_edit_count)
        return True

    # Signal 4: git commit/push just happened (check recent Bash tool_use)
    for t in tool_turns[-5:]:
        if t.tool_name == "Bash" and t.content:
            cmd = t.content.lower()
            if "git commit" in cmd or "git push" in cmd:
                log("smart_trigger_signal", signal="git_commit", tool_content=cmd[:100])
                return True

    return False


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


def run(session_id: str, transcript_path: str, force: bool = False, project_cwd: str = ""):
    """Overwatch main flow."""
    lock = _acquire_lock(session_id)
    if lock is None:
        log("run_skipped_lock", session_id=session_id, reason="another_instance_running")
        return

    try:
        _run_inner(session_id, transcript_path, force, project_cwd)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock_path = lock.name
        lock.close()
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _run_inner(session_id: str, transcript_path: str, force: bool = False, project_cwd: str = ""):
    """Core review logic."""
    from config import API_AUTH_TOKEN
    if not API_AUTH_TOKEN:
        log("config_error", session_id=session_id, error="ANTHROPIC_API_KEY not set")
        return

    state = load_state(session_id)

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
    updated_state = _mark_attempt_started(updated_state)
    review_number = updated_state["review_count"]
    save_state(session_id, updated_state)

    # Determine if agentic review (with tools) is supported
    use_tools = project_cwd and API_FORMAT == "anthropic" and "claude" in REVIEW_MODEL.lower()

    system_prompt, user_message = build_review_prompt(context_text, review_number, last_review, include_tools=use_tools)

    started = time.time()
    log("api_call_start", session_id=session_id, review=review_number, model=REVIEW_MODEL)

    # Use agentic review (with tools) when project_cwd is available
    if project_cwd:
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
        failed_state = _mark_review_failure(updated_state, review_text)
        save_state(session_id, failed_state)
        log(
            "api_call_failed",
            session_id=session_id,
            review=review_number,
            latency_ms=latency_ms,
            error=review_text[:300],
            cooldown_until=failed_state.get("cooldown_until", ""),
        )
        return

    if not _is_valid_review_text(review_text):
        error = f"Invalid review output (len={len(review_text.strip()) if review_text else 0})"
        failed_state = _mark_review_failure(updated_state, error)
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

    review_path = write_review(session_id, review_text, review_number, project_cwd)
    log("review_written", session_id=session_id, review=review_number, latency_ms=latency_ms, path=review_path)

    if not force:
        _write_pending_marker(session_id, review_path)
        log("pending_marker_written", session_id=session_id, review=review_number)

    success_state = _mark_review_success(updated_state)
    save_state(session_id, success_state)
    log("state_saved", session_id=session_id, review=review_number, next_after_turn=success_state['last_reviewed_turn'])


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
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        log("transcript_not_found", transcript=args.transcript)
        sys.exit(1)

    run(args.session_id, args.transcript, args.force, args.cwd)


if __name__ == "__main__":
    main()
