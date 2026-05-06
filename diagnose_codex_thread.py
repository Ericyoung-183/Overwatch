#!/usr/bin/env python3
"""Classify Codex Desktop threads that appear stuck.

This is intentionally read-only. It combines Codex state, rollout JSONL,
local app logs, and Overwatch pending markers to identify where a turn stopped:
hook layer, main-thread dispatch, model sampling, tool continuation, or stop hook.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


HOME = Path.home()
DEFAULT_CODEX_DIR = HOME / ".codex"
DEFAULT_PROJECT_DIR = Path(__file__).resolve().parent.parent


def connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def get_thread(state_db: Path, thread_id: str) -> dict[str, Any] | None:
    con = connect(state_db)
    if con is None:
        return None
    try:
        row = con.execute(
            """
            select id, title, rollout_path, cwd, tokens_used, model, reasoning_effort,
                   datetime(created_at, 'unixepoch', 'localtime') as created_local,
                   datetime(updated_at, 'unixepoch', 'localtime') as updated_local
            from threads
            where id = ?
            """,
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def find_threads_by_title(state_db: Path, title: str) -> list[dict[str, Any]]:
    con = connect(state_db)
    if con is None:
        return []
    try:
        rows = con.execute(
            """
            select id, title, rollout_path, cwd, tokens_used, model, reasoning_effort,
                   datetime(created_at, 'unixepoch', 'localtime') as created_local,
                   datetime(updated_at, 'unixepoch', 'localtime') as updated_local
            from threads
            where title like ?
            order by updated_at desc
            limit 20
            """,
            (f"%{title}%",),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def parse_rollout(path: Path) -> dict[str, Any]:
    counts: collections.Counter[str] = collections.Counter()
    event_counts: collections.Counter[str] = collections.Counter()
    response_counts: collections.Counter[str] = collections.Counter()
    last_items: list[str] = []
    last_event = ""
    last_response = ""
    task_started = 0
    task_complete = 0
    last_agent_idx = -1
    last_tool_output_idx = -1

    if not path.exists():
        return {"missing": True}

    line_count = 0
    with path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh, start=1):
            line_count = idx
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                counts["json_decode_error"] += 1
                continue

            obj_type = obj.get("type", "")
            payload = obj.get("payload") or {}
            subtype = payload.get("type") or payload.get("name") or ""
            key = f"{obj_type}:{subtype}".rstrip(":")
            counts[key] += 1

            if obj_type == "event_msg":
                event_counts[str(subtype)] += 1
                last_event = str(subtype)
                if subtype == "task_started":
                    task_started += 1
                elif subtype == "task_complete":
                    task_complete += 1
                elif subtype == "agent_message":
                    last_agent_idx = idx
            elif obj_type == "response_item":
                response_counts[str(subtype)] += 1
                last_response = str(subtype)
                if subtype in {"function_call_output", "custom_tool_call_output"}:
                    last_tool_output_idx = idx
                elif subtype == "message":
                    role = payload.get("role") or payload.get("item", {}).get("role")
                    if role == "assistant":
                        last_agent_idx = idx

            if obj_type in {"event_msg", "response_item"}:
                label = subtype or obj_type
                status = payload.get("status")
                if status:
                    label = f"{label}({status})"
                last_items.append(label)
                last_items = last_items[-12:]

    return {
        "missing": False,
        "line_count": line_count,
        "counts": dict(counts),
        "event_counts": dict(event_counts),
        "response_counts": dict(response_counts),
        "last_items": last_items,
        "last_event": last_event,
        "last_response": last_response,
        "task_started": task_started,
        "task_complete": task_complete,
        "unfinished_turns": task_started - task_complete,
        "last_agent_idx": last_agent_idx,
        "last_tool_output_idx": last_tool_output_idx,
    }


def query_log_counts(logs_db: Path, thread_id: str) -> dict[str, Any]:
    con = connect(logs_db)
    if con is None:
        return {"missing": True}
    try:
        total = con.execute(
            "select count(*) as c from logs where thread_id = ?", (thread_id,)
        ).fetchone()["c"]
        targets = con.execute(
            """
            select level, target, count(*) as c
            from logs
            where thread_id = ?
            group by level, target
            order by c desc
            limit 12
            """,
            (thread_id,),
        ).fetchall()
        errors = con.execute(
            """
            select datetime(ts, 'unixepoch', 'localtime') as ts_local, level, target,
                   substr(replace(coalesce(feedback_log_body, ''), char(10), ' '), 1, 240) as body
            from logs
            where thread_id = ?
              and (level in ('WARN', 'ERROR') or feedback_log_body like '%timeout%'
                   or feedback_log_body like '%timed out%' or feedback_log_body like '%abort%'
                   or feedback_log_body like '%error%')
            order by ts, ts_nanos
            limit 20
            """,
            (thread_id,),
        ).fetchall()
        return {
            "missing": False,
            "exact_log_rows": total,
            "top_targets": [dict(row) for row in targets],
            "warnings_or_errors": [dict(row) for row in errors],
        }
    finally:
        con.close()


def search_overwatch(project_dir: Path, thread_id: str) -> dict[str, Any]:
    log_path = project_dir / "overwatch" / "overwatch.log"
    result = {
        "hook_lines": [],
        "pending_markers": [],
    }
    if log_path.exists():
        pattern = re.compile(re.escape(thread_id))
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, start=1):
                if pattern.search(line):
                    result["hook_lines"].append(f"{line_no}:{line.rstrip()}")
        result["hook_lines"] = result["hook_lines"][-12:]

    state_dir = project_dir / "overwatch" / "state"
    if state_dir.exists():
        for path in sorted(state_dir.glob(f"*{thread_id}*.json")):
            if "pending" in path.name:
                result["pending_markers"].append(str(path))
    return result


def classify(thread: dict[str, Any], rollout: dict[str, Any], logs: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if rollout.get("missing"):
        return "missing-rollout", ["Rollout JSONL is missing."]

    exact_logs = int(logs.get("exact_log_rows") or 0)
    tokens = int(thread.get("tokens_used") or 0)
    unfinished = int(rollout.get("unfinished_turns") or 0)
    event_counts = rollout.get("event_counts") or {}
    response_counts = rollout.get("response_counts") or {}

    if unfinished > 0:
        reasons.append(f"task_started exceeds task_complete by {unfinished}.")

    if tokens == 0 and exact_logs == 0 and not event_counts.get("agent_message"):
        reasons.append("State has 0 tokens, no exact core logs, and no agent_message.")
        return "main-turn-not-dispatched", reasons

    if (
        rollout.get("last_tool_output_idx", -1) > rollout.get("last_agent_idx", -1)
        and unfinished > 0
    ):
        reasons.append("Last tool output appears after the last assistant message.")
        return "post-tool-continuation-stall", reasons

    if response_counts.get("image_generation_call") or event_counts.get("image_generation_end"):
        if unfinished > 0 and not event_counts.get("task_complete"):
            reasons.append("Image generation appeared in an unfinished turn.")
            return "image-generation-turn-stall", reasons

    if exact_logs > 0 and unfinished > 0:
        reasons.append(f"Core logs exist ({exact_logs} rows), but the rollout did not complete.")
        return "core-turn-unfinished", reasons

    if unfinished == 0:
        reasons.append("Rollout task_started/task_complete counts are balanced.")
        return "not-currently-stuck-by-rollout", reasons

    return "unknown-stuck-pattern", reasons


def print_report(thread: dict[str, Any], project_dir: Path, codex_dir: Path) -> None:
    rollout_path = Path(thread["rollout_path"])
    rollout = parse_rollout(rollout_path)
    logs = query_log_counts(codex_dir / "logs_2.sqlite", thread["id"])
    overwatch = search_overwatch(project_dir, thread["id"])
    label, reasons = classify(thread, rollout, logs)

    print(f"thread_id: {thread['id']}")
    print(f"title: {thread['title']}")
    print(f"cwd: {thread['cwd']}")
    print(f"created: {thread['created_local']}  updated: {thread['updated_local']}")
    print(f"model: {thread.get('model')}  effort: {thread.get('reasoning_effort')}  tokens: {thread.get('tokens_used')}")
    print(f"rollout: {rollout_path}")
    print(f"classification: {label}")
    for reason in reasons:
        print(f"- {reason}")

    if rollout.get("missing"):
        return

    print("\nrollout:")
    print(f"- lines: {rollout['line_count']}")
    print(f"- task_started: {rollout['task_started']}")
    print(f"- task_complete: {rollout['task_complete']}")
    print(f"- last_items: {', '.join(rollout['last_items'])}")
    print(f"- event_counts: {rollout['event_counts']}")
    print(f"- response_counts: {rollout['response_counts']}")

    print("\ncore_logs:")
    if logs.get("missing"):
        print("- logs database missing")
    else:
        print(f"- exact_log_rows: {logs['exact_log_rows']}")
        if logs["top_targets"]:
            for row in logs["top_targets"]:
                print(f"- {row['level']} {row['target']}: {row['c']}")
        if logs["warnings_or_errors"]:
            print("- warnings_or_errors:")
            for row in logs["warnings_or_errors"]:
                print(f"  {row['ts_local']} {row['level']} {row['target']}: {row['body']}")

    print("\noverwatch:")
    if overwatch["hook_lines"]:
        for line in overwatch["hook_lines"]:
            print(f"- {line}")
    else:
        print("- no overwatch hook lines for this thread")
    if overwatch["pending_markers"]:
        print("- pending markers:")
        for marker in overwatch["pending_markers"]:
            print(f"  {marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thread_ids", nargs="*", help="Codex thread ids to diagnose")
    parser.add_argument("--title", help="Find recent threads whose title contains this text")
    parser.add_argument("--codex-dir", type=Path, default=DEFAULT_CODEX_DIR)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    args = parser.parse_args()

    state_db = args.codex_dir / "state_5.sqlite"
    threads: list[dict[str, Any]] = []
    for thread_id in args.thread_ids:
        thread = get_thread(state_db, thread_id)
        if thread is None:
            print(f"thread_id: {thread_id}")
            print("classification: missing-thread-state")
            print()
        else:
            threads.append(thread)

    if args.title:
        threads.extend(find_threads_by_title(state_db, args.title))

    seen: set[str] = set()
    unique_threads = []
    for thread in threads:
        if thread["id"] in seen:
            continue
        seen.add(thread["id"])
        unique_threads.append(thread)

    if not unique_threads:
        parser.error("provide at least one thread id or --title")

    for idx, thread in enumerate(unique_threads):
        if idx:
            print("\n" + "=" * 80 + "\n")
        print_report(thread, args.project_dir, args.codex_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
