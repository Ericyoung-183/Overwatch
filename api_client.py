"""API client for Overwatch. Supports Anthropic and OpenAI-compatible formats.
Zero external dependencies — uses only urllib."""
import json
import random
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from http.client import RemoteDisconnected

from prompts import TOOLS_SECTION, NO_TOOLS_SECTION


def _strip_tools_section(system_prompt: str) -> str:
    """Replace the TOOLS_SECTION in a system prompt with NO_TOOLS_SECTION.

    Used when degrading from agentic review to simple review — the prompt was
    built with tool instructions, but tools are no longer available.
    """
    if TOOLS_SECTION in system_prompt:
        return system_prompt.replace(TOOLS_SECTION, NO_TOOLS_SECTION)
    # Fallback: if exact match fails, try regex on the section header
    import re
    stripped = re.sub(r"\n## Tools\n.*?(?=\n## |\Z)", "\n" + NO_TOOLS_SECTION + "\n", system_prompt, flags=re.DOTALL)
    if "## Tools" not in stripped or NO_TOOLS_SECTION in stripped:
        return stripped
    _log("strip_tools_failed", reason="TOOLS_SECTION not found and regex fallback failed")
    return system_prompt


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


def _log(event: str, **fields):
    """Structured stderr logger for API client events (matches overwatch.py format)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    extras = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in fields.items())
    msg = f"[Overwatch {timestamp}] event={event}"
    if extras:
        msg += " " + extras
    print(msg, file=sys.stderr)


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
        thinking_parts = []  # Collect thinking blocks as fallback
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
                elif block.get("type") == "thinking" and block.get("thinking"):
                    thinking_parts.append(block["thinking"])
                elif isinstance(block.get("content"), str):
                    text_parts.append(block["content"])
            elif isinstance(block, str):
                text_parts.append(block)
        # Fallback: if no text blocks but thinking blocks exist, use thinking content
        if not text_parts and thinking_parts:
            text_parts = thinking_parts
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
        # Extended thinking only works with Claude models
        is_claude = "claude" in model.lower()
        if thinking and is_claude and max_tokens > 4000:
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
            # Diagnostic: log why extraction failed
            content = result.get("content", []) if isinstance(result, dict) else []
            block_types = [b.get("type", "?") if isinstance(b, dict) else type(b).__name__ for b in content] if isinstance(content, list) else []
            _log("empty_response", attempt=attempt, block_types=block_types,
                 response_preview=_response_preview(result)[:300])
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


def call_claude_with_tools(
    system_prompt: str,
    user_message: str,
    tool_definitions: list[dict],
    tool_executor,
    project_cwd: str = "",
    model: str = None,
    max_tokens: int = None,
    max_tool_rounds: int = 1,
) -> str:
    """Call Claude API with tool use in an agentic loop.

    The model can request tool calls (grep, read_file, etc.). We execute them
    locally and feed results back, up to max_tool_rounds. Returns the final
    text response.

    Only supports Anthropic format (tool_use is Anthropic-native).
    Falls back to call_claude() if API_FORMAT is 'openai' or model is not Claude.
    """
    if API_FORMAT == "openai":
        # OpenAI tool calling has a different schema; fall back to simple call
        _log("agentic_review_skip", reason="openai_format_no_tools", model=model or REVIEW_MODEL)
        return call_claude(system_prompt, user_message, model, max_tokens, thinking=False)

    model = model or REVIEW_MODEL
    max_tokens = max_tokens or MAX_REVIEW_TOKENS
    is_claude = "claude" in model.lower()

    if not is_claude:
        # Non-Claude models via Anthropic format don't reliably support tool_use;
        # fall back to simple call without tools
        _log("agentic_review_skip", reason="non_claude_model_no_tools", model=model)
        return call_claude(system_prompt, user_message, model, max_tokens, thinking=False)

    messages = [{"role": "user", "content": user_message}]

    payload_base = {
        "model": model,
        "max_tokens": min(max_tokens, 8000),  # Cap agentic review output to reduce proxy timeout risk
        "system": system_prompt,
        "tools": tool_definitions,
    }
    # No extended thinking for agentic review — tools provide verification,
    # and thinking + tools + large context causes proxy timeouts

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if API_AUTH_TOKEN:
        headers["x-api-key"] = API_AUTH_TOKEN

    total_tool_calls = 0

    for round_num in range(max_tool_rounds + 1):
        payload = {**payload_base, "messages": messages}

        # Retry logic (same as call_claude)
        result = None
        for attempt in range(1, max(1, API_MAX_RETRIES) + 1):
            try:
                result = _post_messages(payload, headers, "anthropic")
                break
            except Exception as exc:
                if attempt < API_MAX_RETRIES and _should_retry_error(exc):
                    _sleep_before_retry(attempt)
                    continue
                # Round 2+ API error — degrade to simple review without tools.
                # Round 1 text is transitional ("Let me check..."), not a review conclusion.
                if total_tool_calls > 0:
                    _log("agentic_degraded_to_simple", reason="api_error_after_tools", round=round_num + 1,
                         total_tool_calls=total_tool_calls, error=str(exc)[:200])
                    degraded_prompt = _strip_tools_section(system_prompt)
                    return call_claude(degraded_prompt, user_message, model, max_tokens, thinking=False)
                if isinstance(exc, OverwatchAPIError):
                    return exc.as_text()
                return f"[Overwatch Error: {type(exc).__name__}] {str(exc)}"

        # Extract content blocks
        content = result.get("content", [])
        stop_reason = result.get("stop_reason", "end_turn")

        # Diagnostic: log content block types for debugging empty responses
        if isinstance(content, list) and content:
            block_types = [b.get("type", "?") if isinstance(b, dict) else type(b).__name__ for b in content]
            _log("response_blocks", round=round_num + 1, stop_reason=stop_reason, blocks=block_types)
        else:
            # content is empty/None — check if response is wrapped in a proxy envelope
            data = result.get("data") if isinstance(result, dict) else None
            if isinstance(data, dict):
                inner_content = data.get("content", [])
                inner_stop = data.get("stop_reason", "?")
                inner_types = [b.get("type", "?") if isinstance(b, dict) else type(b).__name__ for b in inner_content] if isinstance(inner_content, list) else []
                _log("proxy_envelope", round=round_num + 1, inner_stop=inner_stop, inner_types=inner_types,
                     data_keys=list(data.keys()))
            else:
                data_type = type(result.get("data")).__name__ if isinstance(result, dict) and "data" in result else "no_data_key"
                data_preview = str(result.get("data", ""))[:200] if isinstance(result, dict) and "data" in result else ""
                _log("empty_content_no_data", round=round_num + 1, result_keys=list(result.keys()) if isinstance(result, dict) else None,
                     data_type=data_type, data_preview=data_preview)

        # Collect text parts and tool calls
        text_parts = []
        thinking_parts = []
        tool_calls = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                text_parts.append(block["text"])
            elif block.get("type") == "thinking" and block.get("thinking"):
                thinking_parts.append(block["thinking"])
            elif block.get("type") == "tool_use":
                tool_calls.append(block)
        # Fallback: if no text blocks but thinking blocks exist, use thinking content
        if not text_parts and thinking_parts:
            text_parts = thinking_parts

        # If no tool calls or we've hit the round limit, return text
        if not tool_calls or round_num >= max_tool_rounds:
            if total_tool_calls > 0:
                _log("agentic_review_complete", rounds=round_num + 1, total_tool_calls=total_tool_calls)
            final_text = "\n".join(text_parts).strip() if text_parts else _extract_response_text(result)
            if not final_text:
                _log("agentic_review_empty", rounds=round_num + 1, total_tool_calls=total_tool_calls,
                     text_parts=len(text_parts), stop_reason=stop_reason,
                     response_keys=list(result.keys()) if isinstance(result, dict) else None)
            return final_text

        # Execute tool calls and build tool results
        # First, add the assistant's response (with tool_use blocks) to messages
        messages.append({"role": "assistant", "content": content})

        tool_results = []
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_input = tc.get("input", {})
            tool_id = tc.get("id", "")

            total_tool_calls += 1
            input_preview = json.dumps(tool_input, ensure_ascii=False)[:200]
            _log("tool_call", round=round_num + 1, tool=tool_name, input=input_preview)

            tool_output = tool_executor(tool_name, tool_input, project_cwd)

            # Truncate large tool outputs to avoid proxy timeouts on subsequent rounds
            MAX_TOOL_RESULT_CHARS = 8000
            if len(tool_output) > MAX_TOOL_RESULT_CHARS:
                tool_output = tool_output[:MAX_TOOL_RESULT_CHARS] + f"\n... [truncated, {len(tool_output)} chars total]"

            _log("tool_result", round=round_num + 1, tool=tool_name, result_length=len(tool_output))

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": tool_output,
            })

        messages.append({"role": "user", "content": tool_results})

    # Shouldn't reach here, but just in case
    _log("agentic_review_exhausted", rounds=max_tool_rounds, total_tool_calls=total_tool_calls)
    return "\n".join(text_parts).strip() if text_parts else "[Overwatch Error: tool loop exhausted]"
