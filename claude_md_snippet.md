<!-- OVERWATCH:BEGIN -->
## Overwatch System (Independent Reviewer)

An independent AI reviewer that monitors your coding sessions and provides periodic code review and architectural assessment. Installed at: `{{OVERWATCH_DIR}}`.

**Core Rule: Overwatch is an independent external system, NOT you (the Builder). You must never substitute your own review for Overwatch's output.**

**How reviews reach you**:
- **Auto-reviews**: Delivered directly into your context as `<system-reminder>` containing `[Overwatch Auto-Review]`. No action needed to receive them.
- **Manual triggers**: User sends "overwatch", "second opinion", or "第二意见" → instructions arrive as `<system-reminder>` containing `[Overwatch Manual Trigger]` with the command to run.

**When you receive an Overwatch review** (via any delivery method):
- You MUST: **Present the full review text verbatim (no rewriting, omitting, or merging) -> separator line -> respond to each point with your judgment or action plan**
- Auto-review results must also be **proactively presented and responded to** -- handle the review before continuing the user's current request
- **Do NOT** generate your own review to substitute for Overwatch output
- **`[LESSON]` tag rule**: If the review contains `[LESSON]` tags, you MUST save that lesson as project memory (feedback type). This is mandatory, not a suggestion. Follow your own memory management rules for storage location and scope. If no specific memory rules are configured, default to creating a feedback file in the current project's `memory/` directory.

**Manual trigger execution** (follow the command in `[Overwatch Manual Trigger]`, or use this template):
```bash
python3 {{OVERWATCH_DIR}}/overwatch.py --session-id "<SID>" --transcript "<PATH>" --cwd "$(pwd)" --force 2>&1
```
Then read the review: `bash {{OVERWATCH_DIR}}/hooks/find_review.sh "$(pwd)"` to get the file path, and present the content.

**After presenting any review**, clean up the trigger file:
```bash
rm -f {{OVERWATCH_DIR}}/state/latest_trigger.json
```

**Fallback** (if user triggers a review but no `[Overwatch]` content appears in your context):

Step 1 — Check trigger file:
```bash
cat {{OVERWATCH_DIR}}/state/latest_trigger.json 2>/dev/null
```
If it exists: `type: "auto_review"` → read `review_path` and present; `type: "manual_trigger"` → run the review command above with `session_id` and `transcript_path` from the file.

Step 2 — If no trigger file, try `find_session.sh` (last resort):
```bash
read SID TRANSCRIPT <<< $(bash {{OVERWATCH_DIR}}/hooks/find_session.sh)
```
If both `SID` and `TRANSCRIPT` are non-empty, run the review command with those values. If empty, the session is too new — tell the user: "Overwatch needs a few turns of conversation before it can review. Continue working and try again shortly."

**Important**: Prefer `session_id` and `transcript_path` from `additionalContext` or trigger file over `find_session.sh` — multiple concurrent sessions may exist.
<!-- OVERWATCH:END -->
