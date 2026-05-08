#!/usr/bin/env python3
"""Regression tests for runtime-aware Overwatch defaults."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def load_config(env_updates: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "OVERWATCH_ADAPTER",
        "OVERWATCH_BACKEND",
        "OVERWATCH_REVIEW_MODEL",
        "CODEX_THREAD_ID",
        "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    ]:
        env.pop(key, None)
    env.update(env_updates)

    code = """
import json
import sys
sys.path.insert(0, %r)
import config
print(json.dumps({
    "adapter": config.ADAPTER,
    "backend": config.REVIEW_BACKEND,
    "model": config.REVIEW_MODEL,
}, sort_keys=True))
""" % str(ROOT)
    output = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
    return json.loads(output)


def test_non_codex_defaults_keep_api_backend() -> None:
    cfg = load_config({})

    test("non-Codex default adapter stays claude_code", cfg["adapter"] == "claude_code", str(cfg))
    test("non-Codex default backend stays api", cfg["backend"] == "api", str(cfg))


def test_codex_desktop_defaults_use_codex_exec() -> None:
    cfg = load_config({"CODEX_THREAD_ID": "codex-thread-123"})

    test("Codex default adapter is codex", cfg["adapter"] == "codex", str(cfg))
    test("Codex default backend is codex_exec", cfg["backend"] == "codex_exec", str(cfg))
    test("Codex default review model is gpt-5.5", cfg["model"] == "gpt-5.5", str(cfg))


def test_explicit_env_overrides_runtime_defaults() -> None:
    cfg = load_config({
        "CODEX_THREAD_ID": "codex-thread-123",
        "OVERWATCH_ADAPTER": "claude_code",
        "OVERWATCH_BACKEND": "api",
        "OVERWATCH_REVIEW_MODEL": "custom-model",
    })

    test("explicit adapter override wins", cfg["adapter"] == "claude_code", str(cfg))
    test("explicit backend override wins", cfg["backend"] == "api", str(cfg))
    test("explicit model override wins", cfg["model"] == "custom-model", str(cfg))


if __name__ == "__main__":
    test_non_codex_defaults_keep_api_backend()
    test_codex_desktop_defaults_use_codex_exec()
    test_explicit_env_overrides_runtime_defaults()
    print("config runtime default tests passed")
