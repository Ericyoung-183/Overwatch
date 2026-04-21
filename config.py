"""Overwatch configuration. Edit this file to customize behavior."""
import os
import re


_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_ALLOWED_INLINE_WHITESPACE = {"\t", "\n", "\r"}


def _clean_env(value: str, default: str = "") -> str:
    """Trim whitespace/control chars and strip ANSI escapes from env-derived values."""
    if value is None:
        return default
    value = _ANSI_ESCAPE_RE.sub("", str(value))
    value = "".join(ch for ch in value if ch >= " " or ch in _ALLOWED_INLINE_WHITESPACE)
    return value.strip() or default


def _clean_model_id(model_id: str) -> str:
    """Strip bracketed suffixes like [1m] from model IDs.

    Some environments (e.g. proxies) append context-window hints such as
    ``glink/claude-opus-4-6[1m]``.  The bare ID is what the API expects.
    """
    return re.sub(r"\[.*?\]$", "", model_id)


# --- Paths (auto-detected, no manual editing needed) ---
OVERWATCH_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEWS_DIR = os.path.join(OVERWATCH_DIR, "reviews")
STATE_DIR = os.path.join(OVERWATCH_DIR, "state")
CURRENT_REVIEW_LINK = os.path.join(REVIEWS_DIR, "_current.md")

# --- Throttle ---
TURN_THRESHOLD = 10  # Auto-trigger review every N user turns

# --- Context Window ---
RECENT_WINDOW_SIZE = 20  # Keep last N user-assistant exchanges verbatim
MAX_SUMMARY_CHARS = 5000  # Rolling summary max characters
MAX_TURN_CONTENT_CHARS = 4000  # Per-turn content truncation limit

# --- API ---
API_BASE_URL = _clean_env(
    os.environ.get("OVERWATCH_BASE_URL", os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")),
    "https://api.anthropic.com",
).rstrip("/")
# Supports multiple auth env vars: OVERWATCH_API_KEY (dedicated) > ANTHROPIC_API_KEY > ANTHROPIC_AUTH_TOKEN
API_AUTH_TOKEN = _clean_env(
    os.environ.get("OVERWATCH_API_KEY", os.environ.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_AUTH_TOKEN", "")))
)
# API format: "anthropic" (default) or "openai" (for GLM, DeepSeek, Qwen, etc.)
# Auto-detects if not set: uses "openai" when base URL is not api.anthropic.com and not localhost.
_raw_api_format = _clean_env(os.environ.get("OVERWATCH_API_FORMAT", ""), "").lower()
if _raw_api_format in ("anthropic", "openai"):
    API_FORMAT = _raw_api_format
else:
    # Auto-detect: localhost/127.0.0.1 → anthropic (likely proxy), api.anthropic.com → anthropic
    _is_anthropic = any(h in API_BASE_URL for h in ("anthropic.com", "localhost", "127.0.0.1"))
    API_FORMAT = "anthropic" if _is_anthropic else "openai"
REVIEW_MODEL = _clean_model_id(_clean_env(
    os.environ.get("OVERWATCH_REVIEW_MODEL", os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")),
    "claude-sonnet-4-20250514",
))
SUMMARY_MODEL = _clean_model_id(_clean_env(
    os.environ.get("OVERWATCH_SUMMARY_MODEL", os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5-20251001")),
    "claude-haiku-4-5-20251001",
))

# --- API Limits ---
API_TIMEOUT = 300  # seconds (extended thinking takes longer)
MAX_REVIEW_TOKENS = 16000  # Budget for extended thinking + response
MAX_SUMMARY_TOKENS = 1000
MAX_SUMMARY_INPUT_CHARS = 300000  # Haiku input limit (~150K tokens with safety margin)
API_MAX_RETRIES = int(_clean_env(os.environ.get("OVERWATCH_API_MAX_RETRIES", "3"), "3"))
API_RETRY_BASE_DELAY = float(_clean_env(os.environ.get("OVERWATCH_API_RETRY_BASE_DELAY", "1.5"), "1.5"))
API_RETRY_MAX_DELAY = float(_clean_env(os.environ.get("OVERWATCH_API_RETRY_MAX_DELAY", "8"), "8"))
DEBUG_RESPONSE_PREVIEW_CHARS = int(_clean_env(os.environ.get("OVERWATCH_DEBUG_RESPONSE_PREVIEW_CHARS", "1200"), "1200"))

# --- Review Validation / Failure Backoff ---
MIN_REVIEW_CHARS = int(_clean_env(os.environ.get("OVERWATCH_MIN_REVIEW_CHARS", "80"), "80"))
REVIEW_FAILURE_COOLDOWN_SECONDS = int(_clean_env(os.environ.get("OVERWATCH_REVIEW_FAILURE_COOLDOWN_SECONDS", "120"), "120"))
REVIEW_MAX_COOLDOWN_SECONDS = int(_clean_env(os.environ.get("OVERWATCH_REVIEW_MAX_COOLDOWN_SECONDS", "600"), "600"))

# --- Transcript Adapter ---
ADAPTER = "claude_code"  # Which adapter to use for parsing session transcripts

# --- Session Metadata ---
# Claude Code stores projects under this base directory.
# Override with OVERWATCH_CC_PROJECTS env var if your installation differs.
CC_PROJECTS_BASE = os.environ.get("OVERWATCH_CC_PROJECTS", os.path.expanduser("~/.claude/projects"))
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
