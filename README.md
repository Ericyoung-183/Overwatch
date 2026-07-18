# Overwatch

**Independent AI reviewer for your AI coding sessions.**

AI writes code fast, but who reviews the reviewer? Overwatch is a second pair of eyes — an independent AI that reads your full session transcript and provides periodic code review, architectural review, and reasoning quality assessment.

## How It Works

```
You <-> Claude Code (Builder)     Overwatch (Independent Reviewer)
     |                                    |
     |  work on your project...           |
     |  [every N turns]                   |
     |  --------------------------------> | reads full transcript
     |                                    | builds context (summary + recent)
     |                                    | calls configured review backend
     |  <-------------------------------- | injects review via hook
     |  Builder presents & discusses      |
```

Overwatch hooks into Claude Code or Codex Desktop's event system. Both runtimes use the same trigger policy: reviews never fire below the minimum turn floor (default: 5), always fire at the hard ceiling (default: 15), and can fire early between those bounds when smart signals are detected.

1. **Parses** the full session JSONL transcript
2. **Builds context** using a rolling summary (for older turns) + exact recent user/assistant messages with bounded tool evidence
3. **Runs the configured review backend** with its own independent review prompt
4. **Injects** the review into your next conversation turn

The Builder then presents the review and responds to each point.

Pending auto-review delivery markers expire after 72 hours by default. Expiry only removes the delivery marker, not the saved review file, so old reviews remain available without surprising a resumed session days later. Each marker binds the exact session ID and review SHA-256; a marker from another session or a replaced review is rejected and preserved for diagnosis. Review generation first persists a recoverable delivery intent. The Hook keeps the exact pending marker until the Builder presents the full review and runs the session/hash-bound acknowledgement command embedded in that delivery. A rejected Hook response therefore retries on the next prompt instead of recording a false receipt. Fresh authorized reviews are injected in full so the Builder can satisfy the verbatim-delivery protocol.

Smart trigger signals are shared across Claude Code and Codex: explicit review/check requests, user corrections, dense file-edit activity, and recent `git commit` / `git push` boundaries.

When Anchor is installed, both prompt hooks apply a strict two-signal capture gate: a concrete multiline, numbered-inline, or explicitly introduced Chinese-delimited list plus item-by-item intent. Root, child, and temporary interrupt targets are distinguished before work begins. The latest bounded assistant/user exchange can supply the source, so a user-owned list is still captured when “逐一过” arrives on the next turn. Persisted candidates are bound to the same session and working directory, and clearing a captured candidate does not skip a fresh same-turn root or child signal. Exact source bytes remain recoverable until the matching Anchor agenda exists or the Builder records a non-empty dismissal reason. Explanatory lists and generic root-level “continue” prompts remain quiet.

For an active agenda, the prompt hook also blocks prose-only completion when the tracker still marks that item as discussing. The completion phrase and current-item reference must occur in the same clause; short item labels and common wording such as “没问题了” or “结论已确定” are supported without treating ordinary code-completion prose as agenda completion. Missing or mismatched native transcript identity surfaces an explicit warning instead of silently disabling this gate.

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or Codex installed and working
- For the default Claude/API backend, an Anthropic API key — Overwatch calls the Claude API independently for reviews, separate from Claude Code's own authentication:
  1. Create an account at [console.anthropic.com](https://console.anthropic.com/) (this is separate from your Claude Pro/Max subscription)
  2. Go to [Settings > API Keys](https://console.anthropic.com/settings/keys) and create a key (copy it immediately — it's only shown once)
  3. Add to your shell profile:
     ```bash
     # ~/.zshrc (macOS) or ~/.bashrc (Linux)
     export ANTHROPIC_API_KEY="sk-ant-..."
     ```
  4. Restart your terminal, then proceed with install

  New accounts may receive free credit — see [console.anthropic.com](https://console.anthropic.com/) for current pricing details.

> If your environment already provides `ANTHROPIC_AUTH_TOKEN`, Overwatch will use it automatically — no additional setup needed.

For Codex Desktop or Codex CLI, Overwatch can use `OVERWATCH_BACKEND=codex_exec` instead. This runs `codex exec` with the user's existing Codex login, so no separate OpenAI API key is required. Codex runtimes are detected from Codex environment signals by default; explicit `OVERWATCH_ADAPTER`, `OVERWATCH_BACKEND`, and `OVERWATCH_REVIEW_MODEL` values always win. The Codex exec backend defaults to `model_reasoning_effort="xhigh"` for maximum review depth.

## Quick Start

### Claude Code

```bash
git clone https://github.com/Ericyoung-183/Overwatch.git
cd Overwatch
./install.sh
# Restart Claude Code — done!
```

The Claude Code installer automatically:
1. Registers Stop + UserPromptSubmit hooks in your Claude Code settings
2. Injects an Overwatch configuration section into your global `CLAUDE.md` (wrapped in `<!-- OVERWATCH:BEGIN/END -->` markers for clean removal). Replacement and uninstall require exactly one complete marker pair; incomplete, duplicate, or unmarked lookalike content is preserved.

### Codex Desktop / Codex CLI

```bash
git clone https://github.com/Ericyoung-183/Overwatch.git
cd Overwatch
./install_codex.sh
# Restart Codex — done!
```

The Codex installer automatically:
1. Registers Stop + UserPromptSubmit hooks in your Codex `hooks.json`
2. Uses the Codex transcript adapter and `codex_exec` backend in Codex runtimes
3. Keeps Claude Code defaults unchanged

Before changing hooks, the installer verifies every required runtime module and the configured Codex executable. A failed preflight leaves the hooks file untouched.

Run `./uninstall.sh` to remove the exact hooks owned by this Overwatch installation while preserving unrelated or merely similarly named hooks and saved Overwatch files. Claude hooks, Codex hooks, and the owned `CLAUDE.md` section are preflighted and staged as one transaction; a failure restores every already-replaced file. `CC_SETTINGS_PATH` is the Claude settings authority for both hooks and the adjacent `CLAUDE.md` snippet.

If you run a local status relay or workflow bundle, pass it during install:

```bash
OVERWATCH_CODEX_STATUS_RELAY_DIR=/path/to/relay/state ./install_codex.sh
```

### Manual Trigger

Type `overwatch`, `second opinion`, or `第二意见` in Claude Code or Codex to get an immediate review.

Each manual trigger reserves a unique managed result file inside `OVERWATCH_STATE_DIR`. The review command first validates the session ID as a safe file key, binds it to the native ID inside the Codex or Claude transcript, returns an immutable numbered history file, and records that file's SHA-256. Lookup reads the review once, validates the result session, exact bytes, and review metadata, then streams those same verified bytes instead of returning a path that must be reopened. Session discovery is stored as a locked multi-session registry, and fallback triggers live under `state/triggers/<session-id>.json`; concurrent tasks never share a latest-trigger file. Auto-review fallback triggers retain the authorized review hash, and `trigger_state.py read-auto-review` streams only the exact bytes matching it. A failed, replaced, ambiguous, or crossed review exits nonzero instead of falling back to a stale `latest.md`.

### CLI Usage

```bash
python3 overwatch.py --session-id <uuid> --transcript <path> --force
```

The supplied ID must be the single native session ID recorded inside that transcript. Missing, mixed, or mismatched identity fails closed.

## Features

### 6-Dimension Review Framework

| Dimension | What It Checks |
|-----------|---------------|
| **Intent Alignment** | Is the Builder doing what you actually asked? Scope drift, hidden assumptions, premature completion |
| **Reference Integrity** | Are all cross-references consistent after changes? Signatures, renames, imports, consumers |
| **Change-Induced Errors** | Did the change itself introduce new problems? Contradictions, silent failures, hallucinated APIs, security issues |
| **Coverage Completeness** | Were all related files updated? Config, docs, hooks, tests, build scripts, type definitions |
| **Risk Identification** | What could go wrong that hasn't been considered? Production impact, hidden coupling, fragile assumptions |
| **Root Cause Resolution** | Is the root cause solved, or just patched? Is there a simpler, more robust approach? |

### Same-Model Bias Awareness

Overwatch explicitly accounts for **homogeneous bias** — when the reviewer and the builder are from the same model family, they may share blind spots. The review prompt includes a meta-rule: the more "perfect" output looks, the harder to scrutinize.

### Incremental Review

Each review builds on the previous one. Overwatch reads its last review and focuses on:
- Were previous issues fixed?
- Are there new issues?
- No redundant re-reporting of resolved items.

### Long Session Support

Sessions can run 100+ turns. Overwatch handles this with:
- **Rolling summary**: Older turns are compressed by a fast model (Haiku)
- **Recent window**: Last N user/assistant exchanges kept exactly; tool calls and outputs stay explicitly bounded
- **Incremental summarization**: Only newly-expired turns are summarized

### Domain Agnostic

Works for coding, research, analysis, documentation — any AI-assisted work. Review dimensions adapt to the session content.

## Configuration

Edit `config.py`:

```python
TURN_THRESHOLD = 10        # Baseline interval when SMART_TRIGGER is disabled
SMART_TRIGGER = True       # Enable early reviews between min and max when risk signals appear
TURN_THRESHOLD_MIN = 5     # Never auto-review below this many new user turns
TURN_THRESHOLD_MAX = 15    # Always auto-review at or above this many new user turns
RECENT_WINDOW_SIZE = 20    # Keep last N exact user/assistant exchanges
REVIEW_BACKEND = "api"     # "api" or "codex_exec"; Codex runtimes default to codex_exec
REVIEW_MODEL = "claude-sonnet-4-20250514"  # Model for reviews; gpt-5.5 for codex_exec
CODEX_REASONING_EFFORT = "xhigh"  # Highest Codex reasoning effort for codex_exec
SUMMARY_MODEL = "claude-haiku-4-5-20251001"  # Model for summaries
TRIGGER_KEYWORDS = ["overwatch", "second opinion", "第二意见"]  # Manual trigger words
```

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `OVERWATCH_ADAPTER` | Transcript adapter: `claude_code` or `codex` | Runtime-aware (`claude_code`, or `codex` in Codex runtimes) |
| `OVERWATCH_BACKEND` | Review backend: `api` or `codex_exec` | Runtime-aware (`api`, or `codex_exec` in Codex runtimes) |
| `OVERWATCH_CODEX_COMMAND` | Codex executable for `codex_exec` backend | `/Applications/Codex.app/Contents/Resources/codex` if present |
| `OVERWATCH_CODEX_EXEC_TIMEOUT` | Timeout for nested Codex review | `API_TIMEOUT` |
| `OVERWATCH_CODEX_REASONING_EFFORT` | Codex `model_reasoning_effort` for nested reviews | `xhigh` |
| `OVERWATCH_CODEX_STATUS_RELAY_DIR` | Optional directory containing Codex status relay files named `last_stop_says_<session>.json` | unset |
| `OVERWATCH_CODEX_STATUS_RELAY_FILE` | Optional single status relay file; payload must contain the exact current `session_id` | unset |
| `OVERWATCH_PENDING_TTL_HOURS` | Hours a pending auto-review marker remains auto-deliverable; `0` disables expiry | `72` |
| `OVERWATCH_ALLOWED_PROJECTS` | Colon-separated exact project roots; descendants are allowed and both hooks plus engine fail closed outside them | unset (all projects) |
| `ANTHROPIC_API_KEY` | API backend authentication; not required for Codex `codex_exec` | required when `OVERWATCH_BACKEND=api` |
| `ANTHROPIC_BASE_URL` | API endpoint | `https://api.anthropic.com` |
| `OVERWATCH_REVIEW_MODEL` | Override review model | from `ANTHROPIC_MODEL` |
| `OVERWATCH_SUMMARY_MODEL` | Override summary model | from `ANTHROPIC_DEFAULT_HAIKU_MODEL` |
| `OVERWATCH_INCLUDE_LEGACY_CONTEXT` | Include cross-runtime legacy context in review prompts | `false` |
| `OVERWATCH_ENABLE_ANCHOR_DRIFT` | Anchor-specific agenda drift rubric: `auto`, `true`, or `false`; `auto` enables only for actual Anchor context or a pending capture gate | `auto` |
| `OVERWATCH_CC_PROJECTS` | Claude Code projects directory | `~/.claude/projects` |

## Architecture

```
overwatch/
├── overwatch.py           # Main engine: orchestrates the review pipeline
├── config.py              # All configuration in one place
├── api_client.py          # Claude API client (zero external dependencies)
├── codex_exec_client.py   # Codex exec backend (uses existing Codex login)
├── pending_review.py      # Pending auto-review marker TTL and cleanup
├── config_transaction.py  # Atomic installer config compare-and-swap
├── trigger_policy.py      # Shared auto-review trigger policy for Claude Code and Codex
├── context_manager.py     # Rolling summary + recent window management
├── prompts.py             # Review framework and prompt templates
├── adapters/
│   ├── __init__.py        # Adapter interface (Turn dataclass)
│   ├── claude_code.py     # Claude Code JSONL transcript parser
│   └── codex.py           # Codex Desktop/CLI JSONL transcript parser
├── hooks/
│   ├── claude_code_stop.sh     # Stop hook (auto-trigger)
│   ├── claude_code_prompt.sh   # UserPromptSubmit hook (manual trigger)
│   ├── codex_stop.sh           # Codex Stop hook
│   ├── codex_prompt.sh         # Codex UserPromptSubmit hook
│   ├── find_session.sh         # Session discovery
│   └── find_review.sh          # Review file discovery
├── scripts/
│   └── check_release.sh        # Public release compatibility checks
├── install.sh             # One-command Claude Code setup
├── install_codex.sh       # One-command Codex setup
├── uninstall.sh           # Clean removal
├── reviews/               # Review output (created at runtime)
└── state/                 # Persistent state (created at runtime)
```

### Key Design Decisions

- **Zero external dependencies**: Pure Python stdlib (`urllib`, `json`, `dataclasses`). No `pip install` needed.
- **Adapter pattern**: Transcript parsing is pluggable. The built-in adapters preserve current Codex `custom_tool_call`/output and Anchor developer-context records, plus Claude block text, image markers, tool use, and tool results.
- **Guarded prompt context**: Anchor agenda labels are escaped and marked as untrusted data before hook injection. Context caps preserve mandatory tracker, item, cursor, and pending-presentation fields while truncating only optional diagnostics; manual-review command arguments are shell-quoted.
- **Anchor helper compatibility**: When active Anchor state exists, Codex prompt delivery validates Anchor's structured `capabilities` response and blocks mutation when the installed helper lacks the V2.1 state/context contract. Untrusted agenda labels cannot spoof this check.
- **Review boundary precedence**: An explicit read-only, findings-only, frozen-candidate, or no-edit request overrides the default fix-and-persist review protocol.
- **Non-blocking hooks**: Stop hook always returns `{"continue": true}` within 5 seconds. Reviews run asynchronously.
- **File-based state**: No database. State is JSON files in `state/`. Reviews are Markdown in `reviews/`.
- **Private review artifacts**: Persistent review directories are forced to `0700` and review files to `0600`; numbered history remains immutable.
- **Read-only reviewer tools**: Git refs are resolved to object IDs before use, option-like refs are rejected, and external diff/text-conversion helpers are disabled.
- **Symlink-safe archives**: Session/history directories and immutable review files are opened relative to no-follow directory descriptors, so a symlink cannot redirect an archive outside `reviews/`.
- **Directory-durable state**: State, trigger, marker, receipt, manual-result, and review writes fsync both file contents and the containing directory after atomic replacement.
- **Retry-safe review cursor**: A failed backend or invalid review output records cooldown/error state without advancing `last_reviewed_turn`, so the same transcript can retry after cooldown.
- **Recoverable review delivery**: Archive creation persists a delivery intent before marker publication; only the Builder's post-presentation, session/hash-bound acknowledgement converts the exact marker into a delivery receipt. Interrupted or rejected Hook delivery retries without calling the review backend again.
- **Compare-and-swap pending cleanup**: Expiry cleanup deletes only the marker bytes it inspected and restores a concurrently replaced marker.
- **Session plus project identity**: Runtime hooks, transcript adapters, engine state, review artifacts, pending markers, triggers, receipts, and review lookup bind one bounded session ID to one canonical Git project root. Reusing a task in another project fails closed without delivering review or agenda source data.
- **Runtime separation**: Claude Code keeps the Claude/API default path. Codex app/CLI can use the Codex adapter plus `codex_exec` backend without changing Claude defaults.
- **Shared trigger policy**: Claude Code and Codex use the same `trigger_policy.py` decision logic; runtime hooks only adapt transcript parsing and review dispatch.
- **Relocatable installation**: Both installers replace stale or duplicate managed Hook paths with one shell-quoted current path. Hook Python imports receive the install directory through the environment, so spaces and quotes remain safe. Install and shared-uninstall replacements reject symbolic-link configs and use atomic exchange/no-replace compare-and-swap. A pre-commit conflict restores the external bytes; if an external write lands immediately after exchange or during rollback, Overwatch keeps the external edit active or quarantined and preserves displaced bytes in a named recovery file. Cross-runtime rollback retains each config's own mode.
- **Mutation-complete release gate**: Release checks snapshot candidate files and modes recursively, including ignored files; only named runtime/cache artifacts such as `state/`, `reviews/`, `overwatch.log`, bytecode, and editor caches are excluded.

## Custom Adapters

To add support for a new AI coding tool:

1. Create `adapters/your_tool.py` with a `parse(transcript_path, offset) -> list[Turn]` function
2. Set `ADAPTER = "your_tool"` in `config.py`
3. Write hook scripts for your tool's extension system (equivalent to `hooks/claude_code_*.sh`)

See `adapters/claude_code.py` for reference implementation.

## Release Checks

Before publishing a GitHub release, run:

```bash
./scripts/check_release.sh
```

This checks public-file hygiene, real-format transcript adapters, Claude Code compatibility and relocation, Codex compatibility, exact manual-review identity, response-protocol delivery, shell syntax, Python syntax, whitespace errors, and that the gate did not change candidate file bytes or modes. The public Overwatch repository must not depend on personal local paths; local workflow bundles should inject optional behavior through environment variables such as `OVERWATCH_CODEX_STATUS_RELAY_DIR`.

For Codex, the release gate installs Overwatch into a temporary Codex hooks file, then executes the installed Stop and UserPromptSubmit commands with synthetic Codex hook payloads. This proves the installer output and hook command contract without touching a user's live Codex config. A live Codex Desktop or Codex CLI turn remains a manual release check when the Codex runtime does not expose a standalone hook trigger command.

## Comparison

| Feature | Overwatch | CodeRabbit | gossipcat | agent-review | saguaro |
|---------|-----------|------------|-----------|--------------|---------|
| Reviews full session transcript | Yes | No | No | No | No |
| Real-time during session | Yes | No (PR only) | No | No | Yes (per-turn) |
| Incremental with memory | Yes | No | No | No | No |
| Domain agnostic | Yes | No | No | No | No |
| Same-model bias awareness | Yes | N/A | No | No | No |
| Long session support | Yes (rolling summary) | N/A | N/A | N/A | No |
| Zero dependencies | Yes | SaaS | npm | Emacs | npm |

## License

MIT
