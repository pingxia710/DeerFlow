from deerflow.command_room.round import NextRound
from deerflow.command_room.round_context import (
    create_round_context,
    record_action_result_from_event,
    round_context_signals,
)


def test_records_task_completed_event_metadata_action_result_into_round():
    round_ = create_round_context("collect task result")
    event = {
        "type": "task_completed",
        "task_id": "task-1",
        "result": "original string return",
        "action_result": {
            "action_id": "task-1",
            "description": "run tests",
            "status": "completed",
            "summary": "done",
            "evidence_refs": ["command: python -m pytest; exit code: 0; stdout: passed"],
        },
    }

    updated = record_action_result_from_event(round_, event)
    signals = round_context_signals(updated)

    assert updated is not round_
    assert len(updated.action_results) == 1
    assert updated.action_results[0].action_id == "task-1"
    assert signals.action_count == 1
    assert signals.evidence_signals["has_strong_signal"] is True


def test_plain_string_action_result_is_summary_only_weak_not_evidence():
    round_ = create_round_context("plain summary")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-plain",
            "metadata": {"action_result": "I checked it and it is done."},
        },
    )

    signals = round_context_signals(updated)

    assert updated.action_results[0].summary == "I checked it and it is done."
    assert updated.evidence_refs == []
    assert signals.evidence_signals["strong_count"] == 0
    assert signals.evidence_signals["weak_count"] == 0
    assert signals.evidence_signals["has_strong_signal"] is False
    assert updated.is_complete is False


def test_task_failed_error_action_becomes_unresolved_and_blocks_completion():
    round_ = create_round_context("collect failure")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_failed",
            "task_id": "task-err",
            "error": "boom",
            "action_result": {
                "action_id": "task-err",
                "status": "failed",
                "summary": "failed",
                "error": "boom",
            },
        },
    )

    signals = round_context_signals(updated)

    assert updated.action_results[0].status == "failed"
    assert "boom" in updated.unresolved
    assert updated.is_complete is False
    assert "boom" in signals.unresolved


def test_tests_passed_alone_is_weak_signal_only():
    round_ = create_round_context("weak evidence")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-tests",
            "action_result": {
                "action_id": "task-tests",
                "status": "completed",
                "evidence_refs": ["tests passed"],
            },
        },
    )

    signals = round_context_signals(updated).as_dict()

    assert signals["evidence_signals"]["strong_count"] == 0
    assert signals["evidence_signals"]["weak_count"] == 1
    assert signals["quality_verdict"] is None
    assert signals["auto_rework"] is False


def test_command_output_exit_code_evidence_is_strong_without_quality_verdict():
    round_ = create_round_context("strong evidence")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-cmd",
            "action_result": {
                "status": "completed",
                "evidence_refs": ["command: make test; exit code: 0; stdout: ok"],
            },
        },
    )

    signals = round_context_signals(updated)

    assert updated.action_results[0].action_id == "task-cmd"
    assert signals.evidence_signals["has_strong_signal"] is True
    assert signals.evidence_signals["quality_verdict"] is None
    assert signals.evidence_signals["auto_rework"] is False
    assert signals.quality_verdict is None
    assert signals.auto_rework is False


def test_output_ref_is_not_evidence():
    round_ = create_round_context("output ref")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-output",
            "action_result": {
                "action_id": "task-output",
                "status": "completed",
                "output_ref": "outputs/report.md",
            },
        },
    )

    signals = round_context_signals(updated)

    assert updated.evidence_refs == []
    assert updated.action_results[0].output_ref == "outputs/report.md"
    assert signals.evidence_signals["strong_count"] == 0
    assert updated.is_complete is False


def test_worker_output_evidence_ref_is_self_attestation_only():
    round_ = create_round_context("worker output")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-worker-output",
            "action_result": {
                "action_id": "task-worker-output",
                "status": "completed",
                "evidence_refs": ["worker-output:task-worker-output"],
            },
        },
    )

    signals = round_context_signals(updated)
    evidence_signal = signals.evidence_signals["signals"][0]

    assert evidence_signal.ref == "worker-output:task-worker-output"
    assert evidence_signal.strong is False
    assert evidence_signal.trusted_source is False
    assert evidence_signal.source_kind == "self_claim"
    assert signals.evidence_signals["strong_count"] == 0
    assert signals.evidence_signals["weak_count"] == 1
    assert signals.quality_verdict is None
    assert signals.auto_rework is False


def test_helper_does_not_change_original_task_return_or_auto_rework():
    task_return = "plain task() string return"
    round_ = create_round_context("non invasive")
    unchanged = record_action_result_from_event(round_, {"type": "progress", "result": task_return})
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-plain",
            "result": task_return,
            "metadata": {"action_result": {"action_id": "task-plain", "summary": task_return}},
        },
    )

    assert unchanged is round_
    assert task_return == "plain task() string return"
    assert updated.action_results[0].summary == task_return
    assert round_context_signals(updated).auto_rework is False


def test_incomplete_round_requiring_human_confirmation_is_explicit_signal():
    round_ = create_round_context("confirm risky next step")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-evidence",
            "action_result": {
                "action_id": "task-evidence",
                "status": "completed",
                "evidence_refs": ["command: python -m pytest; exit code: 0"],
            },
        },
    )
    updated = updated.__class__(
        goal=updated.goal,
        boundaries=updated.boundaries,
        known_facts=updated.known_facts,
        open_questions=updated.open_questions,
        allowed_actions=updated.allowed_actions,
        evidence_standard=updated.evidence_standard,
        actions=updated.actions,
        action_results=updated.action_results,
        evidence_refs=updated.evidence_refs,
        unresolved=updated.unresolved,
        risks=updated.risks,
        conflicts=updated.conflicts,
        state_delta=updated.state_delta,
        verdict=updated.verdict,
        next_step="wait for user",
        next_round=NextRound(proposal="apply risky change", needs_user_confirmation=True),
    )

    signals = round_context_signals(updated)
    data = signals.as_dict()

    assert updated.is_complete is False
    assert signals.needs_user_confirmation is True
    assert signals.requires_confirmation is True
    assert signals.round_complete is False
    assert signals.next_round_is_safe is False
    assert "requires user confirmation before next step" in signals.unresolved
    assert data["evidence_signals"]["needs_user_confirmation"] is True
    assert data["evidence_signals"]["requires_confirmation"] is True
    assert data["evidence_signals"]["round_complete"] is False
    assert data["quality_verdict"] is None
    assert data["auto_rework"] is False


def test_read_only_diagnostics_next_round_is_safe_without_human_confirmation():
    round_ = create_round_context("diagnose lock")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-lock",
            "action_result": {
                "action_id": "task-lock",
                "status": "completed",
                "evidence_refs": ["log: lock wait observed"],
                "open_questions": ["which PID owns the lock"],
            },
        },
    )
    updated = updated.__class__(
        goal=updated.goal,
        boundaries=updated.boundaries,
        known_facts=updated.known_facts,
        open_questions=updated.open_questions,
        allowed_actions=updated.allowed_actions,
        evidence_standard=updated.evidence_standard,
        actions=updated.actions,
        action_results=updated.action_results,
        evidence_refs=updated.evidence_refs,
        unresolved=updated.unresolved,
        risks=updated.risks,
        conflicts=updated.conflicts,
        state_delta=updated.state_delta,
        verdict=updated.verdict,
        next_round=NextRound(
            proposal="read-only check PID, logs, and lock-file status",
            needs_user_confirmation=False,
        ),
    )

    signals = round_context_signals(updated)

    assert signals.round_complete is False
    assert signals.needs_user_confirmation is False
    assert signals.requires_confirmation is False
    assert signals.next_round_is_safe is True
    assert "requires user confirmation before next step" not in signals.unresolved
    assert "本轮尚未完成但可继续自主排查" in signals.summary


def test_destructive_next_round_is_human_confirmation_boundary():
    round_ = create_round_context("clear lock")
    updated = record_action_result_from_event(
        round_,
        {
            "type": "task_completed",
            "task_id": "task-pid",
            "action_result": {
                "action_id": "task-pid",
                "status": "completed",
                "evidence_refs": ["command: ps -p 1234; exit code: 0"],
            },
        },
    )
    updated = updated.__class__(
        goal=updated.goal,
        boundaries=updated.boundaries,
        known_facts=updated.known_facts,
        open_questions=updated.open_questions,
        allowed_actions=updated.allowed_actions,
        evidence_standard=updated.evidence_standard,
        actions=updated.actions,
        action_results=updated.action_results,
        evidence_refs=updated.evidence_refs,
        unresolved=updated.unresolved,
        risks=updated.risks,
        conflicts=updated.conflicts,
        state_delta=updated.state_delta,
        verdict=updated.verdict,
        next_round=NextRound(
            proposal="kill PID 1234 and remove the lock file",
            needs_user_confirmation=True,
        ),
    )

    signals = round_context_signals(updated)

    assert signals.round_complete is False
    assert signals.needs_user_confirmation is True
    assert signals.requires_confirmation is True
    assert signals.next_round_is_safe is False
    assert "requires user confirmation before next step" in signals.unresolved
    assert "触及红线/授权边界，需要用户确认" in signals.summary
