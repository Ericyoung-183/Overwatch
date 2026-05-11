# Overwatch Backlog

## Needs Product Decision

- Pending auto-review expiry policy: decide whether `auto_review_pending_<session>.json` should expire after a time window or remain deliverable until the next prompt. Current behavior preserves undelivered reviews indefinitely, which avoids silent loss but can surface stale review context if a session is resumed days later. Decision trigger: before changing pending-marker cleanup semantics.
