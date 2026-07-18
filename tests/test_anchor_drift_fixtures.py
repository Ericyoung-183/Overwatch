#!/usr/bin/env python3
"""Regression fixtures for Anchor agenda drift review coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anchor_drift import (  # noqa: E402
    ANCHOR_DRIFT_CATEGORIES,
    classify_anchor_drift,
    format_anchor_drift_rubric,
)
from prompts import OVERWATCH_SYSTEM_PROMPT  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "anchor_drift"
REQUIRED_RUNTIME_CATEGORIES = {
    "missing-whole-picture",
    "false-presentation-ack",
    "missing-presentation-ack",
    "topic-switch-without-interrupt",
    "unsynced-todo-agenda",
    "unsupported-todo-misread",
    "source-agenda-mismatch",
    "missing-parent-synthesis",
}


def test(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {name}")
        return
    print(f"  FAIL {name} -- {detail}")
    raise AssertionError(name)


def load_expected() -> list[dict[str, str]]:
    return json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))


def test_prompt_contains_anchor_drift_rubric() -> None:
    rubric = format_anchor_drift_rubric()
    category_ids = {category["id"] for category in ANCHOR_DRIFT_CATEGORIES}
    test(
        "runtime Anchor obligations are represented in the review rubric",
        REQUIRED_RUNTIME_CATEGORIES.issubset(category_ids),
        str(sorted(REQUIRED_RUNTIME_CATEGORIES - category_ids)),
    )
    for category in ANCHOR_DRIFT_CATEGORIES:
        test(
            f"review prompt contains {category['id']}",
            category["id"] in OVERWATCH_SYSTEM_PROMPT,
            OVERWATCH_SYSTEM_PROMPT,
        )
        test(
            f"rubric contains {category['id']}",
            category["id"] in rubric,
            rubric,
        )
        test(
            f"rubric exposes severity for {category['id']}",
            category["severity"] in rubric,
            rubric,
        )
        test(
            f"rubric exposes evidence level for {category['id']}",
            f"[{category['evidence_level']}]" in rubric,
            rubric,
        )
    test(
        "prompt prevents heuristic overclaiming",
        "Never present a heuristic candidate as a confirmed failure" in OVERWATCH_SYSTEM_PROMPT,
        OVERWATCH_SYSTEM_PROMPT,
    )


def test_drift_fixtures_flag_expected_categories() -> None:
    for item in load_expected():
        text = (FIXTURE_DIR / item["fixture"]).read_text(encoding="utf-8")
        findings = classify_anchor_drift(text)
        category_ids = {finding["id"] for finding in findings}
        expected_id = item["expected_category"]
        test(
            f"{item['fixture']} flags {expected_id}",
            expected_id in category_ids,
            f"findings={findings}",
        )
        matched = next(finding for finding in findings if finding["id"] == expected_id)
        test(
            f"{item['fixture']} severity is {item['expected_severity']}",
            matched["severity"] == item["expected_severity"],
            f"finding={matched}",
        )


def test_assistant_list_without_user_sequential_intent_is_not_flagged() -> None:
    findings = classify_anchor_drift(
        "Assistant: 总结三个发现：1. A 2. B 3. C\nUser: 这个总结很清楚。"
    )
    test("assistant summary alone does not trigger capture", findings == [], str(findings))


if __name__ == "__main__":
    test_prompt_contains_anchor_drift_rubric()
    test_drift_fixtures_flag_expected_categories()
    test_assistant_list_without_user_sequential_intent_is_not_flagged()
    print("anchor drift fixture tests passed")
