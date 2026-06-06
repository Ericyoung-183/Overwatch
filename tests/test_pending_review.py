#!/usr/bin/env python3
"""Regression tests for pending auto-review lifecycle handling."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pending_review import cleanup_expired_pending, write_pending_marker  # noqa: E402


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_write_pending_marker_records_lifecycle_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        path = write_pending_marker(
            state_dir=str(state_dir),
            session_id="pending-lifecycle",
            review_path="/tmp/review.md",
            now=1_700_000_000,
            ttl_hours=72,
        )
        payload = read_json(Path(path))

    test("pending marker records review path", payload.get("review_path") == "/tmp/review.md", str(payload))
    test("pending marker records session id", payload.get("session_id") == "pending-lifecycle", str(payload))
    test("pending marker records created_at", payload.get("created_at") == 1_700_000_000, str(payload))
    test("pending marker records ttl hours", payload.get("ttl_hours") == 72, str(payload))
    test("pending marker records iso timestamp", "created_at_iso" in payload, str(payload))


def test_fresh_pending_remains_deliverable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_fresh.json"
        marker.write_text(
            json.dumps({"review_path": "/tmp/review.md", "session_id": "fresh", "created_at": 1_700_000_000}),
            encoding="utf-8",
        )
        status = cleanup_expired_pending(str(marker), now=1_700_000_000 + 3600)

        test("fresh pending is deliverable", bool(status.get("deliverable")), str(status))
        test("fresh pending is not expired", not status.get("expired"), str(status))
        test("fresh pending remains on disk", marker.exists(), str(status))


def test_expired_pending_is_removed_but_review_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        review.write_text("review body", encoding="utf-8")
        marker = Path(tmp) / "auto_review_pending_expired.json"
        marker.write_text(
            json.dumps({"review_path": str(review), "session_id": "expired", "created_at": 1_700_000_000}),
            encoding="utf-8",
        )
        status = cleanup_expired_pending(str(marker), now=1_700_000_000 + 73 * 3600)

        test("expired pending is not deliverable", not status.get("deliverable"), str(status))
        test("expired pending is marked expired", bool(status.get("expired")), str(status))
        test("expired pending marker is removed", not marker.exists(), str(status))
        test("expired pending keeps review file", review.exists(), str(status))


def test_legacy_pending_uses_file_mtime_for_expiry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_legacy.json"
        marker.write_text(
            json.dumps({"review_path": "/tmp/review.md", "session_id": "legacy"}),
            encoding="utf-8",
        )
        old_time = 1_700_000_000
        os.utime(marker, (old_time, old_time))
        status = cleanup_expired_pending(str(marker), now=old_time + 73 * 3600)

    test("legacy pending without created_at expires by mtime", bool(status.get("expired")), str(status))
    test("legacy pending marker is removed", not marker.exists(), str(status))


def test_zero_ttl_disables_expiry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_no_expiry.json"
        marker.write_text(
            json.dumps({"review_path": "/tmp/review.md", "session_id": "no-expiry", "created_at": 1_700_000_000}),
            encoding="utf-8",
        )
        status = cleanup_expired_pending(
            str(marker),
            env={"OVERWATCH_PENDING_TTL_HOURS": "0"},
            now=1_700_000_000 + 365 * 24 * 3600,
        )

        test("zero ttl keeps old pending deliverable", bool(status.get("deliverable")), str(status))
        test("zero ttl keeps marker on disk", marker.exists(), str(status))


def test_write_pending_marker_preserves_disabled_expiry_policy() -> None:
    old = os.environ.get("OVERWATCH_PENDING_TTL_HOURS")
    os.environ["OVERWATCH_PENDING_TTL_HOURS"] = "0"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_pending_marker(
                state_dir=tmp,
                session_id="pending-no-expiry",
                review_path="/tmp/review.md",
                now=1_700_000_000,
            )
            payload = read_json(Path(path))
            status = cleanup_expired_pending(str(path), now=1_700_000_000 + 365 * 24 * 3600)
    finally:
        if old is None:
            os.environ.pop("OVERWATCH_PENDING_TTL_HOURS", None)
        else:
            os.environ["OVERWATCH_PENDING_TTL_HOURS"] = old

    test("written disabled ttl is explicit zero", payload.get("ttl_hours") == 0, str(payload))
    test("written disabled ttl remains deliverable", bool(status.get("deliverable")), str(status))


if __name__ == "__main__":
    test_write_pending_marker_records_lifecycle_metadata()
    test_fresh_pending_remains_deliverable()
    test_expired_pending_is_removed_but_review_is_preserved()
    test_legacy_pending_uses_file_mtime_for_expiry()
    test_zero_ttl_disables_expiry()
    test_write_pending_marker_preserves_disabled_expiry_policy()
    print("pending review lifecycle tests passed")
