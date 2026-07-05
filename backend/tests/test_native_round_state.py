import pytest

from app.gateway.routers import thread_runs
from deerflow.agents.middlewares.round_context_middleware import format_native_round_context_for_model
from deerflow.persistence.round_state import MemoryRoundStateStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import get_effective_user_id


@pytest.mark.anyio
async def test_closed_round_followup_starts_new_round_with_next_action_only():
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)

    first = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "全面检查 DeerFlow"}]}},
    )
    await manager.set_status(first.run_id, RunStatus.running)
    await manager.set_status(first.run_id, RunStatus.success, terminal_reason="success")
    await manager.update_run_completion(first.run_id, status="success", last_ai_message="Next: implement native state.")

    second = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "好的，下一步"}]}},
    )

    assert first.round_id
    assert second.round_id
    assert second.round_id != first.round_id
    context = second.metadata["round_context"]
    assert context["parent_round_id"] == first.round_id
    assert context["current_intent"] == "好的，下一步"
    assert context["accepted_next_action"] == "Next: implement native state."


@pytest.mark.anyio
async def test_task_event_updates_task_lane_outside_visible_history():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    round_info = await round_store.bind_run(
        thread_id="thread-1",
        run_id="run-1",
        current_intent="start",
    )
    journal = RunJournal(
        "run-1",
        "thread-1",
        event_store,
        flush_threshold=100,
        round_store=round_store,
        round_id=round_info["round_id"],
    )

    journal.record_task_event(
        {
            "type": "task_started",
            "task_id": "task-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "subagent_type": "planner",
            "status": "in_progress",
        }
    )
    await journal.flush()

    assert round_store.task_lanes[("thread-1", "run-1", "task-1")]["role"] == "planner"
    assert round_store.task_lanes[("thread-1", "run-1", "task-1")]["status"] == "in_progress"
    [row] = await event_store.list_messages_by_run("thread-1", "run-1")
    assert row["content"]["round_id"] == round_info["round_id"]


@pytest.mark.anyio
async def test_task_refs_flow_into_native_round_context():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)

    record = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "run task"}]}},
    )
    await manager.set_status(record.run_id, RunStatus.running)
    journal = RunJournal(
        record.run_id,
        record.thread_id,
        event_store,
        flush_threshold=100,
        round_store=round_store,
        round_id=record.round_id,
    )
    journal.record_task_event(
        {
            "type": "task_completed",
            "task_id": "task-1",
            "thread_id": record.thread_id,
            "run_id": record.run_id,
            "subagent_type": "evidence",
            "status": "completed",
            "action_result": {
                "output_ref": "outputs/findings.md",
                "evidence_refs": ["command: pytest backend/tests/test_native_round_state.py -q; exit code: 0"],
            },
        }
    )
    await journal.flush()
    await manager.set_status(record.run_id, RunStatus.success, terminal_reason="success")
    await manager.update_run_completion(record.run_id, status="success", last_ai_message="Next: inspect refs.")

    context = record.metadata["round_context"]
    assert context["artifact_refs"] == ["outputs/findings.md"]
    assert context["evidence_refs"] == ["command: pytest backend/tests/test_native_round_state.py -q; exit code: 0"]
    text = format_native_round_context_for_model(context)
    assert text is not None
    assert "Current Intent: run task" in text
    assert "ArtifactRefs: outputs/findings.md" in text
    assert "EvidenceRefs: command: pytest backend/tests/test_native_round_state.py -q; exit code: 0" in text


@pytest.mark.anyio
async def test_native_round_routes_expose_rounds_and_task_lanes():
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    user_id = get_effective_user_id()
    record = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "route test"}]}},
        user_id=user_id,
    )
    await round_store.record_task_events(
        [
            {
                "type": "task_completed",
                "thread_id": record.thread_id,
                "run_id": record.run_id,
                "task_id": "task-1",
                "subagent_type": "planner",
                "status": "completed",
                "action_result": {"output_ref": "outputs/plan.md"},
            }
        ]
    )

    class AppState:
        round_state_store = round_store

    class Request:
        app = type("App", (), {"state": AppState()})()
        state = type("State", (), {"storage_user_id": None})()

    request = Request()
    rounds = await thread_runs.list_rounds.__wrapped__("thread-1", request, limit=50)
    tasks = await thread_runs.list_round_tasks.__wrapped__("thread-1", record.round_id, request, limit=100)

    assert rounds[0].round_id == record.round_id
    assert rounds[0].artifact_refs == ["outputs/plan.md"]
    assert tasks[0].task_id == "task-1"
    assert tasks[0].role == "planner"
