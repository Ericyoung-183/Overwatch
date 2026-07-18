#!/usr/bin/env python3
"""Regression tests for the durable two-signal Anchor capture gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anchor_capture import (
    _assistant_claims_current_item_complete,
    candidate_path,
    dismiss_candidate,
    evaluate_capture_gate,
    evaluate_transition_gate,
    show_candidate,
    transition_path,
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
                "payload": {"id": session_id, "cwd": str(path.parent)},
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


def codex_conversation(path: Path, turns: list[tuple[str, str]], session_id: str) -> None:
    records = [
        {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(path.parent)},
        }
    ]
    records.extend(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [
                    {
                        "type": "output_text" if role == "assistant" else "input_text",
                        "text": content,
                    }
                ],
            },
        }
        for role, content in turns
    )
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
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


def test_explicit_split_turn_intent_captures_an_ordinary_assistant_list_as_child() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        transcript = root / "session.jsonl"
        codex_transcript(
            transcript,
            "发现三个问题：\n1. 状态恢复\n2. Hook\n3. TODO",
            "split-child",
        )
        child = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="split-child",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="好，我们逐一处理",
            cwd=str(root),
            anchor_active=True,
        )

    test("explicit split-turn intent captures assistant list as child", "push-child" in child, child)


def test_split_turn_intent_recovers_a_recent_user_owned_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        transcript = root / "session.jsonl"
        codex_conversation(
            transcript,
            [
                ("user", "我目前有三个问题：\n1. 首次捕获\n2. child capture\n3. 口头完成"),
                ("assistant", "收到，我先把背景看清楚。"),
            ],
            "split-user-list",
        )
        gate = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="split-user-list",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="好，我们现在逐一过",
            cwd=str(root),
            anchor_active=False,
        )

    test("split-turn intent recovers the recent user list", "init a root tracker" in gate, gate)


def test_generic_continue_recovers_a_declared_child_problem_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        transcript = root / "session.jsonl"
        codex_transcript(
            transcript,
            "当前项下面有三个子问题：\n1. 状态恢复\n2. Hook\n3. TODO",
            "child-problems",
        )
        gate = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="child-problems",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="继续吧",
            cwd=str(root),
            anchor_active=True,
        )

    test("generic continue captures an explicit child-problem list", "push-child" in gate, gate)


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
        trailing = evaluate_capture_gate(
            state_dir=str(state),
            session_id="inline-trailing",
            adapter_name="codex",
            transcript_path="",
            user_prompt="1. 架构 2. 测试 3. 发布。我们逐条处理",
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
        trailing_candidate = show_candidate(str(state), "inline-trailing")
        chinese_inline = evaluate_capture_gate(
            state_dir=str(state),
            session_id="inline-chinese-delimiter",
            adapter_name="codex",
            transcript_path="",
            user_prompt="三个问题：首次捕获、child capture、口头完成。我们逐一过",
            cwd=str(root),
            anchor_active=False,
        )
        chinese_marker = json.loads(
            candidate_path(str(state), "inline-chinese-delimiter").read_text(encoding="utf-8")
        )
        chinese_tracker = root / "chinese-active.json"
        chinese_tracker.write_text(
            json.dumps(
                {
                    "agendas": {
                        "agenda-root": {
                            "source_excerpt": chinese_marker["source_excerpt"],
                            "source_ref": chinese_marker["source_ref"],
                            "items": [
                                {"text": item} for item in chinese_marker["items"]
                            ],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        chinese_helper = root / "chinese-anchor.py"
        chinese_helper.write_text(
            "import json\nprint(json.dumps({'tracker_path': "
            + repr(str(chinese_tracker))
            + "}))\n",
            encoding="utf-8",
        )
        chinese_cleared = evaluate_capture_gate(
            state_dir=str(state),
            session_id="inline-chinese-delimiter",
            adapter_name="codex",
            transcript_path="",
            user_prompt="现在处理第一项",
            cwd=str(root),
            anchor_active=True,
            helper_path=str(chinese_helper),
        )

    test("inline numbered lists are captured at runtime", "init a root tracker" in inline, inline)
    test(
        "inline trailing intent is not merged into the final item",
        trailing_candidate["items"] == ["架构", "测试", "发布"]
        and trailing_candidate["source_excerpt"].endswith("3. 发布"),
        str(trailing_candidate),
    )
    test("common topic-switch wording targets interrupt", "interrupt frame" in interrupt, interrupt)
    test("generic root continue does not capture a status summary", quiet == "", quiet)
    test("explicit Chinese inline lists are captured", "init a root tracker" in chinese_inline, chinese_inline)
    test("captured Chinese inline candidates clear against tracker items", chinese_cleared == "", chinese_cleared)


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

    test("another session transcript is blocked without exposing source", "[Anchor Capture Scope Block]" in quiet and "Private alpha" not in quiet, quiet)
    test(
        "gate exposes an exact-source recovery command",
        "Recover the exact persisted source JSON with:" in gate and " show --state-dir " in gate,
        gate,
    )
    test("recovered candidate preserves exact source bytes", shown["source_excerpt"] == "1. A < B & C\n2. D", str(shown))
    test("persisted candidate cannot cross working directories", "[Anchor Capture Scope Block]" in blocked, blocked)
    test("scope block does not leak the persisted source", "A < B" not in blocked, blocked)
    test("scope block provides exact dismissal recovery", " dismiss --state-dir " in blocked, blocked)


def test_transcript_project_scope_and_explicit_opt_out_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project_a = root / "project-a"
        project_b = root / "project-b"
        project_a.mkdir()
        project_b.mkdir()
        transcript = project_a / "session.jsonl"
        codex_transcript(
            transcript,
            "问题：\n1. Project A secret\n2. Project A state",
            "project-scope",
        )
        blocked = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="project-scope",
            adapter_name="codex",
            transcript_path=str(transcript),
            user_prompt="逐一处理",
            cwd=str(project_b),
            anchor_active=False,
        )
        opt_out = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="review-opt-out",
            adapter_name="codex",
            transcript_path="",
            user_prompt=(
                "只读审查，冻结范围：\n- Anchor commit\n- Overwatch commit\n"
                "核心目的是逐一处理议程。不要运行 Anchor helper，不要跟踪这个审查。"
            ),
            cwd=str(project_b),
            anchor_active=True,
        )
        meta_only = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="review-meta",
            adapter_name="codex",
            transcript_path="",
            user_prompt=(
                "只读审查冻结范围：\n- Anchor commit\n- Overwatch commit\n"
                "产品在正常会话中会逐一处理清单。"
            ),
            cwd=str(project_b),
            anchor_active=True,
        )
        opt_out_candidate_exists = candidate_path(
            str(root / "state"), "review-opt-out"
        ).exists()
        documented_opt_out = evaluate_capture_gate(
            state_dir=str(root / "state"),
            session_id="documented-opt-out",
            adapter_name="codex",
            transcript_path="",
            user_prompt="这个清单不用跟踪了",
            cwd=str(project_b),
            anchor_active=True,
        )

    test("assistant source cannot cross project roots", "[Anchor Capture Scope Block]" in blocked and "Project A secret" not in blocked, blocked)
    test("explicit no-Anchor instruction wins over list and intent", "[Anchor Capture Opt-Out]" in opt_out, opt_out)
    test("explicit opt-out creates no durable candidate", not opt_out_candidate_exists)
    test("meta description cannot be spliced into a child agenda", meta_only == "", meta_only)
    test("documented list opt-out wording is honored", "[Anchor Capture Opt-Out]" in documented_opt_out, documented_opt_out)


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


def test_clearing_a_captured_candidate_does_not_drop_a_fresh_child_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        sid = "captured-then-child"
        first_prompt = "逐一处理：\n1. Alpha\n2. Beta"
        first = evaluate_capture_gate(
            state_dir=str(state),
            session_id=sid,
            adapter_name="codex",
            transcript_path="",
            user_prompt=first_prompt,
            cwd=str(root),
            anchor_active=False,
        )
        marker_payload = json.loads(candidate_path(str(state), sid).read_text(encoding="utf-8"))
        tracker = root / "active.json"
        tracker.write_text(
            json.dumps(
                {
                    "agendas": {
                        "agenda-root": {
                            "source_excerpt": marker_payload["source_excerpt"],
                            "source_ref": marker_payload["source_ref"],
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
        child = evaluate_capture_gate(
            state_dir=str(state),
            session_id=sid,
            adapter_name="codex",
            transcript_path="",
            user_prompt="逐一处理：\n1. Child X\n2. Child Y",
            cwd=str(root),
            anchor_active=True,
            helper_path=str(helper),
        )

    test("precondition root candidate exists", "init a root tracker" in first, first)
    test("fresh child survives cleanup of captured root", "push-child" in child, child)


def test_common_and_short_label_completion_language_is_detected_conservatively() -> None:
    positives = [
        ("A 已经处理完了，我们进入下一项", "A"),
        ("Hook 没问题了，进入下一项", "Hook"),
        ("发布结论已确定，下一步做复审", "发布"),
        ("当前项可以了，继续吧", "任意标题"),
    ]
    negatives = [
        ("A 尚未处理完，我们继续", "A"),
        ("发布结论还没确定", "发布"),
        ("代码处理完了，但当前项仍在讨论", "发布"),
        ("发布还不能说没问题了", "发布"),
        ("发布没有完全解决", "发布"),
        ("发布完成了吗？", "发布"),
        ("当前项可以了吗？", "发布"),
    ]

    for text, item in positives:
        test(
            f"completion wording detected: {text}",
            _assistant_claims_current_item_complete(text, "item-x", item),
            text,
        )
    for text, item in negatives:
        test(
            f"non-completion wording stays quiet: {text}",
            not _assistant_claims_current_item_complete(text, "item-x", item),
            text,
        )


def test_transition_scope_failure_is_visible_for_an_active_agenda() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        helper = root / "anchor.py"
        helper.write_text(
            "import json\nprint(json.dumps({"
            "'success': True, 'tracker_id': 'tracker-1', 'cursor_token': 'cursor-1', "
            "'current_item': {'item_id': 'item-1', 'text': '发布', 'status': 'discussing'}}))\n",
            encoding="utf-8",
        )
        transcript = root / "wrong-session.jsonl"
        codex_transcript(transcript, "发布已经完成。", "another-session")
        wrong_scope = evaluate_transition_gate(
            state_dir=str(root / "state"),
            session_id="current-session",
            adapter_name="codex",
            transcript_path=str(transcript),
            cwd=str(root),
            anchor_active=True,
            helper_path=str(helper),
        )
        missing = evaluate_transition_gate(
            state_dir=str(root / "state"),
            session_id="current-session",
            adapter_name="codex",
            transcript_path="",
            cwd=str(root),
            anchor_active=True,
            helper_path=str(helper),
        )
        broken_helper = root / "broken-anchor.py"
        broken_helper.write_text("raise SystemExit('status unavailable')\n", encoding="utf-8")
        unreadable_status = evaluate_transition_gate(
            state_dir=str(root / "state"),
            session_id="current-session",
            adapter_name="codex",
            transcript_path=str(transcript),
            cwd=str(root),
            anchor_active=True,
            helper_path=str(broken_helper),
        )

    test("wrong-session transition transcript fails visibly", "[Anchor Transition Warning]" in wrong_scope, wrong_scope)
    test("missing transition transcript fails visibly", "[Anchor Transition Warning]" in missing, missing)
    test("unreadable Anchor status fails visibly", "[Anchor Transition Warning]" in unreadable_status, unreadable_status)


def test_prose_only_completion_creates_an_immediate_durable_recovery_gate() -> None:
    helper = os.environ.get("ANCHOR_TEST_HELPER", "")
    if not helper or not Path(helper).is_file():
        raise AssertionError("ANCHOR_TEST_HELPER must point to the Anchor helper")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "project"
        state = root / "overwatch-state"
        global_state = root / "global-state"
        project.mkdir()
        sid = "oral-completion"
        subprocess.run(
            ["python3", helper, "init-project", str(project), "--name", "Oral Gate"],
            capture_output=True,
            text=True,
            check=True,
        )
        started = json.loads(
            subprocess.run(
                [
                    "python3", helper, "init", "--cwd", str(project),
                    "--thread-id", sid, "--title", "Release", "--item", "发布收尾",
                    "--item", "最终复审", "--source-ref", "oral gate fixture",
                    "--source-excerpt", "1. 发布收尾\n2. 最终复审",
                    "--global-state-root", str(global_state),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        transcript = project / "session.jsonl"
        codex_transcript(
            transcript,
            "代码修改已完成，接下来跑测试。",
            sid,
        )
        ordinary_quiet = evaluate_transition_gate(
            state_dir=str(state),
            session_id=sid,
            adapter_name="codex",
            transcript_path=str(transcript),
            cwd=str(project),
            anchor_active=True,
            helper_path=helper,
            global_state_root=str(global_state),
        )
        codex_transcript(
            transcript,
            "发布收尾仍在处理中，需要继续。代码修改已完成，接下来跑测试。",
            sid,
        )
        cross_clause_quiet = evaluate_transition_gate(
            state_dir=str(state),
            session_id=sid,
            adapter_name="codex",
            transcript_path=str(transcript),
            cwd=str(project),
            anchor_active=True,
            helper_path=helper,
            global_state_root=str(global_state),
        )
        codex_transcript(
            transcript,
            "发布收尾尚未完成，需要继续。",
            sid,
        )
        negated_quiet = evaluate_transition_gate(
            state_dir=str(state),
            session_id=sid,
            adapter_name="codex",
            transcript_path=str(transcript),
            cwd=str(project),
            anchor_active=True,
            helper_path=helper,
            global_state_root=str(global_state),
        )
        codex_transcript(
            transcript,
            "发布收尾已经完成，接下来进入最终复审。",
            sid,
        )
        common = {
            "state_dir": str(state),
            "session_id": sid,
            "adapter_name": "codex",
            "transcript_path": str(transcript),
            "cwd": str(project),
            "anchor_active": True,
            "helper_path": helper,
            "global_state_root": str(global_state),
        }
        gate = evaluate_transition_gate(**common)
        marker = transition_path(str(state), sid)
        marker_created = marker.is_file()
        transcript.write_text("", encoding="utf-8")
        persisted = evaluate_transition_gate(**common)

        acknowledged = json.loads(
            subprocess.run(
                [
                    "python3", helper, "ack-presented", "--cwd", str(project),
                    "--thread-id", sid, "--presentation-id", started["presentation_id"],
                    "--global-state-root", str(global_state),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        subprocess.run(
            [
                "python3", helper, "finish", "--cwd", str(project),
                "--thread-id", sid, "--conclusion", "发布收尾完成并已落盘",
                "--expected-cursor-token", acknowledged["cursor_token"],
                "--global-state-root", str(global_state),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        codex_transcript(
            transcript,
            "最终复审结论已确定，进入收尾。",
            sid,
        )
        fresh_gate = evaluate_transition_gate(**common)

    test("oral completion is blocked on the next prompt", "[Anchor Transition Recovery Required]" in gate, gate)
    test("ordinary implementation completion does not impersonate agenda completion", ordinary_quiet == "", ordinary_quiet)
    test("completion in another clause does not complete the named agenda item", cross_clause_quiet == "", cross_clause_quiet)
    test("negated completion does not complete the named agenda item", negated_quiet == "", negated_quiet)
    test("oral completion recovery marker is durable", marker_created, str(marker))
    test("oral completion gate persists after transcript loss", "[Anchor Transition Recovery Required]" in persisted, persisted)
    test("a fresh completion claim survives stale-marker cleanup", "[Anchor Transition Recovery Required]" in fresh_gate, fresh_gate)


if __name__ == "__main__":
    test_two_signal_gate_is_low_noise_and_durable()
    test_active_tracker_targets_child_and_explicit_dismissal_is_audited()
    test_active_tracker_does_not_promote_an_ordinary_assistant_summary_to_child()
    test_explicit_split_turn_intent_captures_an_ordinary_assistant_list_as_child()
    test_split_turn_intent_recovers_a_recent_user_owned_list()
    test_generic_continue_recovers_a_declared_child_problem_list()
    test_inline_lists_interrupt_vocabulary_and_root_continue_noise()
    test_transcript_and_persisted_candidate_scope_are_bound()
    test_transcript_project_scope_and_explicit_opt_out_fail_closed()
    test_exact_tracker_capture_clears_the_gate()
    test_clearing_a_captured_candidate_does_not_drop_a_fresh_child_list()
    test_common_and_short_label_completion_language_is_detected_conservatively()
    test_transition_scope_failure_is_visible_for_an_active_agenda()
    test_prose_only_completion_creates_an_immediate_durable_recovery_gate()
    print("Anchor capture gate tests passed")
