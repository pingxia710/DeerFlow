from __future__ import annotations

import json

import pytest

from deerflow.command_room.account_ledger import (
    account_decision_from_dict,
    account_update_proposal_from_dict,
    build_account_decision,
    build_account_update_proposal,
    list_account_decisions,
    list_account_proposals,
    record_account_decision,
    record_account_update_proposal,
)


def test_account_ledger_serializes_proposal_and_decision(tmp_path) -> None:
    proposal = build_account_update_proposal(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        proposed_by_role="Evidence",
        account_type="goal",
        proposed_change="Keep Goal Account scoped to the current user objective.",
        rationale="The latest evidence changed the durable goal wording.",
        evidence_refs=["source_ref:docs/goal.md"],
        quality_signal_refs=["quality-1"],
        review_invocation_refs=["review-1"],
    )
    decision = build_account_decision(
        proposal_id=proposal.proposal_id,
        thread_id="thread-1",
        run_id="run-1",
        decision="revise",
        rationale="Adopt the direction with narrower wording.",
        revised_change="Keep Goal Account scoped to the active thread objective.",
        evidence_refs=["source_ref:docs/goal.md"],
    )

    record_account_update_proposal(proposal, user_id="user-1", base_dir=tmp_path)
    record_account_decision(decision, user_id="user-1", base_dir=tmp_path)

    [proposal_row] = list_account_proposals(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    [decision_row] = list_account_decisions(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    restored_proposal = account_update_proposal_from_dict(proposal_row)
    restored_decision = account_decision_from_dict(decision_row)

    assert proposal_row["record_type"] == "proposal"
    assert proposal_row["proposed_by_role"] == "evidence"
    assert proposal_row["account_type"] == "goal"
    assert proposal_row["target_role"] == "Chair"
    assert proposal_row["ai_authored"] is True
    assert restored_proposal.as_dict()["review_invocation_refs"] == ["review-1"]

    assert decision_row["record_type"] == "decision"
    assert decision_row["decided_by_role"] == "chair"
    assert decision_row["decision"] == "revise"
    assert decision_row["ai_authored"] is True
    assert restored_decision.as_dict()["revised_change"] == "Keep Goal Account scoped to the active thread objective."

    text = json.dumps({"proposal": proposal_row, "decision": decision_row})
    assert "PASS" not in text
    assert "FAIL" not in text
    assert "auto_rework" not in text
    assert "auto_apply" not in text


def test_account_ledger_rejects_invalid_account_type_and_decision() -> None:
    with pytest.raises(ValueError, match="Unsupported account_type"):
        build_account_update_proposal(
            thread_id="thread-1",
            run_id="run-1",
            proposed_by_role="chair",
            account_type="budget",
            proposed_change="Update budget account.",
            rationale="Not a supported account type.",
        )

    with pytest.raises(ValueError, match="Unsupported account decision"):
        build_account_decision(
            proposal_id="proposal-1",
            thread_id="thread-1",
            run_id="run-1",
            decision="approve",
            rationale="Not a supported decision.",
        )
