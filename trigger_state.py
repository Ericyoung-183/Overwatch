"""Session-bound fallback trigger persistence."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from config import require_valid_session_id
from runtime_fs import ensure_private_directory, fsync_directory


def trigger_path(state_dir: str, session_id: str) -> str:
    session_id = require_valid_session_id(session_id)
    return str(Path(state_dir) / "triggers" / f"{session_id}.json")


def write_trigger(state_dir: str, session_id: str, payload: dict) -> str:
    session_id = require_valid_session_id(session_id)
    path = Path(trigger_path(state_dir, session_id))
    ensure_private_directory(path.parent)
    body = {**payload, "session_id": session_id}
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(body, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return str(path)


def read_trigger(state_dir: str, session_id: str) -> dict:
    session_id = require_valid_session_id(session_id)
    path = Path(trigger_path(state_dir, session_id))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Overwatch trigger root must be a JSON object")
    if payload.get("session_id") != session_id:
        raise ValueError("Overwatch trigger session does not match requested session")
    return payload


def read_auto_review_bytes(state_dir: str, session_id: str) -> bytes:
    payload = read_trigger(state_dir, session_id)
    if payload.get("type") != "auto_review":
        raise ValueError("Overwatch trigger is not an auto-review")
    review_path = str(payload.get("review_path") or "")
    expected_hash = str(payload.get("review_sha256") or "")
    if not review_path or len(expected_hash) != 64:
        raise ValueError("Overwatch auto-review trigger lacks exact review evidence")
    content = Path(review_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("Overwatch auto-review trigger hash mismatch")
    return content


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read session-bound Overwatch triggers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    read_auto = subparsers.add_parser(
        "read-auto-review", help="stream the exact hash-verified auto-review bytes"
    )
    read_auto.add_argument("--state-dir", required=True)
    read_auto.add_argument("--session-id", required=True)
    args = parser.parse_args()
    try:
        sys.stdout.buffer.write(
            read_auto_review_bytes(args.state_dir, args.session_id)
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
