"""Tests for Command Room RoundRecord persistence and readiness signals."""

import json
from dataclasses import asdict
from pathlib import Path

from deerflow.command_room.evidence import analyze_evidence_ref
from deerflow.command_room.round_record import (
    evaluate_decision_signals,
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


def test_decision_signals_report_risk_without_overriding_legacy_pass():
    signals = signals_from_handoffs([_opposition_record()])

    readiness = evaluate_decision_signals(
        """Round Card
Evidence: worker self-claims only
Verdict: PASS
Next: enter execution
""",
        signals,
    )

    assert readiness["decision"] == "PASS"
    assert readiness["modelDecision"] == "PASS"
    assert readiness["gated"] is False  # legacy compatibility field; not a runtime gate
    reasons = " ".join(readiness["reasons"]).lower()
    assert "opposition" in reasons
    assert "risk signal" in reasons


def test_decision_signals_report_exemption_readiness_with_evidence():
    readiness = evaluate_decision_signals(
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

    assert readiness["decision"] == "PASS"
    assert readiness["gated"] is False  # legacy compatibility field; not a runtime gate
    assert any("readiness signal" in reason.lower() for reason in readiness["reasons"])


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
    assert record["decisionSignals"]["decision"] == "PASS"
    assert record["readinessSignals"] == record["decisionSignals"]
    assert record["verdict"] == record["decisionSignals"]  # deprecated compatibility alias
    assert record["verdict"]["gated"] is False
    assert record["compatibilityAliases"]["verdict"] == "decisionSignals"
    assert any("risk signal" in reason.lower() for reason in record["decisionSignals"]["reasons"])
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
    brief = record["roundBrief"]
    assert record["roundContextAvailable"] is True
    assert record["roundRequired"] is True  # deprecated compatibility alias
    assert record["roundContextAliases"]["roundRequired"] is True
    assert record["compatibilityAliases"]["roundRequired"] == "roundContextAvailable"
    assert round_signals["action_count"] == 1
    assert brief["goal"] == "implement and test feature"
    assert "trusted observable evidence" in brief["evidence_status"]
    assert "migration not reviewed" in brief["open_risks_or_questions"]
    assert all(term not in json.dumps(brief).lower() for term in ["gate", "verdict", "pass", "fail"])
    assert round_signals["evidence_signals"]["round_brief"] == brief
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
    assert record["roundContextAvailable"] is False
    assert record["roundRequired"] is False  # deprecated compatibility alias
    assert record["roundContextSignals"] is None
    assert record["decisionSignals"]["gated"] is False  # legacy compatibility field; not a runtime gate


def test_record_command_room_round_filters_handoffs_by_run_id(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="implement",
        final_text="Verdict: NEEDS_MORE",
        run_id="new",
        audit_records=[
            {
                "run_id": "old",
                "status": "completed",
                "task_id": "old-task",
                "subagent_type": "general-purpose",
                "description": "old task",
                "action_result": {
                    "action_id": "old-task",
                    "status": "completed",
                    "summary": "old summary",
                    "evidence_refs": ["old evidence"],
                    "risks": ["old risk"],
                },
            },
            {
                "run_id": "new",
                "status": "completed",
                "task_id": "new-task",
                "subagent_type": "general-purpose",
                "description": "new task",
                "action_result": {
                    "action_id": "new-task",
                    "status": "completed",
                    "summary": "new summary",
                    "evidence_refs": ["command: pytest; exit code: 0"],
                    "risks": ["new risk"],
                },
            },
        ],
        base_dir=tmp_path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["runId"] == "new"
    assert [item["laneId"] for item in record["dispatchPlan"]] == ["new-task"]
    assert record["roundContextSignals"]["action_count"] == 1
    assert "new risk" in record["roundContextSignals"]["risks"]
    assert "old risk" not in record["roundContextSignals"]["risks"]
    assert "old evidence" not in json.dumps(record["roundContextSignals"])
    assert "old-task" not in json.dumps(record["signals"])


def test_dispatch_plan_includes_handoff_packet_and_keeps_worker_claims_weak(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="implement",
        final_text="Evidence: worker says done\nVerdict: PASS",
        run_id="new",
        audit_records=[
            {
                "run_id": "new",
                "status": "completed",
                "task_id": "new-task",
                "subagent_type": "general-purpose",
                "description": "audit task",
                "prompt_sha256": "prompt-hash",
                "prompt_chars": 123,
                "handoff_packet": {
                    "goal": "audit task",
                    "boundary": "do not touch production",
                    "expectedEvidence": "file refs and test output",
                    "stopConditions": "stop on unrelated dirty files",
                    "releasedCapabilities": "read backend; pytest targeted",
                    "present": ["goal", "boundary"],
                },
                "signal": {
                    "valid": True,
                    "missing": [],
                    "fields": {
                        "Role": "general-purpose",
                        "Claim": "done",
                        "EvidenceRefs": "worker says done",
                        "RedlineTouched": "false",
                        "RecommendedDecision": "PASS",
                    },
                },
            }
        ],
        base_dir=tmp_path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    packet = record["dispatchPlan"][0]["handoffPacket"]
    assert packet["boundary"] == "do not touch production"
    assert packet["expectedEvidence"] == "file refs and test output"
    signal = record["signals"][0]
    assert signal["evidenceState"] == "STALE"
    assert signal["selfAttestationOnly"] is True
    assert any("self-claims" in reason.lower() for reason in record["decisionSignals"]["reasons"])
    contract = record["nextRoundContract"]
    assert contract["requiredEvidence"] == contract["evidenceSignals"]  # deprecated compatibility alias
    assert contract["userConfirmationNeeded"] == contract["needsUserConfirmation"]  # deprecated compatibility alias
    assert record["compatibilityAliases"]["requiredEvidence"] == "nextRoundContract.evidenceSignals"


def test_self_claimed_evidence_refs_are_not_trusted_sources():
    weak_refs = ["tests passed", "verified=true", "worker says done"]

    signals = [analyze_evidence_ref(ref) for ref in weak_refs]

    assert [signal.strong for signal in signals] == [False, False, False]
    assert [signal.trusted_source for signal in signals] == [False, False, False]
    assert "tests-passed-alone" in signals[0].weak_reasons
    assert all(asdict(signal)["trusted_source"] is False for signal in signals)


def test_runtime_observable_evidence_ref_is_trusted_source():
    signal = analyze_evidence_ref("command: python -m pytest backend/tests/test_x.py; exit code: 0; stdout: passed")

    assert signal.strong is True
    assert signal.trusted_source is True
    assert "command-output-or-exit-code" in signal.strong_reasons


def test_generated_command_room_round_record_satisfies_contract_checker(tmp_path):
    import importlib.util

    contract_path = Path(__file__).resolve().parents[2] / "contracts" / "command_room_round_contract.json"
    checker_path = Path(__file__).resolve().parents[2] / "scripts" / "command-room-contract-check.py"
    spec = importlib.util.spec_from_file_location("command_room_contract_check", checker_path)
    checker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(checker)

    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="verify generated round record contract",
        final_text="""Round Card
Goal: verify generated round record contract
Boundary: read-only; no production or credentials
Evidence: command: pytest backend/tests/test_command_room_round_record.py -q; exit code: 0
Verdict: NEEDS_MORE
Next: collect additional concrete refs
""",
        run_id="run-1",
        audit_records=[
            {
                "run_id": "run-1",
                "status": "completed",
                "task_id": "task-1",
                "subagent_type": "fact-finder",
                "description": "inspect generated record",
                "prompt_sha256": "prompt-hash",
                "prompt_chars": 123,
                "handoff_packet": {
                    "goal": "inspect generated record",
                    "boundary": "read-only",
                    "expectedEvidence": "file refs and test output",
                    "stopConditions": "stop before production or credentials",
                    "releasedCapabilities": "read files; run targeted pytest",
                },
                "result_sha256": "result-hash",
                "result_chars": 456,
                "signal": {
                    "valid": True,
                    "missing": [],
                    "fields": {
                        "Role": "fact-finder",
                        "Claim": "Generated record has concrete test refs but needs review.",
                        "EvidenceRefs": "command: pytest tests/test_command_room_round_record.py -q; exit code: 0",
                        "EvidenceState": "SUPPORTED",
                        "SelfAttestationOnly": "false",
                        "RedlineTouched": "false",
                        "RecommendedDecision": "NEEDS_MORE",
                        "NextAction": "Review generated record shape against contract.",
                    },
                },
                "action_result": {
                    "action_id": "task-1",
                    "status": "completed",
                    "summary": "generated record includes action summary and handoff packet",
                    "evidence_refs": ["command: pytest tests/test_command_room_round_record.py -q; exit code: 0"],
                    "next_step": "review generated record shape against contract",
                },
            }
        ],
        base_dir=tmp_path,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert record["decisionSignals"]["decision"] == "NEEDS_MORE"
    assert record["roundBrief"]
    assert record["dispatchPlan"][0]["handoffPacket"]["goal"] == "inspect generated record"
    assert checker.validate_round(record, contract) == []
