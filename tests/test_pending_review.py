#!/usr/bin/env python3
"""Regression tests for pending auto-review lifecycle handling."""

from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pending_review  # noqa: E402
from pending_review import (  # noqa: E402
    acknowledge_pending_delivery,
    cleanup_expired_pending,
    delivery_receipt_matches,
    read_deliverable_review,
    write_pending_marker,
)
from runtime_fs import project_identity_sha256  # noqa: E402


PROJECT_ROOT = "/tmp/project"


def project_fields() -> dict[str, str]:
    return {
        "project_root": PROJECT_ROOT,
        "project_sha256": project_identity_sha256(PROJECT_ROOT),
    }


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_review(path: Path, session_id: str, body: str = "review body") -> None:
    path.write_text(
        f"<!-- Overwatch Review #1 | 2026-07-18 10:00:00 | session: {session_id} | "
        f"project-sha256: {project_identity_sha256(PROJECT_ROOT)} | project: {PROJECT_ROOT} -->\n"
        "<!-- META_END -->\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def test_write_pending_marker_records_lifecycle_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        review = state_dir / "review.md"
        write_review(review, "pending-lifecycle")
        path = write_pending_marker(
            state_dir=str(state_dir),
            session_id="pending-lifecycle",
            project_root=PROJECT_ROOT,
            review_path=str(review),
            now=1_700_000_000,
            ttl_hours=72,
        )
        payload = read_json(Path(path))

    test("pending marker records review path", payload.get("review_path") == str(review.resolve()), str(payload))
    test("pending marker records review hash", len(str(payload.get("review_sha256") or "")) == 64, str(payload))
    test("pending marker records session id", payload.get("session_id") == "pending-lifecycle", str(payload))
    test("pending marker records created_at", payload.get("created_at") == 1_700_000_000, str(payload))
    test("pending marker records ttl hours", payload.get("ttl_hours") == 72, str(payload))
    test("pending marker records iso timestamp", "created_at_iso" in payload, str(payload))


def test_fresh_pending_remains_deliverable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "fresh")
        marker = Path(write_pending_marker(
            state_dir=tmp,
            session_id="fresh",
            project_root=PROJECT_ROOT,
            review_path=str(review),
            now=1_700_000_000,
            ttl_hours=72,
        ))
        status = cleanup_expired_pending(
            str(marker),
            expected_session_id="fresh",
            now=1_700_000_000 + 3600,
        )

        test("fresh pending is deliverable", bool(status.get("deliverable")), str(status))
        test("fresh pending is not expired", not status.get("expired"), str(status))
        test("fresh pending remains on disk", marker.exists(), str(status))


def test_expired_pending_is_removed_but_review_is_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        review.write_text("review body", encoding="utf-8")
        marker = Path(tmp) / "auto_review_pending_expired.json"
        marker.write_text(
            json.dumps({"review_path": str(review), "session_id": "expired", "created_at": 1_700_000_000, **project_fields()}),
            encoding="utf-8",
        )
        status = cleanup_expired_pending(str(marker), now=1_700_000_000 + 73 * 3600)

        test("expired pending is not deliverable", not status.get("deliverable"), str(status))
        test("expired pending is marked expired", bool(status.get("expired")), str(status))
        test("expired pending marker is removed", not marker.exists(), str(status))
        test("expired pending keeps review file", review.exists(), str(status))


def test_legacy_pending_uses_file_mtime_for_expiry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "no-expiry")
        marker = Path(tmp) / "auto_review_pending_legacy.json"
        marker.write_text(
            json.dumps({"review_path": str(review), "session_id": "legacy", **project_fields()}),
            encoding="utf-8",
        )
        old_time = 1_700_000_000
        os.utime(marker, (old_time, old_time))
        status = cleanup_expired_pending(str(marker), now=old_time + 73 * 3600)

    test("legacy pending without created_at expires by mtime", bool(status.get("expired")), str(status))
    test("legacy pending marker is removed", not marker.exists(), str(status))


def test_zero_ttl_disables_expiry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "no-expiry")
        marker = Path(write_pending_marker(
            state_dir=tmp,
            session_id="no-expiry",
            project_root=PROJECT_ROOT,
            review_path=str(review),
            now=1_700_000_000,
            ttl_hours=0,
        ))
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
            review = Path(tmp) / "review.md"
            write_review(review, "pending-no-expiry")
            path = write_pending_marker(
                state_dir=tmp,
                session_id="pending-no-expiry",
                project_root=PROJECT_ROOT,
                review_path=str(review),
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


def test_invalid_pending_marker_is_preserved_for_diagnosis() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_invalid.json"
        marker.write_text("{broken", encoding="utf-8")

        status = cleanup_expired_pending(str(marker), now=1_700_000_000)

        test("invalid marker is not deliverable", not status.get("deliverable"), str(status))
        test("invalid marker is not mislabeled expired", not status.get("expired"), str(status))
        test("invalid marker remains for diagnosis", marker.exists(), str(status))


def test_pending_delivery_binds_session_and_exact_review_bytes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "session-a", "authorized review")
        marker = Path(write_pending_marker(
            state_dir=tmp,
            session_id="session-a",
            project_root=PROJECT_ROOT,
            review_path=str(review),
            now=1_700_000_000,
            ttl_hours=72,
        ))

        wrong_session, wrong_content = read_deliverable_review(
            str(marker),
            expected_session_id="session-b",
            expected_project_root=PROJECT_ROOT,
            now=1_700_000_000 + 60,
        )
        write_review(review, "session-a", "replaced review")
        replaced, replaced_content = read_deliverable_review(
            str(marker),
            expected_session_id="session-a",
            expected_project_root=PROJECT_ROOT,
            now=1_700_000_000 + 60,
        )

    test("wrong session is rejected", wrong_session.get("reason") == "session_mismatch", str(wrong_session))
    test("wrong session yields no content", wrong_content == "", wrong_content)
    test("replaced review is rejected", replaced.get("reason") == "artifact_mismatch", str(replaced))
    test("replaced review yields no content", replaced_content == "", replaced_content)


def test_delivery_acknowledgement_is_hash_bound_and_leaves_receipt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "delivery-receipt", "delivered review")
        marker = Path(
            write_pending_marker(
                state_dir=tmp,
                session_id="delivery-receipt",
                project_root=PROJECT_ROOT,
                review_path=str(review),
            )
        )
        marker_hash = pending_review.pending_status(
            str(marker), expected_session_id="delivery-receipt", expected_project_root=PROJECT_ROOT
        )["marker_sha256"]
        wrong = acknowledge_pending_delivery(
            state_dir=tmp,
            pending_path=str(marker),
            session_id="delivery-receipt",
            project_root=PROJECT_ROOT,
            expected_marker_sha256="0" * 64,
        )
        preserved_after_wrong = marker.exists()
        acknowledged = acknowledge_pending_delivery(
            state_dir=tmp,
            pending_path=str(marker),
            session_id="delivery-receipt",
            project_root=PROJECT_ROOT,
            expected_marker_sha256=str(marker_hash),
        )
        receipt_matches = delivery_receipt_matches(
            state_dir=tmp,
            session_id="delivery-receipt",
            project_root=PROJECT_ROOT,
            review_path=str(review),
            review_sha256=str(acknowledged["review_sha256"]),
        )
        removed_after_ack = not marker.exists()

    test("delivery acknowledgement rejects a replaced marker", wrong.get("reason") == "marker_replaced", str(wrong))
    test("wrong marker hash preserves pending delivery", preserved_after_wrong, str(wrong))
    test("exact delivery acknowledgement succeeds", acknowledged.get("acknowledged") is True, str(acknowledged))
    test("delivery acknowledgement removes exact marker", removed_after_ack, str(acknowledged))
    test("delivery receipt binds the exact review", receipt_matches, str(acknowledged))


def test_pending_marker_rejects_cross_session_review_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "session-b")
        try:
            write_pending_marker(
                state_dir=tmp,
                session_id="session-a",
                project_root=PROJECT_ROOT,
                review_path=str(review),
            )
        except ValueError as exc:
            error = str(exc)
        else:
            error = ""

        marker = Path(
            write_pending_marker(
                state_dir=tmp,
                session_id="session-b",
                project_root=PROJECT_ROOT,
                review_path=str(review),
            )
        )
        payload = read_json(marker)
        payload["session_id"] = "session-a"
        marker.write_text(json.dumps(payload), encoding="utf-8")
        status = pending_review.pending_status(
            str(marker), expected_session_id="session-a", expected_project_root=PROJECT_ROOT
        )

    test("writer rejects review metadata from another session", "does not match marker session" in error, error)
    test("reader rejects a marker rebound to another session", status.get("reason") == "artifact_session_mismatch", str(status))


def test_delivery_ack_atomic_rename_survives_receipt_enrichment_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        write_review(review, "atomic-receipt")
        marker = Path(
            write_pending_marker(
                state_dir=tmp,
                session_id="atomic-receipt",
                project_root=PROJECT_ROOT,
                review_path=str(review),
            )
        )
        marker_hash = pending_review.pending_status(
            str(marker), expected_session_id="atomic-receipt", expected_project_root=PROJECT_ROOT
        )["marker_sha256"]

        with mock.patch.object(
            pending_review.tempfile,
            "mkstemp",
            side_effect=OSError("fault after atomic receipt rename"),
        ):
            try:
                acknowledge_pending_delivery(
                    state_dir=tmp,
                    pending_path=str(marker),
                    session_id="atomic-receipt",
                    project_root=PROJECT_ROOT,
                    expected_marker_sha256=str(marker_hash),
                )
            except OSError as exc:
                error = str(exc)
            else:
                error = ""

        receipt_matches = delivery_receipt_matches(
            state_dir=tmp,
            session_id="atomic-receipt",
            project_root=PROJECT_ROOT,
            review_path=str(review),
            review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
        )
        marker_removed = not marker.exists()

    test("receipt enrichment fault is injected", "fault after atomic receipt rename" in error, error)
    test("atomic acknowledgement removes pending marker", marker_removed, str(marker))
    test("minimal renamed receipt still proves exact delivery", receipt_matches)


def test_pending_writer_rejects_unsafe_session_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        review = Path(tmp) / "review.md"
        review.write_text("review", encoding="utf-8")
        try:
            write_pending_marker(
                state_dir=tmp,
                session_id="../escape",
                project_root=PROJECT_ROOT,
                review_path=str(review),
            )
        except ValueError as exc:
            error = str(exc)
        else:
            error = ""

    test("pending writer rejects unsafe session id", "invalid Overwatch session ID" in error, error)


def test_missing_review_file_preserves_pending_marker_for_retry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_missing_review.json"
        review = Path(tmp) / "not-created.md"
        marker.write_text(
            json.dumps({"review_path": str(review), "session_id": "missing-review", "created_at": 1_700_000_000, **project_fields()}),
            encoding="utf-8",
        )

        status = cleanup_expired_pending(str(marker), now=1_700_000_000 + 3600)

        test("missing review is not deliverable", not status.get("deliverable"), str(status))
        test("missing review is not mislabeled expired", not status.get("expired"), str(status))
        test("missing review reason is explicit", status.get("reason") == "missing_review", str(status))
        test("missing review marker remains for retry", marker.exists(), str(status))


def test_expired_missing_review_marker_is_removed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_expired_missing_review.json"
        review = Path(tmp) / "never-created.md"
        marker.write_text(
            json.dumps(
                {
                    "review_path": str(review),
                    "session_id": "expired-missing-review",
                    "created_at": 1_700_000_000,
                    **project_fields(),
                }
            ),
            encoding="utf-8",
        )

        status = cleanup_expired_pending(str(marker), now=1_700_000_000 + 73 * 3600)

        test("expired missing review is classified by age", status.get("reason") == "expired", str(status))
        test("expired missing review is marked expired", bool(status.get("expired")), str(status))
        test("expired missing review marker is removed", not marker.exists(), str(status))


def test_cleanup_preserves_marker_replaced_after_expiry_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "auto_review_pending_replaced.json"
        marker.write_text(
            json.dumps(
                {
                    "review_path": str(Path(tmp) / "old-review.md"),
                    "session_id": "old-session",
                    "created_at": 1_700_000_000,
                    **project_fields(),
                }
            ),
            encoding="utf-8",
        )
        replacement = {
            "review_path": str(Path(tmp) / "new-review.md"),
            "session_id": "new-session",
            "created_at": 1_800_000_000,
            **project_fields(),
        }
        original_status = pending_review.pending_status

        def replace_after_status(*args, **kwargs):
            status = original_status(*args, **kwargs)
            marker.write_text(json.dumps(replacement), encoding="utf-8")
            return status

        with mock.patch.object(
            pending_review, "pending_status", side_effect=replace_after_status
        ):
            status = cleanup_expired_pending(
                str(marker), now=1_700_000_000 + 73 * 3600
            )
        preserved = read_json(marker)

    test("cleanup reports marker replacement race", status.get("reason") == "marker_replaced", str(status))
    test("cleanup does not remove replacement marker", not status.get("removed"), str(status))
    test("cleanup restores exact replacement payload", preserved == replacement, str(preserved))


if __name__ == "__main__":
    test_write_pending_marker_records_lifecycle_metadata()
    test_fresh_pending_remains_deliverable()
    test_expired_pending_is_removed_but_review_is_preserved()
    test_legacy_pending_uses_file_mtime_for_expiry()
    test_zero_ttl_disables_expiry()
    test_write_pending_marker_preserves_disabled_expiry_policy()
    test_invalid_pending_marker_is_preserved_for_diagnosis()
    test_pending_delivery_binds_session_and_exact_review_bytes()
    test_delivery_acknowledgement_is_hash_bound_and_leaves_receipt()
    test_pending_marker_rejects_cross_session_review_metadata()
    test_delivery_ack_atomic_rename_survives_receipt_enrichment_failure()
    test_pending_writer_rejects_unsafe_session_id()
    test_missing_review_file_preserves_pending_marker_for_retry()
    test_expired_missing_review_marker_is_removed()
    test_cleanup_preserves_marker_replaced_after_expiry_check()
    print("pending review lifecycle tests passed")
