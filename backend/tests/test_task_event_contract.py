import importlib
import json
from pathlib import Path

from deerflow.command_room.task_action_result import task_action_result_from_terminal_event

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "task_event_contract.json"
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text())


def test_task_event_base_matches_contract_required_fields():
    contract = _contract()
    event = task_tool_module._task_event_base(
        "task_started",
        "task-1",
        thread_id="thread-1",
        run_id="run-1",
        started_at="2026-07-03T00:00:00Z",
    )

    assert event["schema_version"] == contract["schema_version"]
    assert event["type"] == "task_started"
    assert event["event_type"] == event["type"]
    assert set(contract["required_event_fields"]).issubset(event.keys())


def test_task_event_contract_lists_runtime_event_types():
    contract = _contract()

    assert set(contract["event_types"]) == {
        "task_started",
        "task_running",
        "task_completed",
        "task_failed",
        "task_cancelled",
        "task_timed_out",
    }


def test_task_running_message_is_optional_reserved():
    contract = _contract()

    assert contract["task_running_fields"] == ["message_index", "total_messages"]
    assert contract["optional_task_running_fields"] == ["message"]


def test_boundary_blocked_is_reserved_for_future_task_terminal_reason():
    contract = _contract()

    assert "boundary_blocked" in contract["reserved_terminal_reasons"]
    assert all(case["terminal_reason"] != "boundary_blocked" for case in contract["terminal_cases"])


def test_terminal_action_result_contract_cases_are_preserved():
    contract = _contract()

    for case in contract["terminal_cases"]:
        result = task_action_result_from_terminal_event(
            task_id=f"task-{case['status']}",
            status=case["action_result_status"],
            description="contract check",
            error="terminal error" if case["terminal_reason"] else None,
            terminal_reason=case["terminal_reason"],
        )
        compact = task_tool_module._compact_action_result_event(result)

        assert case["event_type"] in contract["event_types"]
        assert case["status"] in contract["status_values"]
        assert compact["status"] == case["action_result_status"]
        assert compact["terminal_reason"] == case["terminal_reason"]
        if case["terminal_reason"] is not None:
            assert case["terminal_reason"] in contract["terminal_reasons"]


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "task_events"
KNOWN_FIXTURES = [
    "started",
    "running_without_message",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
TERMINAL_EXPECTATIONS = {
    "completed": ("completed", None),
    "failed": ("failed", "failed"),
    "cancelled": ("cancelled", "user_cancelled"),
    "timed_out": ("timed_out", "timed_out"),
}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def test_shared_task_event_fixtures_match_current_contract():
    contract = _contract()

    for name in KNOWN_FIXTURES:
        event = _fixture(name)
        assert event["schema_version"] == contract["schema_version"]
        assert event["type"] == event["event_type"]
        assert event["event_type"] in contract["event_types"]
        assert event["status"] in contract["status_values"]

        action_result = event.get("action_result")
        if event["event_type"] in {"task_completed", "task_failed", "task_cancelled", "task_timed_out"}:
            expected_status, expected_reason = TERMINAL_EXPECTATIONS[name]
            assert action_result["status"] == expected_status
            assert action_result.get("terminal_reason") == expected_reason
        else:
            assert action_result is None


def test_shared_task_running_fixture_does_not_expose_raw_message():
    event = _fixture("running_without_message")

    assert event["event_type"] == "task_running"
    assert "message" not in event


def test_shared_unknown_task_event_fixture_is_negative_case():
    contract = _contract()
    event = _fixture("unknown")

    assert event["schema_version"] == contract["schema_version"]
    assert event["event_type"] not in contract["event_types"]
    assert event["type"] == event["event_type"]
