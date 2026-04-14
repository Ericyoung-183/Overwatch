"""Claude API client. Zero external dependencies — uses only urllib."""
import json
import urllib.request
import urllib.error

from config import (
    API_BASE_URL,
    API_AUTH_TOKEN,
    API_TIMEOUT,
    REVIEW_MODEL,
    MAX_REVIEW_TOKENS,
)


def call_claude(system_prompt: str, user_message: str, model: str = None,
                max_tokens: int = None, thinking: bool = True) -> str:
    """Call Claude API (Messages API format).

    Args:
        system_prompt: System prompt text.
        user_message: User message text.
        model: Override model (default: REVIEW_MODEL from config).
        max_tokens: Override max tokens (default: MAX_REVIEW_TOKENS from config).
        thinking: Enable extended thinking (default: True for reviews).

    Returns:
        Response text, or error string prefixed with "[Overwatch".
    """
    model = model or REVIEW_MODEL
    max_tokens = max_tokens or MAX_REVIEW_TOKENS
    base_url = API_BASE_URL.rstrip("/")
    url = f"{base_url}/v1/messages"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }
    if thinking and max_tokens > 4000:
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": max_tokens - 4000,  # Reserve 4K for visible response
        }

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if API_AUTH_TOKEN:
        headers["x-api-key"] = API_AUTH_TOKEN

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("content", [])
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block["text"])
            return "\n".join(text_parts) if text_parts else "[Overwatch Error: API returned empty content]"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"[Overwatch API Error: {e.code}] {body[:500]}"
    except Exception as e:
        return f"[Overwatch Error: {type(e).__name__}] {str(e)}"
