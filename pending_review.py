"""Pending auto-review lifecycle helpers."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Mapping


DEFAULT_PENDING_TTL_HOURS = 72.0
TTL_ENV_KEY = "OVERWATCH_PENDING_TTL_HOURS"


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


def write_pending_marker(
    *,
    state_dir: str,
    session_id: str,
    review_path: str,
    now: float | None = None,
    ttl_hours: float | None = None,
) -> str:
    """Write a pending auto-review marker and return its path."""
    timestamp = time.time() if now is None else now
    active_ttl = configured_ttl_hours() if ttl_hours is None else ttl_hours
    payload = {
        "review_path": review_path,
        "session_id": session_id,
        "created_at": timestamp,
        "created_at_iso": dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(),
        "ttl_hours": 0 if active_ttl is None else active_ttl,
    }

    Path(state_dir).mkdir(parents=True, exist_ok=True)
    pending_path = Path(state_dir) / f"auto_review_pending_{session_id}.json"
    fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, pending_path)
    return str(pending_path)


def _read_pending(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("pending marker must contain a JSON object")
    return payload


def pending_status(
    pending_path: str,
    *,
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
        pending = _read_pending(path)
    except Exception as exc:
        return {
            "exists": True,
            "deliverable": False,
            "expired": True,
            "reason": "invalid_marker",
            "error": str(exc),
            "path": str(path),
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

    return {
        "exists": True,
        "deliverable": not expired,
        "expired": expired,
        "reason": "expired" if expired else "fresh",
        "path": str(path),
        "pending": pending,
        "created_at": created_at,
        "created_at_source": created_at_source,
        "age_seconds": age_seconds,
        "ttl_hours": ttl_hours,
        "ttl_seconds": ttl_seconds,
    }


def cleanup_expired_pending(
    pending_path: str,
    *,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Remove expired pending markers, preserving review artifacts."""
    status = pending_status(pending_path, env=env, now=now)
    if status.get("expired"):
        try:
            Path(pending_path).unlink(missing_ok=True)
            status["removed"] = True
        except Exception as exc:
            status["removed"] = False
            status["remove_error"] = str(exc)
    else:
        status["removed"] = False
    return status
