"""Explicit AI-authored lifecycle transitions for the Command Room."""

from __future__ import annotations

from typing import Annotated, Literal

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage

from deerflow.command_room.ai_workspace import (
    RecoverableContainerArtifact,
    accept_container_artifact,
    ensure_command_room_ai_workspace,
    record_project_lifecycle_status,
)
from deerflow.tools.types import Runtime


def _command_room_workspace(runtime: Runtime, work_package_id: str | None = None):
    context = getattr(runtime, "context", None)
    state = getattr(runtime, "state", None)
    context = context if isinstance(context, dict) else {}
    state = state if isinstance(state, dict) else {}
    if context.get("agent_name") != "command-room":
        raise RuntimeError("Project lifecycle tools are available only to the Command Room Chair.")
    thread_id = context.get("thread_id")
    thread_data = state.get("thread_data")
    thread_data = thread_data if isinstance(thread_data, dict) else {}
    workspace_path = thread_data.get("workspace_path")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(workspace_path, str) or not workspace_path:
        raise RuntimeError("Command Room lifecycle requires complete thread workspace identity.")
    # The thread workspace is the durable project surface shared by sequential Chair runs.
    return ensure_command_room_ai_workspace(
        workspace_path,
        thread_id,
        work_package_id=work_package_id,
    )


async def _dispatch_fixed_role(
    runtime: Runtime,
    *,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: str,
    container: str,
    delivery_cycle_index: int | None = None,
    work_package_id: str | None = None,
) -> ToolMessage:
    from deerflow.tools.builtins.task_tool import task_tool

    coroutine = getattr(task_tool, "coroutine", None)
    if coroutine is None:  # pragma: no cover - tool construction invariant.
        raise RuntimeError("Task tool coroutine is unavailable.")
    return await coroutine(
        runtime=runtime,
        description=description,
        prompt=prompt,
        subagent_type=subagent_type,
        tool_call_id=tool_call_id,
        container=container,
        delivery_cycle_index=delivery_cycle_index,
        work_package_id=work_package_id,
    )


@tool("accept_handoff", parse_docstring=True)
async def accept_handoff_tool(
    runtime: Runtime,
    artifact_kind: RecoverableContainerArtifact,
    tool_call_id: Annotated[str, InjectedToolCallId],
    work_package_id: str | None = None,
) -> ToolMessage:
    """Accept a changed Planning or Technical Design angle after child failure.

    Use this only when the child transport failed, timed out, or was cancelled
    after writing a complete angle artifact that the Chair has inspected. The
    program verifies only that the assigned file changed; the Chair owns the
    quality decision. Retry the child instead when the artifact is incomplete.

    Args:
        artifact_kind: Failed optional-stage angle artifact explicitly accepted by the Chair.
    """

    workspace = _command_room_workspace(runtime, work_package_id)
    record = accept_container_artifact(workspace, artifact_kind=artifact_kind)
    return ToolMessage(
        content=(f"The Chair explicitly accepted the changed {artifact_kind} handoff after {record['recovered_from_status']}. Continue the current stage using that artifact."),
        name="accept_handoff",
        tool_call_id=tool_call_id,
        additional_kwargs={
            "container_artifact_kind": artifact_kind,
            "container_artifact_status": record["status"],
            "accepted_by_chair": True,
            **({"work_package_id": work_package_id} if work_package_id else {}),
        },
    )


@tool("close_task", parse_docstring=True)
async def close_task_tool(
    runtime: Runtime,
    summary: str,
    review_cycle_index: int,
    tool_call_id: Annotated[str, InjectedToolCallId],
    work_package_id: str | None = None,
) -> ToolMessage:
    """Accept one reviewed delivery task and start the fixed Project Steward.

    This is an explicit Chair decision, not a program quality verdict. The
    program verifies only that Review N factually completed, records
    ``task_closed``, and starts the fixed Project Steward role in background.

    Args:
        summary: Chair's concise accepted-task result and remaining project context.
        review_cycle_index: The accepted Review N cycle number.
    """

    workspace = _command_room_workspace(runtime, work_package_id)
    record_project_lifecycle_status(
        workspace,
        status="task_closed",
        summary=summary,
        review_cycle_index=review_cycle_index,
    )
    steward = await _dispatch_fixed_role(
        runtime,
        description=f"Project Steward after Review {review_cycle_index}",
        prompt=(
            "The Chair explicitly accepted this reviewed delivery task. Inspect the accepted goal, execution and "
            "review handoffs, and current project state. Decide from a project-management perspective whether the "
            "project should continue, is substantively complete, or is blocked. State the next objective when it "
            "should continue. Do not dispatch work or alter the Chair's acceptance.\n\n"
            f"Chair task-close summary:\n{summary}"
        ),
        subagent_type="project-steward",
        tool_call_id=f"{tool_call_id}-project-steward",
        container="project-steward",
        delivery_cycle_index=review_cycle_index,
        work_package_id=work_package_id,
    )
    return ToolMessage(
        content=(f"Review {review_cycle_index} was explicitly closed by the Chair. The fixed Project Steward was started in background and will automatically return to the Chair."),
        name="close_task",
        tool_call_id=tool_call_id,
        additional_kwargs={
            "project_lifecycle_status": "task_closed",
            "review_cycle_index": review_cycle_index,
            "background_task_id": steward.additional_kwargs.get("background_task_id"),
            **({"work_package_id": work_package_id} if work_package_id else {}),
        },
    )


@tool("project_status", parse_docstring=True)
async def project_status_tool(
    runtime: Runtime,
    status: Literal["continue", "project_complete", "blocked", "closed"],
    summary: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    work_package_id: str | None = None,
) -> ToolMessage:
    """Record the Chair's explicit project status after Project Steward input.

    ``project_complete`` starts the fixed Debt and Learning Curators in
    background. ``continue`` leaves the next intelligent task choice to the
    Chair. ``closed`` is allowed only after both curators and a later governance
    Review have factually completed.

    Args:
        status: Explicit Chair lifecycle decision.
        summary: Decision basis and next objective, blocker, or closure basis.
    """

    workspace = _command_room_workspace(runtime, work_package_id)
    record = record_project_lifecycle_status(workspace, status=status, summary=summary)
    if status == "project_complete":
        debt = await _dispatch_fixed_role(
            runtime,
            description="Curate project debt",
            prompt=(
                "The Chair explicitly declared the substantive project work complete. Inspect the accepted delivery "
                "and Project Steward handoff. Classify concrete technical, governance, documentation, test, and skill "
                "debt. Separate closure-required updates from optional backlog. Do not make the updates yourself.\n\n"
                f"Chair project-complete basis:\n{summary}"
            ),
            subagent_type="debt-curator",
            tool_call_id=f"{tool_call_id}-debt-curator",
            container="debt-curation",
            work_package_id=work_package_id,
        )
        learning = await _dispatch_fixed_role(
            runtime,
            description="Curate durable project learning",
            prompt=(
                "The Chair explicitly declared the substantive project work complete. Inspect the accepted delivery "
                "and Project Steward handoff. Identify only durable lessons that should update Skills, AGENTS, "
                "Progress, tests, or references, with concrete evidence and no speculative rule growth. Do not make "
                "the updates yourself.\n\n"
                f"Chair project-complete basis:\n{summary}"
            ),
            subagent_type="learning-curator",
            tool_call_id=f"{tool_call_id}-learning-curator",
            container="learning-curation",
            work_package_id=work_package_id,
        )
        content = "Project completion was explicitly recorded. Debt Curator and Learning Curator were started in background; wait for both results before governance Execution and Review."
        background_ids = [
            debt.additional_kwargs.get("background_task_id"),
            learning.additional_kwargs.get("background_task_id"),
        ]
    elif status == "continue":
        content = "Project continuation was explicitly recorded. The Chair must now choose and dispatch the next bounded Planning, Technical Design, or Execution action without waiting for the human to say continue."
        background_ids = []
    elif status == "blocked":
        content = "Project blocked status was explicitly recorded. Report the blocker and the exact human-only or external condition required to resume."
        background_ids = []
    else:
        content = "Project governance was explicitly closed after curator work and a later governance Review. No automatic next project round will be started."
        background_ids = []
    return ToolMessage(
        content=content,
        name="project_status",
        tool_call_id=tool_call_id,
        additional_kwargs={
            "project_lifecycle_status": status,
            "review_cycle_index": record.get("review_cycle_index"),
            "background_task_ids": [task_id for task_id in background_ids if task_id],
            **({"work_package_id": work_package_id} if work_package_id else {}),
        },
    )


__all__ = ["accept_handoff_tool", "close_task_tool", "project_status_tool"]
