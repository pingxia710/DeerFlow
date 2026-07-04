from types import SimpleNamespace

from app.gateway.routers.thread_runs import _record_to_response, _run_terminal_reason
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus


def test_run_record_terminal_reason_defaults_and_roundtrips():
    record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status=RunStatus.error,
        on_disconnect=DisconnectMode.cancel,
        terminal_reason="input_required",
    )

    values = record.__dict__.copy()
    values.pop("task")
    values.pop("abort_event")
    values["on_disconnect"] = DisconnectMode.cancel

    assert values["terminal_reason"] == "input_required"
    assert RunRecord(**values).terminal_reason == "input_required"


def test_run_terminal_reason_tolerates_legacy_record_without_attribute():
    record = SimpleNamespace(status=RunStatus.success)

    assert _run_terminal_reason(record) == "success"


def test_record_to_response_preserves_store_only_recovery_status():
    record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status="worker_lost",
        on_disconnect=DisconnectMode.cancel,
    )

    response = _record_to_response(record)

    assert response.status == "worker_lost"
    assert response.terminal_reason == "worker_lost"
