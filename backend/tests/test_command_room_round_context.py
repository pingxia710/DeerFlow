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
    assert signals.evidence_signals["total"] == 1


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
    assert signals.evidence_signals["total"] == 0
    assert updated.is_complete is None


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
    assert updated.is_complete is None
    assert "boom" in signals.unresolved


def test_tests_passed_text_is_recorded_without_strength_classification():
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

    assert signals["evidence_signals"]["total"] == 1
    assert signals["evidence_signals"]["refs"] == ["tests passed"]
    assert signals["quality_verdict"] is None
    assert signals["auto_rework"] is False


def test_command_output_text_is_not_classified_by_context_helper():
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
    assert signals.evidence_signals["total"] == 1
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
    assert signals.evidence_signals["total"] == 0
    assert updated.is_complete is None


def test_worker_output_reference_is_preserved_without_classification():
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
    assert signals.evidence_signals["refs"] == ["worker-output:task-worker-output"]
    assert signals.evidence_signals["total"] == 1
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

    assert updated.is_complete is None
    assert signals.needs_user_confirmation is True
    assert signals.requires_confirmation is True
    assert signals.round_complete is None
    assert signals.next_round_is_safe is None
    assert data["evidence_signals"]["needs_user_confirmation"] is True
    assert data["evidence_signals"]["requires_confirmation"] is True
    assert data["evidence_signals"]["round_complete"] is None
    assert data["quality_verdict"] is None
    assert data["auto_rework"] is False


def test_read_only_diagnostics_are_reported_without_programmatic_safety_judgment():
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

    assert signals.round_complete is None
    assert signals.needs_user_confirmation is False
    assert signals.requires_confirmation is False
    assert signals.next_round_is_safe is None
    assert "requires user confirmation before next step" not in signals.unresolved
    assert "下一轮提案：read-only check PID" in signals.summary


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

    assert signals.round_complete is None
    assert signals.needs_user_confirmation is True
    assert signals.requires_confirmation is True
    assert signals.next_round_is_safe is None
    assert "下一轮提案被明确标记为需要用户确认" in signals.summary


def test_round_brief_preserves_ai_words_instead_of_filtering_substrings():
    round_ = create_round_context("preserve compassion and failure analysis")
    updated = record_action_result_from_event(
        round_,
        {
            "task_id": "task-words",
            "action_result": {
                "action_id": "task-words",
                "description": "surrogate gate review",
                "summary": "PASS/FAIL verdict wording belongs to the AI",
            },
        },
    )

    brief = round_context_signals(updated).evidence_signals["round_brief"]
    assert brief["goal"] == "preserve compassion and failure analysis"
    assert brief["handoff_signals"] == ["surrogate gate review: PASS/FAIL verdict wording belongs to the AI"]
