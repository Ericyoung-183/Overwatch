<!-- OVERWATCH:BEGIN -->
## Overwatch System (Independent Reviewer)

An independent AI reviewer that monitors your coding sessions and provides periodic code review and architectural assessment. Installed at: `{{OVERWATCH_DIR}}`.

**Core Rule: Overwatch is an independent external system, NOT you (the Builder). You must never substitute your own review for Overwatch's output.**

**How reviews reach you**:
- **Auto-reviews**: Delivered directly into your context as `<system-reminder>` containing `[Overwatch Auto-Review]`. No action needed to receive them.
- **Manual triggers**: User sends "overwatch", "second opinion", or "第二意见" → instructions arrive as `<system-reminder>` containing `[Overwatch Manual Trigger]` with the command to run.

**When you receive an Overwatch review** (via any delivery method):
{{REVIEW_RESPONSE_PROTOCOL}}
- Auto-review results must also be **proactively presented and responded to** -- handle the review before continuing the user's current request
- **Do NOT** generate your own review to substitute for Overwatch output
- **`[LESSON]` tag rule**: If the review contains `[LESSON]` tags, you MUST save that lesson as project memory (feedback type). This is mandatory, not a suggestion. Follow your own memory management rules for storage location and scope. If no specific memory rules are configured, default to creating a feedback file in the current project's `memory/` directory.

**Manual trigger execution** (follow the command in `[Overwatch Manual Trigger]`, or use this template):
```bash
bash {{OVERWATCH_DIR}}/hooks/run_manual_review.sh --session-id "<SID>" --transcript "<PATH>" --cwd "$(pwd)"
```
The command streams the exact hash-verified review bytes for the same native transcript session. Present that output; if the command fails, do not fall back to an older `latest.md`. Use the exact cleanup command supplied in the trigger context because trigger files are session-bound.

**Fallback** (if user triggers a review but no `[Overwatch]` content appears in your context):

Step 1 — Resolve the current session without guessing:
```bash
SESSION_JSON="$(bash {{OVERWATCH_DIR}}/hooks/find_session.sh --json "$(pwd)")"
SID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])' <<< "$SESSION_JSON")"
TRANSCRIPT="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["transcript_path"])' <<< "$SESSION_JSON")"
```
If this reports multiple sessions, stop and ask for the exact task instead of using another session's review.

Step 2 — Only after a unique `SID` is resolved, inspect that session's trigger:
```bash
cat "{{OVERWATCH_DIR}}/state/triggers/${SID}.json" 2>/dev/null
```
If it is `auto_review`, do not reopen `review_path` directly. Stream the exact hash-verified bytes:
```bash
python3 "{{OVERWATCH_DIR}}/trigger_state.py" read-auto-review --state-dir "${OVERWATCH_STATE_DIR:-{{OVERWATCH_DIR}}/state}" --session-id "$SID"
```
Present only that command's successful output. If it is `manual_trigger`, run the wrapper above with its exact `session_id`, `transcript_path`, and `cwd`. If no unique session or trigger exists, tell the user that Overwatch needs a few more turns before review.

**Important**: Prefer `session_id` and `transcript_path` from `additionalContext`. Never inspect a global latest trigger; multiple concurrent sessions may exist.
<!-- OVERWATCH:END -->
