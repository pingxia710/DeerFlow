from __future__ import annotations

import json

import pytest

from deerflow.command_room.review import (
    build_review_invocation,
    complete_review_invocation,
    list_review_invocations,
    record_review_invocation,
    review_invocation_from_dict,
)


def test_review_invocation_serializes_as_ai_authored_request(tmp_path) -> None:
    invocation = build_review_invocation(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        requested_by_role="Lead",
        reviewer_role="evidence-checker",
        reason="Need a small evidence check before Chair chooses next action.",
        focus="Verify whether the cited command output exists.",
        evidence_refs=["worker says tests completed"],
        handoff_refs=["handoff:task-1"],
        quality_signal_refs=["quality-1"],
    )
    completed = complete_review_invocation(
        invocation,
        result_summary="The cited ref is a worker self-claim; collect command output.",
        result_evidence_refs=["audit/findings.md"],
    )

    record_review_invocation(invocation, user_id="user-1", base_dir=tmp_path)
    record_review_invocation(completed, user_id="user-1", base_dir=tmp_path)
    [row] = list_review_invocations(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    restored = review_invocation_from_dict(row)

    assert row["requested_by_role"] == "lead"
    assert row["reviewer_role"] == "evidence_checker"
    assert row["status"] == "completed"
    assert row["target_role"] == "Chair"
    assert row["ai_authored"] is True
    assert row["result_evidence_refs"] == ["audit/findings.md"]
    assert "auto_rework" not in row
    text = json.dumps(row)
    assert "PASS" not in text
    assert "FAIL" not in text
    assert restored.as_dict()["invocation_id"] == invocation.invocation_id


def test_review_invocation_rejects_unknown_reviewer_role() -> None:
    with pytest.raises(ValueError, match="Unsupported reviewer_role"):
        build_review_invocation(
            thread_id="thread-1",
            run_id="run-1",
            requested_by_role="chair",
            reviewer_role="planner",
            reason="Needs review.",
            focus="Review this.",
        )
