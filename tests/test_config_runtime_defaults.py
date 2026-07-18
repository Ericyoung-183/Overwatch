#!/usr/bin/env python3
"""Regression tests for runtime-aware Overwatch defaults."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
        "OVERWATCH_CODEX_REASONING_EFFORT",
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
    "codex_reasoning_effort": config.CODEX_REASONING_EFFORT,
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
    test("Codex default reasoning effort is xhigh", cfg["codex_reasoning_effort"] == "xhigh", str(cfg))


def test_explicit_env_overrides_runtime_defaults() -> None:
    cfg = load_config({
        "CODEX_THREAD_ID": "codex-thread-123",
        "OVERWATCH_ADAPTER": "claude_code",
        "OVERWATCH_BACKEND": "api",
        "OVERWATCH_REVIEW_MODEL": "custom-model",
        "OVERWATCH_CODEX_REASONING_EFFORT": "high",
    })

    test("explicit adapter override wins", cfg["adapter"] == "claude_code", str(cfg))
    test("explicit backend override wins", cfg["backend"] == "api", str(cfg))
    test("explicit model override wins", cfg["model"] == "custom-model", str(cfg))
    test("explicit reasoning effort override wins", cfg["codex_reasoning_effort"] == "high", str(cfg))


def test_project_allowlist_respects_path_boundaries() -> None:
    env = os.environ.copy()
    env["OVERWATCH_ALLOWED_PROJECTS"] = "/work/client-a"
    code = """
import json
import sys
sys.path.insert(0, %r)
from config import project_is_allowed
print(json.dumps([
    project_is_allowed('/work/client-a'),
    project_is_allowed('/work/client-a/src'),
    project_is_allowed('/work/client-a-secret'),
]))
""" % str(ROOT)
    result = json.loads(
        subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
    )

    test("allowlist accepts exact project", result[0] is True, str(result))
    test("allowlist accepts real descendant", result[1] is True, str(result))
    test("allowlist rejects sibling prefix", result[2] is False, str(result))


def test_all_runtime_guards_use_boundary_aware_allowlist() -> None:
    for hook_name in [
        "claude_code_stop.sh",
        "codex_stop.sh",
        "claude_code_prompt.sh",
        "codex_prompt.sh",
    ]:
        text = (ROOT / "hooks" / hook_name).read_text(encoding="utf-8")
        test(
            f"{hook_name} uses boundary-aware allowlist",
            "project_is_allowed" in text,
            text,
        )
        test(
            f"{hook_name} avoids raw path-prefix allowlist",
            '[[ "$CWD" == "$p"* ]]' not in text,
            text,
        )
    engine = (ROOT / "overwatch.py").read_text(encoding="utf-8")
    test("engine enforces project allowlist", "project_is_allowed(effective_cwd)" in engine, engine)


def test_manual_trigger_is_silent_outside_allowlist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        allowed = root / "allowed"
        outside = root / "allowed-secret"
        allowed.mkdir()
        outside.mkdir()
        transcript = root / "session.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        for hook_name in ["claude_code_prompt.sh", "codex_prompt.sh"]:
            state_dir = root / f"state-{hook_name}"
            env = {
                **os.environ,
                "HOME": str(root / "home"),
                "OVERWATCH_ALLOWED_PROJECTS": str(allowed),
                "OVERWATCH_STATE_DIR": str(state_dir),
                "OVERWATCH_LOG_FILE": str(root / f"{hook_name}.log"),
                "ANCHOR_DISABLE": "1",
            }
            payload = {
                "session_id": "outside-session",
                "cwd": str(outside),
                "user_prompt": "overwatch",
                "transcript_path": str(transcript),
            }
            result = subprocess.run(
                ["bash", str(ROOT / "hooks" / hook_name)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            output = json.loads(result.stdout)
            test(f"{hook_name} does not trigger outside allowlist", output == {"continue": True}, result.stdout)
            test(
                f"{hook_name} writes no trigger outside allowlist",
                not (state_dir / "triggers" / "outside-session.json").exists(),
                str(state_dir),
            )


def test_engine_rejects_direct_call_outside_allowlist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        allowed = root / "allowed"
        outside = root / "allowed-secret"
        allowed.mkdir()
        outside.mkdir()
        env = {
            **os.environ,
            "OVERWATCH_ALLOWED_PROJECTS": str(allowed),
            "OVERWATCH_STATE_DIR": str(root / "state"),
            "OVERWATCH_REVIEWS_DIR": str(root / "reviews"),
            "OVERWATCH_LOG_FILE": str(root / "overwatch.log"),
        }
        code = """
import sys
sys.path.insert(0, %r)
import overwatch
print(overwatch.run('outside-session', '/missing/transcript.jsonl', True, %r))
""" % (str(ROOT), str(outside))
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

    test("direct engine call is rejected before transcript access", result.stdout.strip() == "None", result.stdout)
    test("direct engine rejection is explicit", "run_rejected_project_allowlist" in result.stderr, result.stderr)


def test_invalid_session_ids_are_rejected_before_runtime_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "project"
        project.mkdir()
        transcript = root / "session.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        for hook_name in [
            "claude_code_prompt.sh",
            "codex_prompt.sh",
            "claude_code_stop.sh",
            "codex_stop.sh",
        ]:
            state_dir = root / f"state-{hook_name}"
            env = {
                **os.environ,
                "HOME": str(root / "home"),
                "OVERWATCH_ALLOWED_PROJECTS": str(project),
                "OVERWATCH_STATE_DIR": str(state_dir),
                "OVERWATCH_LOG_FILE": str(root / f"{hook_name}.log"),
                "ANCHOR_DISABLE": "1",
            }
            payload = {
                "session_id": "../escape",
                "cwd": str(project),
                "user_prompt": "overwatch",
                "transcript_path": str(transcript),
            }
            result = subprocess.run(
                ["bash", str(ROOT / "hooks" / hook_name)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            test(f"{hook_name} keeps invalid session non-blocking", json.loads(result.stdout) == {"continue": True}, result.stdout)
            test(
                f"{hook_name} writes no session artifacts for invalid id",
                not (state_dir / "session_map.json").exists()
                and not (state_dir / "triggers").exists(),
                str(state_dir),
            )

        env = {
            **os.environ,
            "OVERWATCH_ALLOWED_PROJECTS": str(project),
            "OVERWATCH_STATE_DIR": str(root / "engine-state"),
            "OVERWATCH_REVIEWS_DIR": str(root / "engine-reviews"),
        }
        code = """
import sys
sys.path.insert(0, %r)
import overwatch
print(overwatch.run('../escape', '/missing/transcript.jsonl', True, %r))
""" % (str(ROOT), str(project))
        engine = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

    test("engine rejects invalid session before transcript access", engine.stdout.strip() == "None", engine.stdout)
    test("engine logs invalid session rejection", "run_rejected_session_id" in engine.stderr, engine.stderr)


def test_cli_refuses_to_remove_unmanaged_result_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        state.mkdir()
        transcript = root / "session.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        sentinel = root / "keep.json"
        sentinel.write_text("keep\n", encoding="utf-8")
        env = {
            **os.environ,
            "OVERWATCH_STATE_DIR": str(state),
            "OVERWATCH_REVIEWS_DIR": str(root / "reviews"),
        }
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "overwatch.py"),
                "--session-id",
                "safe-session",
                "--transcript",
                str(transcript),
                "--force",
                "--result-file",
                str(sentinel),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        preserved = sentinel.read_text(encoding="utf-8")

    test("CLI rejects unmanaged result path", result.returncode != 0, result.stderr)
    test("CLI preserves unmanaged result file", preserved == "keep\n", preserved)
    test("CLI logs managed result boundary", "result_file_rejected" in result.stderr, result.stderr)


if __name__ == "__main__":
    test_non_codex_defaults_keep_api_backend()
    test_codex_desktop_defaults_use_codex_exec()
    test_explicit_env_overrides_runtime_defaults()
    test_project_allowlist_respects_path_boundaries()
    test_all_runtime_guards_use_boundary_aware_allowlist()
    test_manual_trigger_is_silent_outside_allowlist()
    test_engine_rejects_direct_call_outside_allowlist()
    test_invalid_session_ids_are_rejected_before_runtime_paths()
    test_cli_refuses_to_remove_unmanaged_result_file()
    print("config runtime default tests passed")
