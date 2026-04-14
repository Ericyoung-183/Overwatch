<!-- OVERWATCH:BEGIN -->
## Overwatch System (Independent Reviewer)

An independent AI reviewer that monitors your coding sessions and provides periodic code review and architectural assessment. Installed at: `{{OVERWATCH_DIR}}`.

**Core Rule: Overwatch is an independent external system, NOT you (the Builder). You must never substitute your own review for Overwatch's output.**

**When you receive an Overwatch review** (via trigger file or systemMessage):
- You MUST: **Present the full review text verbatim (no rewriting, omitting, or merging) -> separator line -> respond to each point with your judgment or action plan**
- Auto-review results must also be **proactively presented and responded to** -- handle the review before continuing the user's current request
- **Do NOT** generate your own review to substitute for Overwatch output
- **`[LESSON]` tag rule**: If the review contains `[LESSON]` tags, you MUST save that lesson as project memory (feedback type). This is mandatory, not a suggestion. Follow your own memory management rules for storage location and scope. If no specific memory rules are configured, default to creating a feedback file in the current project's `memory/` directory.

**Trigger methods**:
- **Manual**: User sends "overwatch", "second opinion", or "第二意见" -> UserPromptSubmit Hook triggers
- **Auto**: Stop Hook triggers review every N turns, result delivered on next turn

**Unified execution flow after trigger**:
Both manual and auto reviews are delivered via `state/latest_trigger.json`. Read the trigger file and act based on the `type` field:

**Step 1 -- Read trigger file**:
```bash
cat {{OVERWATCH_DIR}}/state/latest_trigger.json
```

**Step 2 -- Execute by type**:

- **`type: "manual_trigger"`**: Extract `session_id` and `transcript_path`, run review:
  ```bash
  python3 {{OVERWATCH_DIR}}/overwatch.py --session-id "<SID>" --transcript "<PATH>" --cwd "$(pwd)" --force 2>&1
  ```
  Then read the review: `bash {{OVERWATCH_DIR}}/hooks/find_review.sh "$(pwd)"` to get the file path, and present the content.

- **`type: "auto_review"`**: Review is already complete. Read the file at `review_path` directly and present the content.

**Step 3 -- Present + Cleanup**:
Present full review verbatim -> separator -> respond point by point -> delete trigger file. Then continue with the user's current request.

**Important**: Always use `session_id` and `transcript_path` from the trigger file. Do not call `find_session.sh` independently -- multiple concurrent sessions may exist for the same project directory, and indirect lookup may return the wrong one.

**Fallback -- find_session.sh** (only when trigger file doesn't exist):
```bash
read SID TRANSCRIPT <<< $(bash {{OVERWATCH_DIR}}/hooks/find_session.sh)
python3 {{OVERWATCH_DIR}}/overwatch.py --session-id "$SID" --transcript "$TRANSCRIPT" --cwd "$(pwd)" --force 2>&1
```
<!-- OVERWATCH:END -->
