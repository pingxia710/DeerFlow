import asyncio
import importlib
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from deerflow.command_room.ai_workspace import (
    ensure_command_room_ai_workspace,
    prepare_command_room_container_task,
    record_container_task_completion,
    record_container_task_terminal,
)

lifecycle_module = importlib.import_module("deerflow.tools.builtins.command_room_lifecycle")


def _complete(task, text="AI-authored handoff"):
    task.output_path.write_text(text, encoding="utf-8")
    assert record_container_task_completion(task)


def _runtime(workspace):
    return SimpleNamespace(
        context={"agent_name": "command-room", "thread_id": "thread-1"},
        state={"thread_data": {"workspace_path": str(workspace)}},
    )


def _invoke(tool, **kwargs):
    return asyncio.run(tool.coroutine(**kwargs))


def test_close_task_starts_fixed_steward_and_project_complete_starts_both_curators(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    root = ensure_command_room_ai_workspace(workspace, "thread-1")
    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-1",
        delivery_cycle_index=1,
    )
    _complete(execution)
    review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="review-1",
        delivery_cycle_index=1,
    )
    _complete(review)
    dispatched = []

    async def dispatch(_runtime, **kwargs):
        dispatched.append(kwargs)
        return ToolMessage(
            content="accepted for background execution",
            name="task",
            tool_call_id=kwargs["tool_call_id"],
            additional_kwargs={"background_task_id": kwargs["tool_call_id"]},
        )

    monkeypatch.setattr(lifecycle_module, "_dispatch_fixed_role", dispatch)
    runtime = _runtime(workspace)
    closed = _invoke(
        lifecycle_module.close_task_tool,
        runtime=runtime,
        summary="Review accepted.",
        review_cycle_index=1,
        tool_call_id="close-1",
    )

    assert closed.additional_kwargs["project_lifecycle_status"] == "task_closed"
    assert dispatched[0]["subagent_type"] == "project-steward"

    steward = prepare_command_room_container_task(
        root,
        container="project-steward",
        task_id="steward-1",
        delivery_cycle_index=1,
    )
    _complete(steward, "Project complete.")
    dispatched.clear()
    project_complete = _invoke(
        lifecycle_module.project_status_tool,
        runtime=runtime,
        status="project_complete",
        summary="Substantive work is complete.",
        tool_call_id="status-1",
    )

    assert project_complete.additional_kwargs["project_lifecycle_status"] == "project_complete"
    assert [item["subagent_type"] for item in dispatched] == [
        "debt-curator",
        "learning-curator",
    ]


def test_accept_handoff_records_explicit_chair_recovery_and_unblocks_stage(tmp_path):
    workspace = tmp_path / "workspace"
    root = ensure_command_room_ai_workspace(workspace, "thread-1")
    forward = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-forward",
        container_artifact="technical-forward",
    )
    _complete(forward, "Forward technical design")
    opposition = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition",
        container_artifact="technical-opposition",
    )
    opposition.output_path.write_text("Complete contrary technical angle", encoding="utf-8")
    record_container_task_terminal(opposition, status="failed")

    accepted = _invoke(
        lifecycle_module.accept_handoff_tool,
        runtime=_runtime(workspace),
        artifact_kind="technical-opposition",
        tool_call_id="accept-1",
    )

    assert accepted.additional_kwargs["container_artifact_status"] == "completed"
    assert accepted.additional_kwargs["accepted_by_chair"] is True
    technical_plan = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-plan",
        container_artifact="technical-plan",
    )
    assert technical_plan.artifact_kind == "technical-plan"


def test_close_task_scopes_fixed_project_steward_to_its_work_package(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    root = ensure_command_room_ai_workspace(workspace, "thread-1")
    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="package-execution",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    _complete(execution)
    review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="package-review",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    _complete(review)
    dispatched = []

    async def dispatch(_runtime, **kwargs):
        dispatched.append(kwargs)
        return ToolMessage(
            content="accepted for background execution",
            name="task",
            tool_call_id=kwargs["tool_call_id"],
            additional_kwargs={"background_task_id": kwargs["tool_call_id"]},
        )

    monkeypatch.setattr(lifecycle_module, "_dispatch_fixed_role", dispatch)
    closed = _invoke(
        lifecycle_module.close_task_tool,
        runtime=_runtime(workspace),
        summary="Package review accepted.",
        review_cycle_index=1,
        work_package_id="package-a",
        tool_call_id="close-package-a",
    )

    assert closed.additional_kwargs["work_package_id"] == "package-a"
    assert dispatched[0]["work_package_id"] == "package-a"
