# Codex Desktop turn lifecycle stalls across image and non-image sessions

## Summary

Codex Desktop sessions can get stuck indefinitely showing "thinking" / "generating" after a few turns. This is not limited to long-running sessions: I observed it in small/new image sessions as well as in a long non-image session. Local diagnostics indicate multiple turn lifecycle stall modes:

- main turn starts but never reaches a normal completion state
- tool outputs are returned, but the assistant continuation never resumes
- long non-image sessions enter a core unfinished turn state after compaction/prewarm/interruption activity

The user-visible result is that the session remains in a thinking state and no further assistant message appears.

## Environment

- Product: Codex Desktop app + Codex CLI
- CLI version: `codex-cli 0.128.0-alpha.1`
- OS: macOS 15.5, arm64
- Desktop app server observed as: `Codex.app ... app-server --analytics-default-enabled`
- Main affected model in real sessions: `gpt-5.5`
- Probe model: `gpt-5.4-mini`
- GitHub issue report intentionally excludes full transcripts, local private paths, and image files.

## Observed cases

### Case A: new/small image session, main turn did not complete

- Session type: image-to-cartoon style task
- Session size: small/new session
- Observed diagnostic shape:
  - `task_started: 2`
  - `task_complete: 0`
  - user-visible session remained thinking
  - prompt hooks completed normally and did not block
  - later manual abort/interrupt appears in logs

This suggests the main user turn entered Codex core state but did not reach a normal completion/error surface.

### Case B: image/pet session, post-tool continuation stalled

- Session type: image-heavy pet asset generation
- Observed diagnostic shape:
  - `task_started: 8`
  - `task_complete: 6`
  - last successful events include image generation/tool calls and completed shell command outputs
  - final tool outputs appear after the last assistant message
  - no assistant continuation follows the returned tool outputs

This looks like a post-tool continuation failure: tools returned, but the assistant loop did not resume.

### Case C: non-image long session, core turn unfinished

- Session type: assets migration / non-image engineering work
- Observed diagnostic shape:
  - `task_started: 119`
  - `task_complete: 112`
  - `context_compacted: 13`
  - `turn_aborted: 6`
  - core logs include session startup prewarm and later interrupt/abort activity

This is important because it shows the hang is not only image-related. Image inputs appear to increase the chance of stalls, but a non-image long-context session can also hit the failure mode.

## A/B timing probes

All probes were trivial prompts that completed with `OK`; the point was to compare turn startup/runtime paths.

| Probe path | Text prompt | Image prompt |
|---|---:|---:|
| normal user config/hooks | 36.49s | 87.37s |
| UserPromptSubmit disabled | 35.49s | 58.60s |
| isolated: `--ephemeral --ignore-user-config --disable codex_hooks --disable plugins --disable memories --disable tool_search` | 5.76s | 29.49s |
| isolated + `--disable shell_snapshot` | 5.13s | 31.37s |

Interpretation:

- UserPromptSubmit hooks do not explain the core issue: text timing is essentially unchanged when they are disabled.
- Image inputs are a strong latency amplifier even in isolated mode.
- Normal Desktop/user-config runtime adds a large overhead compared with isolated mode.
- `shell_snapshot` is not the main overhead in this probe.

## Additional signal: analytics Cloudflare 403 during isolated runs

Even with `--ephemeral --ignore-user-config`, Codex emitted warnings that analytics events to:

`https://chatgpt.com/backend-api/codex/analytics-events/events`

failed with Cloudflare-managed 403/challenge HTML.

The probes still completed, so this may not be the sole blocker, but it is a platform-level failing request outside the user's project code/config and may contribute latency/noise or interact badly with retries in Desktop sessions.

## Expected behavior

When Codex cannot continue a turn, it should surface an explicit error, timeout, or cancellation state. A session should not remain indefinitely in a thinking/generating state after:

- the main user turn is accepted
- tool outputs have already returned
- compaction/prewarm/interrupt state occurs

## Actual behavior

The app can remain indefinitely in a thinking/generating state with no further assistant output. Depending on the case, the rollout/log evidence shows dangling `task_started`, returned tool outputs without continuation, or unfinished core turns.

## Related issues found before filing

- #20392: dangling `task_started` hydration issue
- #19980: "thinking... but no output"
- #17728: never ending thinking process
- #14220: hang with parallel long-running tool calls
- #14251: stuck generating after interrupted turn
- #16042: compaction-related regression

This report is filed separately because it includes multiple observed stall modes, an image vs non-image split, and A/B evidence that user prompt hooks are unlikely to be the primary cause.

## Privacy note

I can provide specific local thread IDs or redacted rollout snippets privately if useful, but I am not posting complete transcripts or user image assets in this public issue.
