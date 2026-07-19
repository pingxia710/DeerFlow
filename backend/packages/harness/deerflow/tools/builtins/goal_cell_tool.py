"""Chair tools for recursive Goal Cell creation and explicit parent return."""

from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage

from deerflow.tools.types import Runtime

_DISPATCHER_CONTEXT_KEY = "__goal_cell_dispatcher"
_WAKE_CONTEXT_KEYS = frozenset(
    {
        "model_name",
        "mode",
        "thinking_enabled",
        "reasoning_effort",
        "reasoning_summary",
        "text_verbosity",
        "is_plan_mode",
        "subagent_enabled",
        "agent_name",
    }
)


def _runtime_context(runtime: Runtime) -> dict[str, Any]:
    return runtime.context if isinstance(runtime.context, dict) else {}


def _dispatcher(context: dict[str, Any]) -> Any | None:
    dispatcher = context.get(_DISPATCHER_CONTEXT_KEY)
    if callable(getattr(dispatcher, "create_cell", None)) and callable(getattr(dispatcher, "return_to_parent", None)):
        return dispatcher
    return None


def _wake_context(context: dict[str, Any]) -> dict[str, Any]:
    wake = {key: context[key] for key in _WAKE_CONTEXT_KEYS if key in context}
    wake["agent_name"] = "command-room"
    wake["subagent_enabled"] = True
    return wake


def _round_id(context: dict[str, Any]) -> str | None:
    value = context.get("round_id")
    if isinstance(value, str) and value:
        return value
    round_context = context.get("round_context")
    value = round_context.get("round_id") if isinstance(round_context, dict) else None
    return value if isinstance(value, str) and value else None


@tool("create_goal_cell", parse_docstring=True)
async def create_goal_cell_tool(
    runtime: Runtime,
    brief: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    display_name: str | None = None,
    capability_refs: list[str] | None = None,
    workspace_ref: str | None = None,
    input_refs: list[str] | None = None,
) -> ToolMessage:
    """Create and launch one recursive child Goal Workspace.

    The complete brief is persisted unchanged. Capability and workspace
    references are structural requests only; this tool does not grant new
    permissions. Exact ``input_refs`` are copied byte-for-byte into the child
    Goal Cell's read-only input capsule. The program does not choose or judge
    the materials. The child inherits the current Goal Mandate and runs the
    same Chair loop as a temporary Workstream Lead.

    Args:
        brief: Complete local mission, context, boundaries, and completion
            criteria for the child Chair.
        display_name: Optional human-readable Goal Cell name chosen by the Chair.
        capability_refs: Optional references to capabilities the child may need.
        workspace_ref: Optional reference to an existing factual workspace.
        input_refs: Optional exact parent files to snapshot, using
            ``workspace/...``, ``uploads/...``, or ``outputs/...`` paths.
    """
    context = _runtime_context(runtime)
    dispatcher = _dispatcher(context)
    thread_id = context.get("thread_id")
    run_id = context.get("run_id")
    if context.get("agent_name") != "command-room" or dispatcher is None:
        return ToolMessage(
            "Goal Cell creation is unavailable outside NextOS Command Room.",
            tool_call_id=tool_call_id,
        )
    if not isinstance(thread_id, str) or not thread_id or not isinstance(run_id, str) or not run_id:
        return ToolMessage(
            "Goal Cell creation requires current thread and run identity.",
            tool_call_id=tool_call_id,
        )
    try:
        result = await dispatcher.create_cell(
            parent_thread_id=thread_id,
            parent_run_id=run_id,
            parent_round_id=_round_id(context),
            tool_call_id=tool_call_id,
            display_name=display_name,
            brief=brief,
            capability_refs=list(capability_refs or []),
            workspace_ref=workspace_ref,
            input_refs=list(input_refs or []),
            wake_context=_wake_context(context),
        )
    except Exception as exc:
        return ToolMessage(
            f"Could not create Goal Cell: {exc}",
            tool_call_id=tool_call_id,
        )
    return ToolMessage(
        f"Goal Cell launched: thread_id={result['child_thread_id']}, run_id={result['child_run_id']}, parent_thread_id={result['parent_thread_id']}.",
        tool_call_id=tool_call_id,
    )


@tool("return_to_parent", parse_docstring=True)
async def return_to_parent_tool(
    runtime: Runtime,
    complete_result: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    artifact_refs: list[str] | None = None,
) -> ToolMessage:
    """Return one complete Goal Cell result to its parent Chair.

    This records and transports the AI-authored return unchanged. It does not
    mark the result accepted, judge quality, or close either Goal Workspace.

    Args:
        complete_result: Complete natural-language result for the parent Chair.
        artifact_refs: Optional factual references to produced artifacts.
    """
    context = _runtime_context(runtime)
    dispatcher = _dispatcher(context)
    thread_id = context.get("thread_id")
    run_id = context.get("run_id")
    if context.get("agent_name") != "command-room" or dispatcher is None:
        return ToolMessage(
            "Goal Cell return is unavailable outside NextOS Command Room.",
            tool_call_id=tool_call_id,
        )
    if not isinstance(thread_id, str) or not thread_id or not isinstance(run_id, str) or not run_id:
        return ToolMessage(
            "Goal Cell return requires current thread and run identity.",
            tool_call_id=tool_call_id,
        )
    try:
        result = await dispatcher.return_to_parent(
            child_thread_id=thread_id,
            child_run_id=run_id,
            tool_call_id=tool_call_id,
            complete_result=complete_result,
            artifact_refs=list(artifact_refs or []),
            wake_context=_wake_context(context),
        )
    except Exception as exc:
        return ToolMessage(
            f"Could not return Goal Cell result: {exc}",
            tool_call_id=tool_call_id,
        )
    return ToolMessage(
        f"Complete Goal Cell result was persisted and queued for factual parent wake: parent_thread_id={result['parent_thread_id']}, background_task_id={result['background_task_id']}.",
        tool_call_id=tool_call_id,
    )


__all__ = ["create_goal_cell_tool", "return_to_parent_tool"]
