"""Concurrency-safe session discovery and lock inspection."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from pathlib import Path

from config import require_valid_session_id, valid_session_id
from runtime_fs import canonical_project_root, ensure_private_directory, fsync_directory


class SessionProjectMismatchError(ValueError):
    """Raised when one session ID is observed in a different project root."""


def _atomic_json(path: Path, payload: dict) -> None:
    ensure_private_directory(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _load_records(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("sessions"), dict):
        return {
            sid: record
            for sid, record in payload["sessions"].items()
            if valid_session_id(sid) and isinstance(record, dict)
        }
    # Read the original cwd -> session map without preserving its unsafe shape.
    return {
        sid: {
            "cwd": canonical_project_root(cwd),
            "project_root": canonical_project_root(cwd),
            "updated_at": 0.0,
        }
        for cwd, sid in payload.items()
        if isinstance(cwd, str) and valid_session_id(sid)
    }


def record_session(state_dir: str, cwd: str, session_id: str, *, now: float | None = None) -> None:
    session_id = require_valid_session_id(session_id)
    project_root = canonical_project_root(cwd)
    if not project_root:
        raise ValueError("session project root is required")
    state = Path(state_dir)
    ensure_private_directory(state)
    map_path = state / "session_map.json"
    lock_path = state / "session_map.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        records = _load_records(map_path)
        existing = records.get(session_id)
        if isinstance(existing, dict):
            existing_root = canonical_project_root(
                str(existing.get("project_root") or existing.get("cwd") or "")
            )
            if existing_root and existing_root != project_root:
                raise SessionProjectMismatchError(
                    f"session {session_id!r} is already bound to project "
                    f"{existing_root!r}, not {project_root!r}"
                )
        records[session_id] = {
            "cwd": project_root,
            "project_root": project_root,
            "updated_at": time.time() if now is None else float(now),
        }
        newest = sorted(
            records.items(),
            key=lambda item: float(item[1].get("updated_at") or 0),
            reverse=True,
        )[:256]
        _atomic_json(map_path, {"version": 2, "sessions": dict(newest)})
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def sessions_for_project(state_dir: str, project_dir: str) -> list[str]:
    state = Path(state_dir)
    map_path = state / "session_map.json"
    if not map_path.is_file():
        return []
    project = canonical_project_root(project_dir)
    lock_path = state / "session_map.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        records = _load_records(map_path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    matches: list[tuple[float, str]] = []
    for sid, record in records.items():
        root = canonical_project_root(
            str(record.get("project_root") or record.get("cwd") or "")
        )
        if root == project:
            matches.append((float(record.get("updated_at") or 0), sid))
    return [sid for _, sid in sorted(matches, reverse=True)]


def project_root_for_session(state_dir: str, session_id: str) -> str:
    """Return the canonical project root already bound to a session."""
    session_id = require_valid_session_id(session_id)
    state = Path(state_dir)
    map_path = state / "session_map.json"
    if not map_path.is_file():
        return ""
    lock_path = state / "session_map.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        records = _load_records(map_path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    record = records.get(session_id)
    if not isinstance(record, dict):
        return ""
    return canonical_project_root(
        str(record.get("project_root") or record.get("cwd") or "")
    )


def session_lock_active(state_dir: str, session_id: str) -> bool:
    session_id = require_valid_session_id(session_id)
    lock_path = Path(state_dir) / f"{session_id}.lock"
    if not lock_path.exists():
        return False
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return False
