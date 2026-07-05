from __future__ import annotations

import json

from deerflow.command_room.brief import build_chair_operating_brief, format_chair_operating_brief_for_model


def test_chair_operating_brief_compacts_existing_facts_without_verdicts() -> None:
    brief = build_chair_operating_brief(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        filter_round_id="round-1",
        generated_at="2026-01-01T00:00:00+00:00",
        capability_snapshot={"version": 1},
        handoffs=[
            {
                "task_id": "task-1",
                "round_id": "round-1",
                "role": "evidence",
                "status": "completed",
                "handoff": {
                    "targetRole": "Chair",
                    "taskOrQuestion": "Inspect evidence refs",
                    "evidenceStrength": "Strong",
                    "evidenceRefs": ["command: pytest -q; exit code: 0"],
                },
            }
        ],
        evidence={
            "evidence_refs": [
                {"round_id": "round-1", "task_id": "task-1", "source_kind": "command_output", "strength": "Strong", "ref": "command: pytest -q; exit code: 0"},
                {"round_id": "round-2", "task_id": "task-2", "source_kind": "self_claim", "strength": "Weak", "ref": "tests passed"},
            ]
        },
        quality_signals=[
            {
                "signal_id": "quality-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "round_id": "round-1",
                "task_id": "task-1",
                "author_role": "evidence",
                "recommendation": "needs_more_evidence",
                "rationale": "Need one more concrete ref.",
                "evidence_refs": ["command: pytest -q; exit code: 0"],
                "capability_refs": [],
                "capability_snapshot_version": 1,
                "target_role": "Chair",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        review_invocations=[
            {
                "invocation_id": "review-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "round_id": "round-1",
                "task_id": "task-1",
                "requested_by_role": "lead",
                "reviewer_role": "opposition",
                "status": "completed",
                "focus": "Check evidence boundary.",
                "target_role": "Chair",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        account_proposals=[
            {
                "proposal_id": "proposal-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "round_id": "round-1",
                "task_id": "task-1",
                "proposed_by_role": "planner",
                "account_type": "goal",
                "target_role": "Chair",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        account_decisions=[
            {
                "decision_id": "decision-1",
                "proposal_id": "proposal-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "decided_by_role": "chair",
                "decision": "defer",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    )

    payload = brief.as_dict()
    assert payload["capability_snapshot_version"] == 1
    assert payload["handoff_count"] == 1
    assert payload["latest_handoff"]["target_role"] == "Chair"
    assert payload["evidence_summary"]["total"] == 1
    assert payload["evidence_summary"]["by_strength"] == {"Strong": 1, "Weak": 0, "Unverified": 0}
    assert payload["source_counts"] == {
        "handoffs": 1,
        "evidence_refs": 1,
        "quality_signals": 1,
        "review_invocations": 1,
        "account_proposals": 1,
        "account_decisions": 1,
    }
    assert payload["known_gaps"] == []
    public_json = json.dumps(payload).lower()
    assert "pass" not in public_json
    assert "fail" not in public_json
    assert "needs_rework" not in public_json
    assert "quality_verdict" not in public_json
    assert "auto_rework" not in public_json


def test_chair_operating_brief_known_gaps_are_mechanical_only() -> None:
    brief = build_chair_operating_brief(thread_id="thread-1", run_id="run-1")

    assert brief.known_gaps == ["no_capability_snapshot", "no_evidence_refs", "no_quality_signals"]
    assert set(brief.known_gaps) <= {"no_capability_snapshot", "no_evidence_refs", "no_quality_signals"}

    text = format_chair_operating_brief_for_model(brief)
    assert text is not None
    assert "Internal Chair Operating Brief" in text
    lowered = text.lower()
    assert "pass" not in lowered
    assert "fail" not in lowered
    assert "needs_rework" not in lowered
