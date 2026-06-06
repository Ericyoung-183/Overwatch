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
2. **Builds context** using a rolling summary (for older turns) + verbatim recent window
3. **Runs the configured review backend** with its own independent review prompt
4. **Injects** the review into your next conversation turn

The Builder then presents the review and responds to each point.

Pending auto-review delivery markers expire after 72 hours by default. Expiry only removes the delivery marker, not the saved review file, so old reviews remain available without surprising a resumed session days later.

Smart trigger signals are shared across Claude Code and Codex: explicit review/check requests, user corrections, dense file-edit activity, and recent `git commit` / `git push` boundaries.

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
2. Injects an Overwatch configuration section into your global `CLAUDE.md` (wrapped in `<!-- OVERWATCH:BEGIN/END -->` markers for clean removal)

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

If you run a local status relay or workflow bundle, pass it during install:

```bash
OVERWATCH_CODEX_STATUS_RELAY_DIR=/path/to/relay/state ./install_codex.sh
```

### Manual Trigger

Type `overwatch`, `second opinion`, or `第二意见` in Claude Code or Codex to get an immediate review.

### CLI Usage

```bash
python3 overwatch.py --session-id <uuid> --transcript <path> --force
```

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
- **Recent window**: Last N turns kept verbatim for detailed review
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
RECENT_WINDOW_SIZE = 20    # Keep last N exchanges verbatim
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
| `OVERWATCH_CODEX_STATUS_RELAY_FILE` | Optional single status relay file for the current Codex session | unset |
| `OVERWATCH_PENDING_TTL_HOURS` | Hours a pending auto-review marker remains auto-deliverable; `0` disables expiry | `72` |
| `ANTHROPIC_API_KEY` | API backend authentication; not required for Codex `codex_exec` | required when `OVERWATCH_BACKEND=api` |
| `ANTHROPIC_BASE_URL` | API endpoint | `https://api.anthropic.com` |
| `OVERWATCH_REVIEW_MODEL` | Override review model | from `ANTHROPIC_MODEL` |
| `OVERWATCH_SUMMARY_MODEL` | Override summary model | from `ANTHROPIC_DEFAULT_HAIKU_MODEL` |
| `OVERWATCH_INCLUDE_LEGACY_CONTEXT` | Include cross-runtime legacy context in review prompts | `false` |
| `OVERWATCH_ENABLE_ANCHOR_DRIFT` | Anchor-specific agenda drift rubric: `auto`, `true`, or `false`; `auto` enables only when Anchor helper or context is detected | `auto` |
| `OVERWATCH_CC_PROJECTS` | Claude Code projects directory | `~/.claude/projects` |

## Architecture

```
overwatch/
├── overwatch.py           # Main engine: orchestrates the review pipeline
├── config.py              # All configuration in one place
├── api_client.py          # Claude API client (zero external dependencies)
├── codex_exec_client.py   # Codex exec backend (uses existing Codex login)
├── pending_review.py      # Pending auto-review marker TTL and cleanup
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
- **Adapter pattern**: Transcript parsing is pluggable. Add support for Cursor, Copilot, etc. by implementing a new adapter.
- **Non-blocking hooks**: Stop hook always returns `{"continue": true}` within 5 seconds. Reviews run asynchronously.
- **File-based state**: No database. State is JSON files in `state/`. Reviews are Markdown in `reviews/`.
- **Runtime separation**: Claude Code keeps the Claude/API default path. Codex app/CLI can use the Codex adapter plus `codex_exec` backend without changing Claude defaults.
- **Shared trigger policy**: Claude Code and Codex use the same `trigger_policy.py` decision logic; runtime hooks only adapt transcript parsing and review dispatch.

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

This checks public-file hygiene, Claude Code compatibility, Codex compatibility, response-protocol delivery, shell syntax, Python syntax, and whitespace errors. The public Overwatch repository must not depend on personal local paths; local workflow bundles should inject optional behavior through environment variables such as `OVERWATCH_CODEX_STATUS_RELAY_DIR`.

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
