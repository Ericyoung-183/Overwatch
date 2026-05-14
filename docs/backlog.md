# Overwatch Backlog

## Needs Product Decision

- Pending auto-review expiry policy: decide whether `auto_review_pending_<session>.json` should expire after a time window or remain deliverable until the next prompt. Current behavior preserves undelivered reviews indefinitely, which avoids silent loss but can surface stale review context if a session is resumed days later. Decision trigger: before changing pending-marker cleanup semantics.

## Remote Sync

- Push local Overwatch product commits to GitHub after explicit user approval. Owner: Codex with Eric approval. Blocking reason: remote push requires explicit approval. Risk: local Overwatch product behavior can drift from GitHub `main`. Trigger/checkpoint: before the next public release or when Eric says to push.
