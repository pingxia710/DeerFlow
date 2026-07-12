"""Tests for objective Command Room RoundRecord persistence."""

import importlib.util
import json
from pathlib import Path

from deerflow.command_room.round_record import latest_command_room_round, record_command_room_round


def _audit_record(*, run_id: str = "run-1", status: str = "completed") -> dict:
    return {
        "run_id": run_id,
        "status": status,
        "task_id": "task-1",
        "subagent_type": "general-purpose",
        "result_sha256": "result-hash",
        "result_chars": 99,
        "handoff_packet": {"boundary": "read-only; do not write production"},
        "action_result": {
            "action_id": "task-1",
            "summary": "pytest was run",
            "evidence_refs": ["command: pytest backend/tests/test_x.py -q; exit code: 0"],
        },
    }


def test_record_is_objective_and_does_not_parse_model_prose(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="SECRET_USER_INTENT_SHOULD_NOT_APPEAR",
        final_text="""Round Card
Verdict: PASS
EvidenceStrength: Strong
Recommended Next Role: opposition
SECRET_FINAL_TEXT_SHOULD_NOT_APPEAR""",
        audit_records=[_audit_record()],
        base_dir=tmp_path,
    )

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "SECRET_USER_INTENT_SHOULD_NOT_APPEAR" not in text
    assert "SECRET_FINAL_TEXT_SHOULD_NOT_APPEAR" not in text
    record = json.loads(text)
    assert record["version"] == 2
    assert record["userGoal"]["sha256"]
    assert record["artifacts"]["finalText"]["sha256"]
    assert record["explicitBoundary"] == [{"taskId": "task-1", "value": "read-only; do not write production"}]
    assert record["actionResults"][0]["actionResult"]["action_id"] == "task-1"
    for legacy in ("verdict", "decisionSignals", "signals", "dispatchPlan", "nextRoundContract", "evidenceStandard"):
        assert legacy not in record


def test_record_filters_action_facts_by_run_id(tmp_path):
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="goal",
        final_text="Verdict: NEEDS_MORE",
        run_id="new",
        audit_records=[_audit_record(run_id="old"), _audit_record(run_id="new")],
        base_dir=tmp_path,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["runId"] == "new"
    assert len(record["actionResults"]) == 1
    assert record["actionResults"][0]["taskId"] == "task-1"


def test_record_skips_other_agents_and_latest_is_readable(tmp_path):
    assert (
        record_command_room_round(
            thread_id="thread-1",
            agent_name="general-purpose",
            user_id="user-1",
            final_text="PASS",
            base_dir=tmp_path,
        )
        is None
    )
    path = record_command_room_round(
        thread_id="thread-1",
        agent_name="command-room",
        user_id="user-1",
        user_message="goal",
        final_text="ordinary response",
        audit_records=[],
        base_dir=tmp_path,
    )
    assert path is not None
    latest = latest_command_room_round(thread_id="thread-1", user_id="user-1", base_dir=tmp_path)
    assert latest and latest["roundId"]
    assert latest["actionResults"] == []


def test_generated_fact_record_satisfies_contract_checker(tmp_path):
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
        user_message="verify record",
        final_text="Verdict: PASS",
        audit_records=[_audit_record()],
        base_dir=tmp_path,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert checker.validate_round(record, contract) == []
