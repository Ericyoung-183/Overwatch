#!/bin/bash
set -euo pipefail

OVERWATCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${OVERWATCH_STATE_DIR:-${OVERWATCH_DIR}/state}"
SESSION_ID=""
TRANSCRIPT=""
CWD=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --session-id) SESSION_ID="${2:?missing --session-id value}"; shift 2 ;;
        --transcript) TRANSCRIPT="${2:?missing --transcript value}"; shift 2 ;;
        --cwd) CWD="${2:?missing --cwd value}"; shift 2 ;;
        *) echo "run_manual_review: unknown argument: $1" >&2; exit 2 ;;
    esac
done

[ -n "$SESSION_ID" ] || { echo "run_manual_review: --session-id is required" >&2; exit 2; }
[ -n "$TRANSCRIPT" ] || { echo "run_manual_review: --transcript is required" >&2; exit 2; }
[ ! -L "$STATE_DIR" ] || { echo "ERROR: Overwatch state directory cannot be a symlink" >&2; exit 2; }
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
RESULT_FILE=$(mktemp "${STATE_DIR}/manual_review_result.XXXXXX.json")
rm -f "$RESULT_FILE"
cleanup() {
    rm -f "$RESULT_FILE"
}
trap cleanup EXIT

python3 "${OVERWATCH_DIR}/overwatch.py" \
    --session-id "$SESSION_ID" \
    --transcript "$TRANSCRIPT" \
    --cwd "$CWD" \
    --force \
    --result-file "$RESULT_FILE" \
    2>&1
# The verifier streams the already-hashed bytes it inspected, so callers never
# reopen a mutable path between authorization and presentation.
bash "${OVERWATCH_DIR}/hooks/find_review.sh" \
    --result-file "$RESULT_FILE" \
    --session-id "$SESSION_ID"
