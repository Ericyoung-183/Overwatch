# Overwatch Backlog

## Recently Resolved

- Pending auto-review expiry policy: implemented default 72-hour TTL for `auto_review_pending_<session>.json`. Expiry removes only the delivery marker, keeps the saved review artifact, and lets Stop hooks continue normal review scheduling instead of being blocked by stale pending state.

## Needs Product Decision

- None.
