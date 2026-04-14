"""Overwatch configuration. Edit this file to customize behavior."""
import os

# --- Paths (auto-detected, no manual editing needed) ---
OVERWATCH_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEWS_DIR = os.path.join(OVERWATCH_DIR, "reviews")
STATE_DIR = os.path.join(OVERWATCH_DIR, "state")
CURRENT_REVIEW_LINK = os.path.join(REVIEWS_DIR, "_current.md")

# --- Throttle ---
TURN_THRESHOLD = 10  # Auto-trigger review every N user turns

# --- Context Window ---
RECENT_WINDOW_SIZE = 10  # Keep last N user-assistant exchanges verbatim
MAX_SUMMARY_CHARS = 5000  # Rolling summary max characters
MAX_TURN_CONTENT_CHARS = 4000  # Per-turn content truncation limit

# --- API ---
API_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
# Supports multiple auth env vars for compatibility with different Claude Code distributions
API_AUTH_TOKEN = os.environ.get("ANTHROPIC_API_KEY",
                                os.environ.get("ANTHROPIC_AUTH_TOKEN", ""))
REVIEW_MODEL = os.environ.get("OVERWATCH_REVIEW_MODEL",
                              os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"))
SUMMARY_MODEL = os.environ.get("OVERWATCH_SUMMARY_MODEL",
                               os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5-20251001"))

# --- API Limits ---
API_TIMEOUT = 300  # seconds (extended thinking takes longer)
MAX_REVIEW_TOKENS = 16000  # Budget for extended thinking + response
MAX_SUMMARY_TOKENS = 1000
MAX_SUMMARY_INPUT_CHARS = 300000  # Haiku input limit (~150K tokens with safety margin)

# --- Transcript Adapter ---
ADAPTER = "claude_code"  # Which adapter to use for parsing session transcripts

# --- Session Metadata ---
# Claude Code stores projects under this base directory.
# Override with OVERWATCH_CC_PROJECTS env var if your installation differs.
CC_PROJECTS_BASE = os.environ.get("OVERWATCH_CC_PROJECTS",
                                  os.path.expanduser("~/.claude/projects"))
# Fallback paths to try if primary doesn't exist.
# Set OVERWATCH_CC_PROJECTS_FALLBACK (colon-separated) for additional search paths.
CC_PROJECTS_FALLBACKS = [
    p for p in [os.path.expandvars(os.path.expanduser(x))
                for x in os.environ.get("OVERWATCH_CC_PROJECTS_FALLBACK", "").split(":")]
    if p and os.path.isdir(p)
]

# --- Project Whitelist ---
# If non-empty, Overwatch only runs in these project directories.
# Empty list (default) = all projects.
ALLOWED_PROJECTS = [
    p for p in os.environ.get("OVERWATCH_ALLOWED_PROJECTS", "").split(":")
    if p
]

# --- Skip Patterns ---
SKIP_TYPES = {"system", "attachment", "file-history-snapshot", "permission-mode"}
SKIP_USER_PATTERNS = [
    "<local-command-caveat>",
    "<command-name>",
    "<local-command-stdout>",
    "<system-reminder>",
]

# --- Manual Trigger Keywords ---
# Users type these to trigger an immediate review. Matched exactly (case-insensitive).
TRIGGER_KEYWORDS = ["overwatch", "second opinion", "第二意见"]
