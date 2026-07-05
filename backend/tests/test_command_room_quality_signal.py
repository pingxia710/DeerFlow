from __future__ import annotations

import json

import pytest

from deerflow.command_room.quality import build_quality_signal, list_quality_signals, quality_signal_from_dict, record_quality_signal


def test_quality_signal_serializes_as_ai_authored_recommendation(tmp_path) -> None:
    signal = build_quality_signal(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        author_role="Evidence",
        recommendation="needs_more_evidence",
        rationale="Needs one command output ref before Chair decides.",
        evidence_refs=["worker says done"],
        capability_refs=["capability:sandbox"],
        capability_snapshot_version=1,
    )

    record_quality_signal(signal, user_id="user-1", base_dir=tmp_path)
    [row] = list_quality_signals(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    restored = quality_signal_from_dict(row)

    assert row["author_role"] == "evidence"
    assert row["recommendation"] == "needs_more_evidence"
    assert row["ai_authored"] is True
    assert row["programmatic_decision"] is False
    assert row["quality_verdict"] is None
    assert row["auto_rework"] is False
    assert restored.as_dict()["evidence_refs"] == ["worker says done"]
    assert "PASS" not in json.dumps(row)
    assert "FAIL" not in json.dumps(row)


def test_quality_signal_rejects_pass_fail_recommendations() -> None:
    with pytest.raises(ValueError, match="PASS/FAIL"):
        build_quality_signal(
            thread_id="thread-1",
            run_id="run-1",
            author_role="chair",
            recommendation="PASS",
            rationale="not allowed",
        )
