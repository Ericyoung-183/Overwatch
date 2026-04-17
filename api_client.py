"""API client for Overwatch. Supports Anthropic and OpenAI-compatible formats.
Zero external dependencies — uses only urllib."""
import json
import random
import time
import urllib.request
import urllib.error
from http.client import RemoteDisconnected

from config import (
    API_BASE_URL,
    API_AUTH_TOKEN,
    API_FORMAT,
    API_TIMEOUT,
    REVIEW_MODEL,
    MAX_REVIEW_TOKENS,
    API_MAX_RETRIES,
    API_RETRY_BASE_DELAY,
    API_RETRY_MAX_DELAY,
    DEBUG_RESPONSE_PREVIEW_CHARS,
)


RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


class OverwatchAPIError(RuntimeError):
    """Structured API error used for retry/failure handling."""

    def __init__(self, code: str, message: str, retryable: bool = False, response_preview: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.response_preview = response_preview

    def as_text(self) -> str:
        detail = f"[Overwatch {self.code}] {self.message}"
        if self.response_preview:
            detail += f" | response={self.response_preview}"
        return detail


def _response_preview(payload) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = str(payload)
    if len(text) > DEBUG_RESPONSE_PREVIEW_CHARS:
        text = text[:DEBUG_RESPONSE_PREVIEW_CHARS] + "... [truncated]"
    return text.replace("\n", "\\n")


def _extract_response_text(result: dict) -> str:
    """Best-effort extraction for both Anthropic and OpenAI-compatible responses."""
    # --- OpenAI format: choices[].message.content ---
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message", {})
            if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"].strip():
                return msg["content"].strip()

    # --- Anthropic format: content[].text ---
    content = result.get("content")
    text_parts = []

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    text_parts.append(block["content"])
            elif isinstance(block, str):
                text_parts.append(block)
    elif isinstance(content, str):
        text_parts.append(content)

    if text_parts:
        return "\n".join(part for part in text_parts if part).strip()

    # --- Fallbacks ---
    if isinstance(result.get("output_text"), str) and result["output_text"].strip():
        return result["output_text"].strip()
    if isinstance(result.get("completion"), str) and result["completion"].strip():
        return result["completion"].strip()

    message = result.get("message")
    if isinstance(message, dict):
        msg_content = message.get("content")
        if isinstance(msg_content, str) and msg_content.strip():
            return msg_content.strip()
        if isinstance(msg_content, list):
            nested = _extract_response_text({"content": msg_content})
            if nested:
                return nested

    return ""


def _should_retry_error(exc: Exception) -> bool:
    if isinstance(exc, OverwatchAPIError):
        return exc.retryable
    return isinstance(exc, (urllib.error.URLError, TimeoutError, RemoteDisconnected))


def _sleep_before_retry(attempt: int):
    delay = min(API_RETRY_BASE_DELAY * (2 ** (attempt - 1)), API_RETRY_MAX_DELAY)
    delay += random.uniform(0, 0.35)
    time.sleep(delay)


def _post_messages(payload: dict, headers: dict, api_format: str = "anthropic") -> dict:
    base_url = API_BASE_URL.rstrip("/")
    url = f"{base_url}/v1/chat/completions" if api_format == "openai" else f"{base_url}/v1/messages"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        preview = body[:DEBUG_RESPONSE_PREVIEW_CHARS]
        raise OverwatchAPIError(
            code=f"API Error: {e.code}",
            message=f"HTTP {e.code}",
            retryable=e.code in RETRYABLE_HTTP_CODES,
            response_preview=preview,
        ) from e
    except RemoteDisconnected as e:
        raise OverwatchAPIError(
            code="Error: RemoteDisconnected",
            message="Remote end closed connection without response",
            retryable=True,
        ) from e
    except urllib.error.URLError as e:
        raise OverwatchAPIError(
            code="Error: URLError",
            message=str(e.reason),
            retryable=True,
        ) from e
    except TimeoutError as e:
        raise OverwatchAPIError(
            code="Error: Timeout",
            message=str(e),
            retryable=True,
        ) from e
    except Exception as e:
        raise OverwatchAPIError(
            code=f"Error: {type(e).__name__}",
            message=str(e),
            retryable=False,
        ) from e


def call_claude(system_prompt: str, user_message: str, model: str = None,
                max_tokens: int = None, thinking: bool = True) -> str:
    """Call LLM API with retry. Supports Anthropic and OpenAI-compatible formats."""
    model = model or REVIEW_MODEL
    max_tokens = max_tokens or MAX_REVIEW_TOKENS
    api_format = API_FORMAT

    if api_format == "openai":
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        headers = {
            "Content-Type": "application/json",
        }
        if API_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {API_AUTH_TOKEN}"
    else:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        if thinking and max_tokens > 4000:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": max_tokens - 4000,
            }
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if API_AUTH_TOKEN:
            headers["x-api-key"] = API_AUTH_TOKEN

    last_error = None
    total_attempts = max(1, API_MAX_RETRIES)

    for attempt in range(1, total_attempts + 1):
        try:
            result = _post_messages(payload, headers, api_format)
            text = _extract_response_text(result)
            if text:
                return text
            retryable = attempt < total_attempts
            raise OverwatchAPIError(
                code="Error: EmptyContent",
                message="API returned empty content",
                retryable=retryable,
                response_preview=_response_preview(result),
            )
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts and _should_retry_error(exc):
                _sleep_before_retry(attempt)
                continue
            break

    if isinstance(last_error, OverwatchAPIError):
        return last_error.as_text()
    return f"[Overwatch Error: {type(last_error).__name__}] {str(last_error)}"
