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
     |                                    | calls Claude API
     |  <-------------------------------- | injects review via hook
     |  Builder presents & discusses      |
```

Overwatch hooks into Claude Code's event system. After every N user turns (default: 10), it:

1. **Parses** the full session JSONL transcript
2. **Builds context** using a rolling summary (for older turns) + verbatim recent window
3. **Calls Claude API** with its own independent review prompt
4. **Injects** the review into your next conversation turn

The Builder then presents the review and responds to each point.

## Quick Start

```bash
git clone https://github.com/Ericyoung183/overwatch.git
cd overwatch
./install.sh
# Restart Claude Code — done!
```

The installer automatically:
1. Registers Stop + UserPromptSubmit hooks in your Claude Code settings
2. Injects an Overwatch configuration section into your global `CLAUDE.md` (wrapped in `<!-- OVERWATCH:BEGIN/END -->` markers for clean removal)

### Manual Trigger

Type `overwatch`, `second opinion`, or `第二意见` in Claude Code to get an immediate review.

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
TURN_THRESHOLD = 10        # Auto-review every N turns
RECENT_WINDOW_SIZE = 10    # Keep last N exchanges verbatim
REVIEW_MODEL = "claude-sonnet-4-20250514"  # Model for reviews
SUMMARY_MODEL = "claude-haiku-4-5-20251001"  # Model for summaries
TRIGGER_KEYWORDS = ["overwatch", "second opinion", "第二意见"]  # Manual trigger words
```

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | API authentication | (required) |
| `ANTHROPIC_BASE_URL` | API endpoint | `https://api.anthropic.com` |
| `OVERWATCH_REVIEW_MODEL` | Override review model | from `ANTHROPIC_MODEL` |
| `OVERWATCH_SUMMARY_MODEL` | Override summary model | from `ANTHROPIC_DEFAULT_HAIKU_MODEL` |
| `OVERWATCH_CC_PROJECTS` | Claude Code projects directory | `~/.claude/projects` |

## Architecture

```
overwatch/
├── overwatch.py           # Main engine: orchestrates the review pipeline
├── config.py              # All configuration in one place
├── api_client.py          # Claude API client (zero external dependencies)
├── context_manager.py     # Rolling summary + recent window management
├── prompts.py             # Review framework and prompt templates
├── adapters/
│   ├── __init__.py        # Adapter interface (Turn dataclass)
│   └── claude_code.py     # Claude Code JSONL transcript parser
├── hooks/
│   ├── claude_code_stop.sh     # Stop hook (auto-trigger)
│   ├── claude_code_prompt.sh   # UserPromptSubmit hook (manual trigger)
│   ├── find_session.sh         # Session discovery
│   └── find_review.sh          # Review file discovery
├── install.sh             # One-command setup
├── uninstall.sh           # Clean removal
├── reviews/               # Review output (created at runtime)
└── state/                 # Persistent state (created at runtime)
```

### Key Design Decisions

- **Zero external dependencies**: Pure Python stdlib (`urllib`, `json`, `dataclasses`). No `pip install` needed.
- **Adapter pattern**: Transcript parsing is pluggable. Add support for Cursor, Copilot, etc. by implementing a new adapter.
- **Non-blocking hooks**: Stop hook always returns `{"continue": true}` within 5 seconds. Reviews run asynchronously.
- **File-based state**: No database. State is JSON files in `state/`. Reviews are Markdown in `reviews/`.

## Custom Adapters

To add support for a new AI coding tool:

1. Create `adapters/your_tool.py` with a `parse(transcript_path, offset) -> list[Turn]` function
2. Set `ADAPTER = "your_tool"` in `config.py`
3. Write hook scripts for your tool's extension system (equivalent to `hooks/claude_code_*.sh`)

See `adapters/claude_code.py` for reference implementation.

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
