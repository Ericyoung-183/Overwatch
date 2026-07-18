"""Session-bound fallback trigger persistence."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from config import require_valid_session_id
from pending_review import review_artifact_identity
from runtime_fs import (
    canonical_project_root,
    ensure_private_directory,
    fsync_directory,
    project_identity_sha256,
)


def trigger_path(state_dir: str, session_id: str) -> str:
    session_id = require_valid_session_id(session_id)
    return str(Path(state_dir) / "triggers" / f"{session_id}.json")


def write_trigger(state_dir: str, session_id: str, payload: dict) -> str:
    session_id = require_valid_session_id(session_id)
    project_root = canonical_project_root(
        str(payload.get("project_root") or payload.get("cwd") or "")
    )
    if not project_root:
        raise ValueError("Overwatch trigger requires a project root")
    path = Path(trigger_path(state_dir, session_id))
    ensure_private_directory(path.parent)
    body = {
        **payload,
        "session_id": session_id,
        "cwd": project_root,
        "project_root": project_root,
        "project_sha256": project_identity_sha256(project_root),
    }
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


def read_trigger(state_dir: str, session_id: str, expected_project_root: str) -> dict:
    session_id = require_valid_session_id(session_id)
    path = Path(trigger_path(state_dir, session_id))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Overwatch trigger root must be a JSON object")
    if payload.get("session_id") != session_id:
        raise ValueError("Overwatch trigger session does not match requested session")
    project_root = canonical_project_root(str(payload.get("project_root") or ""))
    expected_root = canonical_project_root(expected_project_root)
    if project_root != expected_root:
        raise ValueError("Overwatch trigger project does not match requested project")
    if payload.get("project_sha256") != project_identity_sha256(expected_root):
        raise ValueError("Overwatch trigger project identity is invalid")
    return payload


def read_auto_review_bytes(
    state_dir: str, session_id: str, expected_project_root: str
) -> bytes:
    payload = read_trigger(state_dir, session_id, expected_project_root)
    if payload.get("type") != "auto_review":
        raise ValueError("Overwatch trigger is not an auto-review")
    review_path = str(payload.get("review_path") or "")
    expected_hash = str(payload.get("review_sha256") or "")
    if not review_path or len(expected_hash) != 64:
        raise ValueError("Overwatch auto-review trigger lacks exact review evidence")
    content = Path(review_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("Overwatch auto-review trigger hash mismatch")
    artifact_session_id, artifact_project_sha256 = review_artifact_identity(review_path)
    if artifact_session_id != session_id:
        raise ValueError("Overwatch auto-review artifact session mismatch")
    if artifact_project_sha256 != payload.get("project_sha256"):
        raise ValueError("Overwatch auto-review artifact project mismatch")
    return content


def auto_review_metadata(
    state_dir: str, session_id: str, expected_project_root: str
) -> dict:
    payload = read_trigger(state_dir, session_id, expected_project_root)
    if payload.get("type") != "auto_review":
        raise ValueError("Overwatch trigger is not an auto-review")
    required = ("pending_path", "marker_sha256", "review_path", "review_sha256")
    if any(not str(payload.get(key) or "").strip() for key in required):
        raise ValueError("Overwatch auto-review trigger lacks delivery metadata")
    return {
        **{
            key: payload[key]
            for key in (
                "session_id",
                "project_root",
                "project_sha256",
                "pending_path",
                "marker_sha256",
                "review_path",
                "review_sha256",
            )
        },
        "trigger_path": trigger_path(state_dir, session_id),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read session-bound Overwatch triggers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    read_auto = subparsers.add_parser(
        "read-auto-review", help="stream the exact hash-verified auto-review bytes"
    )
    read_auto.add_argument("--state-dir", required=True)
    read_auto.add_argument("--session-id", required=True)
    read_auto.add_argument("--project-root", required=True)
    metadata = subparsers.add_parser(
        "auto-review-metadata", help="print verified auto-review delivery metadata"
    )
    metadata.add_argument("--state-dir", required=True)
    metadata.add_argument("--session-id", required=True)
    metadata.add_argument("--project-root", required=True)
    args = parser.parse_args()
    try:
        if args.command == "read-auto-review":
            sys.stdout.buffer.write(read_auto_review_bytes(
                args.state_dir, args.session_id, args.project_root
            ))
            return 0
        if args.command == "auto-review-metadata":
            print(json.dumps(auto_review_metadata(
                args.state_dir, args.session_id, args.project_root
            ), ensure_ascii=False))
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
