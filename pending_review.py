"""Pending auto-review lifecycle helpers."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Mapping

from config import require_valid_session_id
from runtime_fs import ensure_private_directory, fsync_directory


DEFAULT_PENDING_TTL_HOURS = 72.0
TTL_ENV_KEY = "OVERWATCH_PENDING_TTL_HOURS"
REVIEW_SESSION_RE = re.compile(r"\| session: ([^| ]+) \|")


@contextlib.contextmanager
def pending_lock(pending_path: Path):
    lock_path = pending_path.with_name(pending_path.name + ".lock")
    ensure_private_directory(lock_path.parent)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ttl_from_value(value: object, default: float | None = DEFAULT_PENDING_TTL_HOURS) -> float | None:
    ttl = _coerce_float(value)
    if ttl is None:
        return default
    if ttl <= 0:
        return None
    return ttl


def configured_ttl_hours(
    *,
    env: Mapping[str, str] | None = None,
    pending: Mapping[str, object] | None = None,
) -> float | None:
    """Return the active pending marker TTL in hours.

    Runtime env wins so operators can change delivery policy without rewriting
    existing markers. A non-positive TTL disables expiry.
    """
    source_env = os.environ if env is None else env
    if TTL_ENV_KEY in source_env:
        return _ttl_from_value(source_env.get(TTL_ENV_KEY))
    if pending and pending.get("ttl_hours") is not None:
        return _ttl_from_value(pending.get("ttl_hours"))
    return DEFAULT_PENDING_TTL_HOURS


def review_document_session_id(document: str) -> str:
    """Return the session bound into an Overwatch review document."""
    lines = document.splitlines()
    first_line = lines[0] if lines else ""
    second_line = lines[1] if len(lines) > 1 else ""
    match = REVIEW_SESSION_RE.search(first_line.rstrip("\r\n"))
    if not match or second_line != "<!-- META_END -->":
        raise ValueError("review artifact has no valid Overwatch session metadata")
    return require_valid_session_id(match.group(1))


def review_artifact_session_id(review_path: str | Path) -> str:
    """Return the session bound into an Overwatch review header."""
    path = Path(review_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as stream:
        document_head = stream.readline(4096) + stream.readline(4096)
    return review_document_session_id(document_head)


def write_pending_marker(
    *,
    state_dir: str,
    session_id: str,
    review_path: str,
    now: float | None = None,
    ttl_hours: float | None = None,
) -> str:
    """Write a pending auto-review marker and return its path."""
    session_id = require_valid_session_id(session_id)
    timestamp = time.time() if now is None else now
    active_ttl = configured_ttl_hours() if ttl_hours is None else ttl_hours
    review = Path(review_path).expanduser().resolve()
    artifact_session_id = review_artifact_session_id(review)
    if artifact_session_id != session_id:
        raise ValueError(
            f"review artifact session {artifact_session_id!r} does not match marker session {session_id!r}"
        )
    review_sha256 = hashlib.sha256(review.read_bytes()).hexdigest()
    payload = {
        "review_path": str(review),
        "review_sha256": review_sha256,
        "session_id": session_id,
        "created_at": timestamp,
        "created_at_iso": dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(),
        "ttl_hours": 0 if active_ttl is None else active_ttl,
    }

    ensure_private_directory(state_dir)
    pending_path = Path(state_dir) / f"auto_review_pending_{session_id}.json"
    with pending_lock(pending_path):
        fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, pending_path)
            fsync_directory(pending_path.parent)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return str(pending_path)


def _read_pending(path: Path) -> tuple[dict[str, object], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("pending marker must contain a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def pending_status(
    pending_path: str,
    *,
    expected_session_id: str | None = None,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Return status for a pending marker without mutating it."""
    path = Path(pending_path)
    if not path.exists():
        return {
            "exists": False,
            "deliverable": False,
            "expired": False,
            "reason": "missing",
            "path": str(path),
        }

    current_time = time.time() if now is None else now
    try:
        pending, marker_sha256 = _read_pending(path)
    except Exception as exc:
        return {
            "exists": True,
            "deliverable": False,
            "expired": False,
            "reason": "invalid_marker",
            "error": str(exc),
            "path": str(path),
        }

    marker_session_id = str(pending.get("session_id") or "").strip()
    if expected_session_id is not None and marker_session_id != expected_session_id:
        return {
            "exists": True,
            "deliverable": False,
            "expired": False,
            "reason": "session_mismatch",
            "error": (
                f"pending marker session {marker_session_id!r} does not match "
                f"expected session {expected_session_id!r}"
            ),
            "path": str(path),
            "pending": pending,
        }

    created_at = _coerce_float(pending.get("created_at"))
    created_at_source = "created_at"
    if created_at is None:
        created_at = path.stat().st_mtime
        created_at_source = "mtime"

    ttl_hours = configured_ttl_hours(env=env, pending=pending)
    ttl_seconds = None if ttl_hours is None else ttl_hours * 3600
    age_seconds = max(0.0, current_time - created_at)
    expired = False if ttl_seconds is None else age_seconds > ttl_seconds

    common = {
        "exists": True,
        "path": str(path),
        "pending": pending,
        "marker_sha256": marker_sha256,
        "created_at": created_at,
        "created_at_source": created_at_source,
        "age_seconds": age_seconds,
        "ttl_hours": ttl_hours,
        "ttl_seconds": ttl_seconds,
    }
    if expired:
        return {
            **common,
            "deliverable": False,
            "expired": True,
            "reason": "expired",
        }

    review_value = str(pending.get("review_path") or "").strip()
    if not review_value:
        return {
            **common,
            "deliverable": False,
            "expired": False,
            "reason": "invalid_marker",
            "error": "pending marker has no review_path",
        }
    review_path = Path(review_value).expanduser()
    if not review_path.is_absolute():
        review_path = (path.parent / review_path).resolve()
    if not review_path.is_file():
        return {
            **common,
            "deliverable": False,
            "expired": False,
            "reason": "missing_review",
            "error": f"review file is missing: {review_path}",
            "review_path": str(review_path),
        }

    expected_review_sha256 = str(pending.get("review_sha256") or "").strip().lower()
    if len(expected_review_sha256) != 64:
        return {
            **common,
            "deliverable": False,
            "expired": False,
            "reason": "invalid_marker",
            "error": "pending marker has no valid review_sha256",
            "review_path": str(review_path),
        }
    actual_review_sha256 = hashlib.sha256(review_path.read_bytes()).hexdigest()
    if actual_review_sha256 != expected_review_sha256:
        return {
            **common,
            "deliverable": False,
            "expired": False,
            "reason": "artifact_mismatch",
            "error": "pending review bytes no longer match the authorized artifact",
            "review_path": str(review_path),
            "review_sha256": actual_review_sha256,
        }
    try:
        artifact_session_id = review_artifact_session_id(review_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {
            **common,
            "deliverable": False,
            "expired": False,
            "reason": "artifact_session_mismatch",
            "error": str(exc),
            "review_path": str(review_path),
            "review_sha256": actual_review_sha256,
        }
    if artifact_session_id != marker_session_id:
        return {
            **common,
            "deliverable": False,
            "expired": False,
            "reason": "artifact_session_mismatch",
            "error": (
                f"review artifact session {artifact_session_id!r} does not match "
                f"marker session {marker_session_id!r}"
            ),
            "review_path": str(review_path),
            "review_sha256": actual_review_sha256,
        }

    return {
        **common,
        "deliverable": not expired,
        "expired": expired,
        "reason": "expired" if expired else "fresh",
        "review_path": str(review_path),
        "review_sha256": actual_review_sha256,
    }


def read_deliverable_review(
    pending_path: str,
    *,
    expected_session_id: str,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> tuple[dict[str, object], str]:
    """Return the exact authorized review content for one session."""
    status = pending_status(
        pending_path,
        expected_session_id=expected_session_id,
        env=env,
        now=now,
    )
    if not status.get("deliverable"):
        return status, ""
    review_path = Path(str(status["review_path"]))
    review_bytes = review_path.read_bytes()
    actual_hash = hashlib.sha256(review_bytes).hexdigest()
    if actual_hash != status.get("review_sha256"):
        return {
            **status,
            "deliverable": False,
            "reason": "artifact_mismatch",
            "error": "pending review changed while it was being read",
        }, ""
    return status, review_bytes.decode("utf-8")


def delivery_receipt_path(state_dir: str, session_id: str) -> Path:
    session_id = require_valid_session_id(session_id)
    return Path(state_dir) / f"auto_review_delivered_{session_id}.json"


def acknowledge_pending_delivery(
    *,
    state_dir: str,
    pending_path: str,
    session_id: str,
    expected_marker_sha256: str,
    now: float | None = None,
) -> dict[str, object]:
    """Record exact delivery and remove only the marker that was displayed."""
    session_id = require_valid_session_id(session_id)
    path = Path(pending_path)
    with pending_lock(path):
        status = pending_status(pending_path, expected_session_id=session_id, now=now)
        if not status.get("deliverable"):
            return {**status, "acknowledged": False}
        if status.get("marker_sha256") != expected_marker_sha256:
            return {
                **status,
                "acknowledged": False,
                "reason": "marker_replaced",
            }

        receipt_path = delivery_receipt_path(state_dir, session_id)
        ensure_private_directory(receipt_path.parent)
        # One atomic rename both removes the pending marker and leaves durable
        # delivery evidence. The marker payload itself is a valid minimal
        # receipt if the process stops before the richer metadata rewrite.
        os.replace(path, receipt_path)
        fsync_directory(receipt_path.parent)

        timestamp = time.time() if now is None else now
        receipt = {
            "session_id": session_id,
            "review_path": status["review_path"],
            "review_sha256": status["review_sha256"],
            "marker_sha256": status["marker_sha256"],
            "delivered_at": timestamp,
            "delivered_at_iso": dt.datetime.fromtimestamp(
                timestamp, dt.timezone.utc
            ).isoformat(),
        }
        fd, tmp = tempfile.mkstemp(dir=str(receipt_path.parent), suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(receipt, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, receipt_path)
            fsync_directory(receipt_path.parent)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return {
            **status,
            "acknowledged": True,
            "receipt_path": str(receipt_path),
        }


def delivery_receipt_matches(
    *,
    state_dir: str,
    session_id: str,
    review_path: str,
    review_sha256: str,
) -> bool:
    receipt_path = delivery_receipt_path(state_dir, session_id)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    return (
        receipt.get("session_id") == session_id
        and receipt.get("review_path") == str(Path(review_path).expanduser().resolve())
        and receipt.get("review_sha256") == review_sha256
    )


def cleanup_expired_pending(
    pending_path: str,
    *,
    expected_session_id: str | None = None,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Remove expired pending markers, preserving review artifacts."""
    path = Path(pending_path)
    with pending_lock(path):
        status = pending_status(
            pending_path,
            expected_session_id=expected_session_id,
            env=env,
            now=now,
        )
        if status.get("expired") and status.get("reason") == "expired":
            quarantine = path.with_name(
                f".{path.name}.expired.{os.getpid()}.{time.time_ns()}"
            )
            try:
                os.replace(path, quarantine)
                fsync_directory(path.parent)
                moved_hash = hashlib.sha256(quarantine.read_bytes()).hexdigest()
                if moved_hash != status.get("marker_sha256"):
                    if not path.exists():
                        os.replace(quarantine, path)
                        fsync_directory(path.parent)
                    else:
                        quarantine.unlink(missing_ok=True)
                        fsync_directory(path.parent)
                    status["removed"] = False
                    status["reason"] = "marker_replaced"
                else:
                    quarantine.unlink()
                    fsync_directory(path.parent)
                    status["removed"] = True
            except FileNotFoundError:
                status["removed"] = False
                status["reason"] = "marker_replaced"
            except Exception as exc:
                status["removed"] = False
                status["remove_error"] = str(exc)
                if quarantine.exists() and not path.exists():
                    os.replace(quarantine, path)
                    fsync_directory(path.parent)
        else:
            status["removed"] = False
        return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    acknowledge = subparsers.add_parser("acknowledge")
    acknowledge.add_argument("--state-dir", required=True)
    acknowledge.add_argument("--pending-path", required=True)
    acknowledge.add_argument("--session-id", required=True)
    acknowledge.add_argument("--expected-marker-sha256", required=True)
    args = parser.parse_args()
    if args.command == "acknowledge":
        result = acknowledge_pending_delivery(
            state_dir=args.state_dir,
            pending_path=args.pending_path,
            session_id=args.session_id,
            expected_marker_sha256=args.expected_marker_sha256,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("acknowledged") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
