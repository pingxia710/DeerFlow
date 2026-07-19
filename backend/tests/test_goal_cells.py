"""Recursive Goal Cells reuse Threads and return complete results through the inbox."""

from __future__ import annotations

import hashlib
import stat
from types import SimpleNamespace

import pytest
from langgraph.store.memory import InMemoryStore

from app.gateway import goal_cells as goal_cells_module
from app.gateway.command_room_background import _RequestSnapshot
from app.gateway.goal_cells import BoundGoalCellDispatcher
from deerflow.config.paths import Paths
from deerflow.persistence.thread_meta import MemoryThreadMetaStore
from deerflow.persistence.workspace_event import (
    GOAL_MANDATE_REVISED,
    MemoryWorkspaceEventStore,
)
from deerflow.runtime.goal_cells import (
    GOAL_CELL_CREATED,
    GOAL_CELL_INPUT_CAPSULE_KEY,
    GOAL_CELL_PARENT_RUN_KEY,
    GOAL_CELL_PARENT_THREAD_KEY,
    GOAL_CELL_RETURNED,
    GOAL_CELL_ROOT_THREAD_KEY,
)
from deerflow.tools.builtins.goal_cell_tool import (
    create_goal_cell_tool,
    return_to_parent_tool,
)


def _request(app, *, user_id: str = "user-1"):
    return SimpleNamespace(
        app=app,
        scope={
            "headers": [],
            "state": {"user": SimpleNamespace(id=user_id)},
        },
    )


@pytest.mark.anyio
async def test_goal_cell_launch_uses_one_idempotent_internal_admission(monkeypatch):
    from app.gateway import services

    calls = []

    async def start_run(body, thread_id, request, **kwargs):
        calls.append((body, thread_id, request, kwargs))
        return SimpleNamespace(record=SimpleNamespace(run_id="cell-run"))

    monkeypatch.setattr(services, "start_run", start_run)
    snapshot = _RequestSnapshot(
        app=SimpleNamespace(state=SimpleNamespace()),
        headers=[],
        state={"user": SimpleNamespace(id="user-1")},
    )
    arguments = {
        "child_thread_id": "cell-1",
        "parent_thread_id": "root-thread",
        "parent_run_id": "root-run",
        "tool_call_id": "create-cell",
        "brief": "Complete local brief.",
        "capability_refs": ["read-only"],
        "workspace_ref": "workspace://one",
        "input_capsule": [],
        "wake_context": {"model_name": "safe-model"},
    }

    await goal_cells_module._start_goal_cell_run(snapshot, **arguments)
    await goal_cells_module._start_goal_cell_run(snapshot, **arguments)

    first_body, first_thread_id, _request_value, first_kwargs = calls[0]
    second_kwargs = calls[1][3]
    first_admission = first_kwargs["command_room_wake_admission"]
    second_admission = second_kwargs["command_room_wake_admission"]
    assert first_thread_id == "cell-1"
    assert first_admission.wake_id == second_admission.wake_id
    assert first_admission.thread_id == "cell-1"
    assert first_admission.user_id == "user-1"
    assert first_kwargs["return_command_room_wake_admission"] is True
    assert first_body.context["agent_name"] == "command-room"
    message = first_body.input["messages"][0]
    assert message["additional_kwargs"]["hide_from_ui"] is True
    assert "Complete local brief." in message["content"]
    assert "they grant no program permission" in message["content"]
    assert "read-only" in message["content"]


@pytest.mark.anyio
async def test_goal_cells_recurse_and_return_complete_result(monkeypatch):
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    workspace_store = MemoryWorkspaceEventStore()
    app = SimpleNamespace(
        state=SimpleNamespace(
            thread_store=thread_store,
            workspace_event_store=workspace_store,
        )
    )
    await thread_store.create(
        "root-thread",
        assistant_id="command-room",
        user_id="user-1",
        display_name="Root goal",
    )
    await workspace_store.append(
        thread_id="root-thread",
        user_id="user-1",
        event_type=GOAL_MANDATE_REVISED,
        body="Explore autonomously inside the confirmed boundary.",
        author_run_id="root-run",
        event_id="root-mandate",
    )

    launched_threads = []

    async def start_goal_cell(_snapshot, **kwargs):
        launched_threads.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(run_id=f"run-{len(launched_threads)}"))

    monkeypatch.setattr(
        goal_cells_module,
        "_start_goal_cell_run",
        start_goal_cell,
    )

    dispatched_jobs = []

    class BackgroundDispatcher:
        async def dispatch(self, job):
            dispatched_jobs.append(job)

    dispatcher = BoundGoalCellDispatcher(
        _request(app),
        BackgroundDispatcher(),
    )
    first = await dispatcher.create_cell(
        parent_thread_id="root-thread",
        parent_run_id="root-run",
        parent_round_id="root-round",
        tool_call_id="create-first",
        display_name="Research cell",
        brief="Complete local research brief.",
        capability_refs=["web-read"],
        workspace_ref="workspace://research",
        wake_context={"model_name": "safe-model"},
    )
    first_thread = await thread_store.get(
        first["child_thread_id"],
        user_id="user-1",
    )
    first_context = await workspace_store.current_context(
        thread_id=first["child_thread_id"],
        user_id="user-1",
    )
    assert first_thread["metadata"][GOAL_CELL_PARENT_THREAD_KEY] == "root-thread"
    assert first_thread["metadata"][GOAL_CELL_PARENT_RUN_KEY] == "root-run"
    assert first_thread["metadata"][GOAL_CELL_ROOT_THREAD_KEY] == "root-thread"
    assert first_context["goal_mandate"]["body"] == ("Explore autonomously inside the confirmed boundary.")
    assert first_context["operating_brief"]["body"] == ("Complete local research brief.")
    root_events = await workspace_store.list_by_thread(
        "root-thread",
        user_id="user-1",
    )
    created = next(row for row in root_events if row["event_type"] == GOAL_CELL_CREATED)
    assert created["body"] == "Complete local research brief."
    assert created["metadata"]["child_thread_id"] == first["child_thread_id"]

    nested = await dispatcher.create_cell(
        parent_thread_id=first["child_thread_id"],
        parent_run_id=first["child_run_id"],
        parent_round_id=None,
        tool_call_id="create-nested",
        display_name="Nested cell",
        brief="Complete nested brief.",
        capability_refs=[],
        workspace_ref=None,
        wake_context={},
    )
    nested_thread = await thread_store.get(
        nested["child_thread_id"],
        user_id="user-1",
    )
    assert nested_thread["metadata"][GOAL_CELL_PARENT_THREAD_KEY] == (first["child_thread_id"])
    assert nested_thread["metadata"][GOAL_CELL_ROOT_THREAD_KEY] == "root-thread"

    receipt = await dispatcher.return_to_parent(
        child_thread_id=first["child_thread_id"],
        child_run_id=first["child_run_id"],
        tool_call_id="return-first",
        complete_result="Complete Workstream Lead result, unchanged.",
        artifact_refs=["artifact://report"],
        wake_context={},
    )
    assert receipt["parent_thread_id"] == "root-thread"
    assert len(dispatched_jobs) == 1
    job = dispatched_jobs[0]
    assert job.thread_id == "root-thread"
    assert job.source_run_id == "root-run"
    assert job.result_author_run_id == first["child_run_id"]
    assert job.result_metadata["source_goal_cell_thread_id"] == (first["child_thread_id"])
    outcome = await job.execute()
    assert outcome.result == "Complete Workstream Lead result, unchanged."
    child_events = await workspace_store.list_by_thread(
        first["child_thread_id"],
        user_id="user-1",
    )
    returned = next(row for row in child_events if row["event_type"] == GOAL_CELL_RETURNED)
    assert returned["body"] == "Complete Workstream Lead result, unchanged."
    assert returned["metadata"]["artifact_refs"] == ["artifact://report"]


@pytest.mark.anyio
async def test_goal_cell_seals_exact_parent_input_bytes_without_judging_them(monkeypatch, tmp_path):
    paths = Paths(tmp_path / "deer-flow")
    paths.ensure_thread_dirs("root-thread", user_id="user-1")
    source = paths.sandbox_work_dir("root-thread", user_id="user-1") / "brief.md"
    source.write_bytes(b"original parent material")
    monkeypatch.setattr(goal_cells_module, "get_paths", lambda: paths)

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    workspace_store = MemoryWorkspaceEventStore()
    app = SimpleNamespace(
        state=SimpleNamespace(
            thread_store=thread_store,
            workspace_event_store=workspace_store,
        )
    )
    await thread_store.create(
        "root-thread",
        assistant_id="command-room",
        user_id="user-1",
        display_name="Root goal",
    )

    async def start_goal_cell(_snapshot, **_kwargs):
        return SimpleNamespace(record=SimpleNamespace(run_id="cell-run"))

    monkeypatch.setattr(goal_cells_module, "_start_goal_cell_run", start_goal_cell)

    class BackgroundDispatcher:
        async def dispatch(self, _job):
            return None

    dispatcher = BoundGoalCellDispatcher(_request(app), BackgroundDispatcher())
    created = await dispatcher.create_cell(
        parent_thread_id="root-thread",
        parent_run_id="root-run",
        parent_round_id=None,
        tool_call_id="create-sealed-cell",
        display_name="Sealed cell",
        brief="Use the selected input material.",
        capability_refs=[],
        workspace_ref=None,
        input_refs=["workspace/brief.md"],
        wake_context={},
    )

    sealed = paths.sandbox_inputs_dir(created["child_thread_id"], user_id="user-1") / "workspace" / "brief.md"
    assert sealed.read_bytes() == b"original parent material"
    assert stat.S_IMODE(sealed.stat().st_mode) & 0o222 == 0
    source.write_bytes(b"later parent change")
    assert sealed.read_bytes() == b"original parent material"
    recovered = await dispatcher.create_cell(
        parent_thread_id="root-thread",
        parent_run_id="root-run",
        parent_round_id=None,
        tool_call_id="create-sealed-cell",
        display_name="Sealed cell",
        brief="Use the selected input material.",
        capability_refs=[],
        workspace_ref=None,
        input_refs=["workspace/brief.md"],
        wake_context={},
    )
    assert recovered["child_thread_id"] == created["child_thread_id"]
    assert sealed.read_bytes() == b"original parent material"

    child = await thread_store.get(created["child_thread_id"], user_id="user-1")
    capsule = child["metadata"][GOAL_CELL_INPUT_CAPSULE_KEY]
    assert capsule == [
        {
            "source_ref": "workspace/brief.md",
            "input_ref": "/mnt/user-data/inputs/workspace/brief.md",
            "sha256": hashlib.sha256(b"original parent material").hexdigest(),
            "bytes": len(b"original parent material"),
        }
    ]
    parent_events = await workspace_store.list_by_thread("root-thread", user_id="user-1")
    event = next(row for row in parent_events if row["event_type"] == GOAL_CELL_CREATED)
    assert event["metadata"]["input_capsule"] == capsule


@pytest.mark.anyio
async def test_goal_cell_tools_pass_complete_ai_authored_text():
    calls = []

    class Dispatcher:
        async def create_cell(self, **kwargs):
            calls.append(("create", kwargs))
            return {
                "child_thread_id": "cell-1",
                "child_run_id": "cell-run-1",
                "parent_thread_id": "root-thread",
            }

        async def return_to_parent(self, **kwargs):
            calls.append(("return", kwargs))
            return {
                "parent_thread_id": "root-thread",
                "background_task_id": "return-task",
            }

    runtime = SimpleNamespace(
        context={
            "agent_name": "command-room",
            "thread_id": "root-thread",
            "run_id": "root-run",
            "round_id": "root-round",
            "model_name": "safe-model",
            "__goal_cell_dispatcher": Dispatcher(),
        }
    )
    brief = "Complete local brief\nwith all original context."
    create_message = await create_goal_cell_tool.coroutine(
        runtime=runtime,
        brief=brief,
        display_name="Cell",
        capability_refs=["read-only"],
        workspace_ref="workspace://one",
        input_refs=["workspace/brief.md"],
        tool_call_id="create-1",
    )
    result = "Complete local result\nwith every material fact."
    return_message = await return_to_parent_tool.coroutine(
        runtime=runtime,
        complete_result=result,
        artifact_refs=["artifact://one"],
        tool_call_id="return-1",
    )

    assert "cell-1" in create_message.content
    assert calls[0][1]["brief"] == brief
    assert calls[0][1]["parent_round_id"] == "root-round"
    assert calls[0][1]["input_refs"] == ["workspace/brief.md"]
    assert "root-thread" in return_message.content
    assert calls[1][1]["complete_result"] == result
    assert calls[1][1]["artifact_refs"] == ["artifact://one"]
