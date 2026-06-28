"""Tests for Command Room RoundRecord persistence and readiness signals."""

import json

from deerflow.command_room.round_record import (
    evaluate_verdict_gate,
    record_command_room_round,
    signals_from_handoffs,
)


def _opposition_record(*, decision: str = "STOP_CONFIRM", redline: str = "true") -> dict:
    return {
        "status": "completed",
        "task_id": "call-opposition-1",
        "subagent_type": "opposition",
        "description": "opposition check",
        "prompt_sha256": "prompt-hash",
        "prompt_chars": 100,
        "result_sha256": "result-hash",
        "result_chars": 200,
        "signal": {
            "valid": True,
            "missing": [],
            "fields": {
                "Role": "opposition",
                "Claim": "Draft PASS is unsupported.",
                "EvidenceRefs": "worker self-claims only",
                "RedlineTouched": redline,
                "RecommendedDecision": decision,
                "NextAction": "Stop and collect concrete refs.",
            },
        },
    }


def test_verdict_gate_reports_risk_without_overriding_pass():
    signals = signals_from_handoffs([_opposition_record()])

    gate = evaluate_verdict_gate(
        """Round Card
Evidence: worker self-claims only
Verdict: PASS
Next: enter execution
""",
        signals,
    )

    assert gate["decision"] == "PASS"
    assert gate["modelDecision"] == "PASS"
    assert gate["gated"] is False
    reasons = " ".join(gate["reasons"]).lower()
    assert "opposition" in reasons
    assert "risk signal" in reasons


def test_verdict_gate_reports_exemption_readiness_with_evidence():
    gate = evaluate_verdict_gate(
        """Round Card
Evidence: backend/tests/test_command_room_round_record.py::test_example passed
Opposition:
Not dispatched because: single-file read-only validation.
Risk class: low.
Evidence basis: deterministic test output.
No permission expansion: true.
No PASS from worker self-claim: true.
Verdict: PASS
Next: done
""",
        [],
    )

    assert gate["decision"] == "PASS"
    assert gate["gated"] is False
    assert any("readiness signal" in reason.lower() for reason in gate["reasons"])


def test_record_command_room_round_omits_raw_user_and_final_text(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="SECRET_USER_INTENT_SHOULD_NOT_APPEAR",
        final_text="""Round Card
Goal: verify command room behavior
Evidence: worker self-claims only
Verdict: PASS
Next: enter execution

SECRET_FINAL_TEXT_SHOULD_NOT_APPEAR
""",
        usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        source="test",
        audit_records=[_opposition_record()],
        base_dir=tmp_path,
    )

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "SECRET_USER_INTENT_SHOULD_NOT_APPEAR" not in text
    assert "SECRET_FINAL_TEXT_SHOULD_NOT_APPEAR" not in text

    record = json.loads(text)
    assert record["verdict"]["decision"] == "PASS"
    assert record["verdict"]["gated"] is False
    assert any("risk signal" in reason.lower() for reason in record["verdict"]["reasons"])
    assert record["intentSeed"]["sha256"]
    assert record["artifacts"]["finalText"]["sha256"]
    assert record["signals"][0]["role"] == "opposition"
    assert record["signals"][0]["outputRef"] == {"sha256": "result-hash", "chars": 200}


def test_record_command_room_round_skips_other_agents(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="general-purpose",
        user_id="user-1",
        user_message="hello",
        final_text="Verdict: PASS",
        base_dir=tmp_path,
    )

    assert path is None
    assert not (tmp_path / "command_room_rounds.jsonl").exists()


def test_command_room_action_result_becomes_round_context_signals(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="implement and test feature",
        final_text="Verdict: NEEDS_MORE",
        audit_records=[
            {
                "status": "completed",
                "task_id": "task-1",
                "subagent_type": "general-purpose",
                "description": "run tests",
                "action_result": {
                    "action_id": "task-1",
                    "status": "completed",
                    "summary": "tests passed but config unclear",
                    "evidence_refs": ["command: python -m pytest; exit code: 0; stdout: passed"],
                    "risks": ["migration not reviewed"],
                    "open_questions": ["rollback plan"],
                    "unresolved": ["deployment target unknown"],
                },
            }
        ],
        base_dir=tmp_path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    round_signals = record["roundContextSignals"]
    assert record["roundRequired"] is True
    assert round_signals["action_count"] == 1
    json.dumps(round_signals)
    assert round_signals["evidence_signals"]["has_strong_signal"] is True
    evidence_signal = round_signals["evidence_signals"]["signals"][0]
    assert evidence_signal["ref"] == "command: python -m pytest; exit code: 0; stdout: passed"
    assert evidence_signal["strong"] is True
    assert "command-output-or-exit-code" in evidence_signal["strong_reasons"]
    assert "migration not reviewed" in round_signals["risks"]
    assert "rollback plan" in round_signals["open_questions"]
    assert round_signals["quality_verdict"] is None
    assert round_signals["auto_rework"] is False


def test_command_room_no_task_path_does_not_force_round(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="hello",
        final_text="ordinary chat",
        audit_records=[],
        base_dir=tmp_path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["roundRequired"] is False
    assert record["roundContextSignals"] is None
    assert record["verdict"]["gated"] is False
