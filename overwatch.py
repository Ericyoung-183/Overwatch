#!/usr/bin/env python3
"""Overwatch main engine: parse transcript -> manage context -> call Claude API -> write review."""
import argparse
import fcntl
import json
import os
import sys
from datetime import datetime

# Ensure sibling modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    REVIEW_MODEL,
    REVIEWS_DIR,
    CURRENT_REVIEW_LINK,
    ADAPTER,
)
from api_client import call_claude
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

    # Write latest.md
    latest_path = os.path.join(session_dir, "latest.md")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    # Archive
    archive_path = os.path.join(history_dir, f"review_{review_number:03d}.md")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    # Update global _current.md symlink
    if os.path.islink(CURRENT_REVIEW_LINK) or os.path.exists(CURRENT_REVIEW_LINK):
        os.remove(CURRENT_REVIEW_LINK)
    os.symlink(latest_path, CURRENT_REVIEW_LINK)

    # Update per-project symlink
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
    pending_path = os.path.join(STATE_DIR, "auto_review_pending.json")
    with open(pending_path, "w") as f:
        _json.dump({"review_path": review_path, "session_id": session_id}, f)


def run(session_id: str, transcript_path: str, force: bool = False, project_cwd: str = ""):
    """Overwatch main flow."""
    lock = _acquire_lock(session_id)
    if lock is None:
        log("Another Overwatch instance is running, skipping.")
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


def _read_project_description(project_cwd: str) -> str:
    """Extract project description from CLAUDE.md for context."""
    if not project_cwd:
        return ""
    claude_md = os.path.join(project_cwd, ".claude", "CLAUDE.md")
    if not os.path.exists(claude_md):
        return ""
    try:
        with open(claude_md, "r", encoding="utf-8") as f:
            content = f.read()
        return content[:500].strip()
    except Exception:
        return ""


def _run_inner(session_id: str, transcript_path: str, force: bool = False, project_cwd: str = ""):
    """Core review logic."""
    # 0. Verify API key is configured
    from config import API_AUTH_TOKEN
    if not API_AUTH_TOKEN:
        log("ANTHROPIC_API_KEY not set. Please set this environment variable.")
        return

    # 1. Load state
    state = load_state(session_id)

    # 2. Parse transcript (always from beginning for full context)
    parse = get_adapter(ADAPTER)
    turns = parse(transcript_path, offset=0)

    if not turns:
        log("No turns found in transcript, skipping.")
        return

    # 3. Check for new content
    current_turn_count = len([t for t in turns if t.role == "user"])
    last_reviewed = state.get("last_reviewed_turn", 0)

    if not force and current_turn_count <= last_reviewed:
        log(f"No new turns since last review (current={current_turn_count}, last={last_reviewed})")
        return

    # 4. Read incremental review inputs
    last_review = _read_last_review(session_id)
    project_description = _read_project_description(project_cwd)

    # 5. Build context
    context_text, updated_state = build_review_context(turns, state, project_description)
    review_number = updated_state["review_count"]

    # 6. Assemble prompt
    system_prompt, user_message = build_review_prompt(context_text, review_number, last_review)

    # 7. Call API
    log(f"Calling Claude API for review #{review_number} (model={REVIEW_MODEL})...")
    review_text = call_claude(system_prompt, user_message)

    # 7.5. Check for API errors
    if review_text.startswith("[Overwatch") and ("Error" in review_text or "API Error" in review_text):
        log(f"API call failed: {review_text[:200]}")
        return

    # 8. Write review
    review_path = write_review(session_id, review_text, review_number, project_cwd)
    log(f"Review written to: {review_path}")

    # 8.5. Write pending marker for auto-trigger (manual --force is injected by hook directly)
    if not force:
        _write_pending_marker(session_id, review_path)
        log("Pending review marker written for next-turn display.")

    # 9. Save state
    save_state(session_id, updated_state)
    log(f"State saved. Next review after turn {updated_state['last_reviewed_turn']}.")


def log(msg: str):
    """Log to stderr (doesn't interfere with hook stdout)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[Overwatch {timestamp}] {msg}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Overwatch - Independent AI Session Reviewer")
    parser.add_argument("--session-id", required=True, help="Session UUID")
    parser.add_argument("--transcript", required=True, help="Path to session JSONL transcript")
    parser.add_argument("--force", action="store_true", help="Force review regardless of turn threshold")
    parser.add_argument("--cwd", default="", help="Project working directory")
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        log(f"Transcript not found: {args.transcript}")
        sys.exit(1)

    run(args.session_id, args.transcript, args.force, args.cwd)


if __name__ == "__main__":
    main()
