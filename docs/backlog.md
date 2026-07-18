# Overwatch Backlog

## Recently Resolved

- Anchor capture enforcement: prompt hooks persist a session/cwd-bound two-signal root/child/interrupt candidate, including inline numbered lists, until exact tracker capture or an audited reason-bearing dismissal.
- Review visibility acknowledgement: pending review markers remain retryable until the Builder presents the review and runs the exact session/hash-bound acknowledgement command.
- Installer CAS: Codex/Claude install and shared uninstall refuse to overwrite configuration bytes changed after preflight.
- Reviewer tool and archive safety: option-like Git refs, external diff drivers, and symlink-directed review history writes fail closed.
- Directory durability and uninstall transaction: state/marker/receipt/manual-result replacements fsync parent directories, and Claude/Codex/CLAUDE.md removal commits or rolls back together.
- CLAUDE ownership safety: install and uninstall modify only one complete `OVERWATCH:BEGIN/END` section, reject incomplete or duplicate markers, and preserve unmarked lookalike headings.
- Review privacy and retry: review directories/files use `0700`/`0600`, and failed review attempts retain the prior `last_reviewed_turn` so the same transcript can retry after cooldown.
- Pending cleanup concurrency: expiry cleanup compare-and-swaps the inspected marker bytes and cannot delete a freshly replaced marker.
- Complete mutation gate: release immutability snapshots include Git-ignored candidate files as well as tracked and untracked files, with explicit exclusions only for known runtime and cache artifacts.
- Pending auto-review expiry policy: implemented default 72-hour TTL for `auto_review_pending_<session>.json`. Fresh markers with a missing review remain available for retry; once expired, even a missing-review marker is removed so manual and automatic review scheduling can continue. Saved review artifacts are never deleted by marker cleanup.
- Pending-review evidence parity: Claude Prompt and Stop hooks now distinguish expired, missing-review, and invalid-marker states; fresh broken evidence is shown to the Builder and preserved for diagnosis.
- Transcript evidence parity: current Codex custom tool calls/outputs and Anchor developer context, plus Claude block text/images/tool results, are preserved by release-gated adapters.
- Manual-review identity: each trigger uses a unique success-only result file bound to an immutable numbered review plus SHA-256 and exact session; verification streams the exact bytes it hashed, so a failed, replaced, or crossed run cannot surface stale review output.
- Installer relocation: Codex and Claude installers replace stale/duplicate managed Hook paths, survive spaces and single quotes, honor custom Claude settings as the only config authority, and preserve unrelated Hooks.
- Runtime privacy boundary: project allowlists use exact-or-descendant path semantics, and transcript-native session identity is verified before review dispatch.
- Release isolation: Hook and installer integration tests use temporary state/review directories and cannot restore stale snapshots over live runtime files.
- Manual allowlist enforcement: both prompt hooks and the engine reject manual review outside exact-or-descendant project roots.
- Pending artifact binding: delivery markers bind the session ID and exact review SHA-256, and hooks deliver only bytes that pass both checks.
- Symmetric uninstall: one uninstaller atomically removes exact current-install Claude Code and Codex hooks while preserving unrelated and similarly named foreign hooks.
- Status relay ownership: fixed-file Codex relays must carry the exact session ID, preventing one concurrent task from consuming another task's Stop status.
- Session isolation: triggers are session-bound, and a locked multi-session registry preserves concurrent tasks in the same project while ambiguous fallback lookup fails closed.
- Crash safety: session state uses atomic replacement, numbered review history refuses overwrite, and stable OS locks distinguish active work from a harmless residual lock file.
- Review delivery transaction: implemented a persisted delivery intent plus exact-marker delivery receipt, so archive/marker/state interruptions recover without replaying the review backend.
- Candidate immutability: the release gate isolates Python bytecode and compares candidate content hashes plus file modes before and after all checks.
- Runtime path keys: session IDs are validated before any state/review path construction, manual result files stay inside the managed state directory, and review lookup uses path boundaries plus review metadata.
- Fallback artifact binding: session auto-review triggers retain the authorized SHA-256, and the fallback reader streams the same verified bytes while rejecting a replaced review file.

## Needs Product Decision

- None.
