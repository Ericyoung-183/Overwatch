#!/usr/bin/env python3
"""Regression tests for the durable two-signal Anchor capture gate."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anchor_capture import (
    candidate_path,
    dismiss_candidate,
    evaluate_capture_gate,
    show_candidate,
)


def test(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"  PASS {name}")


def codex_transcript(path: Path, assistant_text: str, session_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": session_id},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": assistant_text}],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_two_signal_gate_is_low_noise_and_durable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        transcript = root / "session.jsonl"
        codex_transcript(
            transcript,
            "检查结果：\n1. 修复首次捕获\n2. 修复 child capture",
            "capture-session",
        )
        common = {
            "state_dir": str(state),
            "session_id": "capture-session",
            "adapter_name": "codex",
            "transcript_path": str(transcript),
            "cwd": str(root),
        }

        no_intent = evaluate_capture_gate(
            **common,
            user_prompt="请解释一下这两个问题",
            anchor_active=False,
        )
        gate = evaluate_capture_gate(
            **common,
            user_prompt="好的，我们逐一处理",
            anchor_active=False,
        )
        marker = candidate_path(str(state), "capture-session")
        transcript.write_text("", encoding="utf-8")
        persisted = evaluate_capture_gate(
            **common,
            user_prompt="现在先做第一项",
            anchor_active=False,
        )

        test("an explanatory list without sequential intent stays quiet", no_intent == "", no_intent)
        test("two signals create the capture gate", "[Anchor Capture Required]" in gate, gate)
        test("root candidate requires init", "init a root tracker" in gate, gate)
        test("candidate marker is durable", marker.is_file(), str(marker))
        test("gate persists after the source leaves the latest transcript", "[Anchor Capture Required]" in persisted, persisted)


def test_active_tracker_targets_child_and_explicit_dismissal_is_audited() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        gate = evaluate_capture_gate(
            state_dir=str(state),
            session_id="child-capture",
            adapter_name="codex",
            transcript_path="",
            user_prompt="逐条处理：\n- child one\n- child two",
            cwd=str(root),
            anchor_active=True,
        )
        result = dismiss_candidate(str(state), "child-capture", "These are examples, not work items")
        marker = candidate_path(str(state), "child-capture")
        receipt = Path(result["receipt_path"])

        test("active agenda candidate requires push-child", "push-child under the current item" in gate, gate)
        test("explicit dismissal clears the pending gate", not marker.exists(), str(marker))
        test("dismissal leaves a reason-bearing receipt", receipt.is_file() and "examples" in receipt.read_text(encoding="utf-8"), str(receipt))

        interrupt = evaluate_capture_gate(
            state_dir=str(state),
            session_id="interrupt-capture",
            adapter_name="codex",
            transcript_path="",
            user_prompt="临时切换处理，逐条过：\n1. Urgent one\n2. Urgent two",
            cwd=str(root),
            anchor_active=True,
        )
        test("temporary topic list targets an interrupt frame", "create an Anchor interrupt frame" in interrupt, interrupt)


def test_active_tracker_does_not_promote_an_ordinary_assistant_summary_to_child() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        transcript = root / "session.jsonl"
        codex_transcript(
            transcript,
            "当前进展：\n1. Tests are green\n2. Docs are updated",
            "active-summary",
        )
        quiet = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="active-summary",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="请继续",
            cwd=str(root),
            anchor_active=True,
        )
        codex_transcript(
            transcript,
            "当前项形成子清单：\n1. Verify install\n2. Verify remote",
            "active-child-declared",
        )
        child = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="active-child-declared",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="请继续",
            cwd=str(root),
            anchor_active=True,
        )

    test("ordinary assistant progress summary stays outside child capture", quiet == "", quiet)
    test("explicit assistant child declaration remains capturable", "push-child" in child, child)


def test_inline_lists_interrupt_vocabulary_and_root_continue_noise() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        inline = evaluate_capture_gate(
            state_dir=str(state),
            session_id="inline-user",
            adapter_name="codex",
            transcript_path="",
            user_prompt="我看到三个问题，我们逐一处理：1. 架构 2. 测试 3. 发布",
            cwd=str(root),
            anchor_active=False,
        )
        interrupt = evaluate_capture_gate(
            state_dir=str(state),
            session_id="interrupt-vocabulary",
            adapter_name="codex",
            transcript_path="",
            user_prompt="打断一下，逐条处理：1. Urgent one 2. Urgent two",
            cwd=str(root),
            anchor_active=True,
        )
        transcript = root / "summary.jsonl"
        codex_transcript(
            transcript,
            "当前进展：1. Tests are green 2. Docs are updated",
            "root-summary",
        )
        quiet = evaluate_capture_gate(
            state_dir=str(state),
            session_id="root-summary",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="请继续实现剩余代码",
            cwd=str(root),
            anchor_active=False,
        )

    test("inline numbered lists are captured at runtime", "init a root tracker" in inline, inline)
    test("common topic-switch wording targets interrupt", "interrupt frame" in interrupt, interrupt)
    test("generic root continue does not capture a status summary", quiet == "", quiet)


def test_transcript_and_persisted_candidate_scope_are_bound() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        transcript = root / "wrong-session.jsonl"
        codex_transcript(
            transcript,
            "审查项：1. Private alpha 2. Private beta",
            "another-session",
        )
        quiet = evaluate_capture_gate(
            state_dir=str(state),
            session_id="current-session",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="逐一处理",
            cwd=str(root),
            anchor_active=False,
        )
        source = "逐条处理：\n1. A < B & C\n2. D"
        gate = evaluate_capture_gate(
            state_dir=str(state),
            session_id="scoped-session",
            adapter_name="codex",
            transcript_path="",
            user_prompt=source,
            cwd=str(root),
            anchor_active=False,
        )
        shown = show_candidate(str(state), "scoped-session")
        other = root / "other"
        other.mkdir()
        blocked = evaluate_capture_gate(
            state_dir=str(state),
            session_id="scoped-session",
            adapter_name="codex",
            transcript_path="",
            user_prompt="继续处理",
            cwd=str(other),
            anchor_active=False,
        )

    test("another session transcript cannot seed capture", quiet == "", quiet)
    test(
        "gate exposes an exact-source recovery command",
        "Recover the exact persisted source JSON with:" in gate and " show --state-dir " in gate,
        gate,
    )
    test("recovered candidate preserves exact source bytes", shown["source_excerpt"] == "1. A < B & C\n2. D", str(shown))
    test("persisted candidate cannot cross working directories", "[Anchor Capture Scope Block]" in blocked, blocked)
    test("scope block does not leak the persisted source", "A < B" not in blocked, blocked)


def test_exact_tracker_capture_clears_the_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        common = {
            "state_dir": str(state),
            "session_id": "captured-session",
            "adapter_name": "codex",
            "transcript_path": "",
            "user_prompt": "逐一处理：\n1. Alpha\n2. Beta",
            "cwd": str(root),
            "anchor_active": False,
        }
        first = evaluate_capture_gate(**common)
        tracker = root / "active.json"
        marker_payload = json.loads(
            candidate_path(str(state), "captured-session").read_text(encoding="utf-8")
        )
        tracker.write_text(
            json.dumps(
                {
                    "agendas": {
                        "agenda-root": {
                            "source_excerpt": "1. Alpha\n2. Beta",
                            "source_ref": "older agenda with the same item text",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        helper = root / "anchor.py"
        helper.write_text(
            "import json\nprint(json.dumps({'tracker_path': " + repr(str(tracker)) + "}))\n",
            encoding="utf-8",
        )
        still_pending = evaluate_capture_gate(**common, helper_path=str(helper))
        tracker.write_text(
            json.dumps(
                {
                    "agendas": {
                        "agenda-root": {
                            "source_excerpt": "1. Alpha\n2. Beta",
                            "source_ref": marker_payload["source_ref"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cleared = evaluate_capture_gate(**common, helper_path=str(helper))

        test("precondition gate was created", "[Anchor Capture Required]" in first, first)
        test("an older same-text agenda does not clear a new capture", "[Anchor Capture Required]" in still_pending, still_pending)
        test("exact source capture clears the durable gate", cleared == "", cleared)
        test("captured marker is removed", not candidate_path(str(state), "captured-session").exists())


if __name__ == "__main__":
    test_two_signal_gate_is_low_noise_and_durable()
    test_active_tracker_targets_child_and_explicit_dismissal_is_audited()
    test_active_tracker_does_not_promote_an_ordinary_assistant_summary_to_child()
    test_inline_lists_interrupt_vocabulary_and_root_continue_noise()
    test_transcript_and_persisted_candidate_scope_are_bound()
    test_exact_tracker_capture_clears_the_gate()
    print("Anchor capture gate tests passed")
