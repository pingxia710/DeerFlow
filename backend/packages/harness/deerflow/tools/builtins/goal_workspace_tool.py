"""Chair-authored factual records for the current Goal Workspace."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage

from deerflow.persistence.workspace_event import (
    GOAL_MANDATE_REVISED,
    OPERATING_BRIEF_REVISED,
    ORGANIZATION_MAP_REVISED,
)
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

_STORE_CONTEXT_KEY = "__workspace_event_store"
_EVENT_TYPES = {
    "goal_mandate": GOAL_MANDATE_REVISED,
    "operating_brief": OPERATING_BRIEF_REVISED,
    "organization_map": ORGANIZATION_MAP_REVISED,
}


def _event_id(kind: str, run_id: str, tool_call_id: str) -> str:
    raw = f"{kind}:{run_id}:{tool_call_id}"
    if len(raw) <= 128:
        return raw
    return f"{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _chair_runtime(
    runtime: Runtime,
) -> tuple[dict[str, Any], Any, str | None, str | None]:
    context = runtime.context if isinstance(runtime.context, dict) else {}
    return (
        context,
        context.get(_STORE_CONTEXT_KEY),
        context.get("thread_id"),
        context.get("run_id"),
    )


@tool("record_goal_workspace", parse_docstring=True)
async def record_goal_workspace_tool(
    runtime: Runtime,
    kind: Literal["goal_mandate", "operating_brief", "organization_map"],
    body: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> ToolMessage:
    """Append one complete AI-authored Goal Workspace record.

    The program stores this body unchanged. It does not parse the text, infer
    stages, judge completion, or choose any next action.

    Do not use for read-only discovery or simple questions answerable with the
    Chair's read-only tools—those need no Mandate, Brief, or Map.

    Args:
        kind: Record a relatively stable human Goal Mandate, the Chair's
            current Operating Brief, or its current temporary Organization Map.
        body: Complete natural-language record that later Chair runs can read.
    """
    context, store, thread_id, run_id = _chair_runtime(runtime)
    if context.get("agent_name") != "command-room":
        return ToolMessage(
            "Only NextOS Command Room can record its Goal Workspace.",
            tool_call_id=tool_call_id,
        )
    append = getattr(store, "append", None)
    if not callable(append) or not isinstance(thread_id, str) or not thread_id:
        return ToolMessage(
            "Goal Workspace persistence is unavailable for this run.",
            tool_call_id=tool_call_id,
        )
    if not isinstance(run_id, str) or not run_id:
        return ToolMessage(
            "Goal Workspace recording requires a run identity.",
            tool_call_id=tool_call_id,
        )

    row = await append(
        thread_id=thread_id,
        user_id=resolve_runtime_user_id(runtime),
        event_type=_EVENT_TYPES[kind],
        body=body,
        author_run_id=run_id,
        event_id=_event_id(kind, run_id, tool_call_id),
    )
    return ToolMessage(
        f"Recorded {kind} revision {row['revision']} (sha256:{row['content_hash']}).",
        tool_call_id=tool_call_id,
    )


@tool("read_workspace_results", parse_docstring=True)
async def read_workspace_results_tool(
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    after_seq: int | None = None,
) -> ToolMessage:
    """Read complete persisted result envelopes from this Goal Workspace.

    Reading never acknowledges, deletes, summarizes, or changes a result.

    Args:
        after_seq: Optional factual event sequence to read after. Omit it to
            read every result after the Chair's last explicit acknowledgement.
    """
    context, store, thread_id, _run_id = _chair_runtime(runtime)
    read = getattr(store, "result_inbox", None)
    if context.get("agent_name") != "command-room" or not callable(read):
        return ToolMessage(
            "Goal Workspace result inbox is unavailable for this run.",
            tool_call_id=tool_call_id,
        )
    if not isinstance(thread_id, str) or not thread_id:
        return ToolMessage("Thread identity is unavailable.", tool_call_id=tool_call_id)
    inbox = await read(
        thread_id=thread_id,
        user_id=resolve_runtime_user_id(runtime),
        after_seq=after_seq,
    )
    lines = [
        "[Goal Workspace result inbox]",
        f"acknowledged_through_seq: {inbox['acknowledged_through_seq']}",
        f"notified_through_seq: {inbox['notified_through_seq']}",
    ]
    for row in inbox["results"]:
        metadata = row.get("metadata") or {}
        lines.extend(
            [
                "",
                f"--- result_seq: {row['revision']} ---",
                f"source_run_id: {metadata.get('source_run_id')}",
                f"task_id: {metadata.get('task_id')}",
                f"role: {metadata.get('role')}",
                f"description: {metadata.get('description')}",
                f"status: {metadata.get('status')}",
                f"error: {metadata.get('error')}",
                "Complete result:",
                row["body"],
            ]
        )
    if not inbox["results"]:
        lines.append("(no matching results)")
    return ToolMessage("\n".join(lines), tool_call_id=tool_call_id)


@tool("read_goal_workspace_history", parse_docstring=True)
async def read_goal_workspace_history_tool(
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    before_revision: int | None = None,
    limit: int = 20,
) -> ToolMessage:
    """Read one bounded page of complete append-only Goal Workspace facts.

    The records are newest first and returned unchanged. This never selects
    relevant facts, summarizes them, acknowledges results, or changes the
    Workspace.

    Args:
        before_revision: Read records with a revision lower than this exclusive
            cursor. Omit for the newest page.
        limit: Number of factual records to read, from 1 through 100.
    """
    context, store, thread_id, _run_id = _chair_runtime(runtime)
    history = getattr(store, "history", None)
    if context.get("agent_name") != "command-room" or not callable(history):
        return ToolMessage(
            "Goal Workspace history is unavailable for this run.",
            tool_call_id=tool_call_id,
        )
    if not isinstance(thread_id, str) or not thread_id:
        return ToolMessage("Thread identity is unavailable.", tool_call_id=tool_call_id)
    if before_revision is not None and before_revision < 1:
        return ToolMessage(
            "History cursor must be a positive revision.",
            tool_call_id=tool_call_id,
        )
    if limit < 1 or limit > 100:
        return ToolMessage(
            "History limit must be between 1 and 100.",
            tool_call_id=tool_call_id,
        )
    page = await history(
        thread_id=thread_id,
        user_id=resolve_runtime_user_id(runtime),
        before_revision=before_revision,
        limit=limit,
    )
    lines = [
        "[Goal Workspace factual history]",
        "Complete append-only records; their relevance and meaning remain the Chair's judgment.",
    ]
    for row in page["events"]:
        lines.extend(
            [
                "",
                f"--- revision: {row['revision']} ---",
                f"event_type: {row['event_type']}",
                f"author_run_id: {row.get('author_run_id')}",
                f"created_at: {row['created_at']}",
                f"content_hash: {row['content_hash']}",
                "metadata:",
                json.dumps(row.get("metadata") or {}, ensure_ascii=False),
                "Complete record:",
                row["body"],
            ]
        )
    if not page["events"]:
        lines.append("(no matching historical records)")
    next_before_revision = page.get("next_before_revision")
    if isinstance(next_before_revision, int):
        lines.append(f"next_before_revision: {next_before_revision}")
    else:
        lines.append("next_before_revision: (none)")
    return ToolMessage("\n".join(lines), tool_call_id=tool_call_id)


@tool("acknowledge_workspace_results", parse_docstring=True)
async def acknowledge_workspace_results_tool(
    runtime: Runtime,
    through_seq: int,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> ToolMessage:
    """Record the Chair's explicit acknowledgement of result envelopes.

    Call only after the Chair has read and incorporated every result through
    the given factual sequence. This records the AI decision; it is not a
    program quality judgment or completion gate.

    Args:
        through_seq: Highest result event sequence explicitly acknowledged by
            the Chair.
    """
    context, store, thread_id, run_id = _chair_runtime(runtime)
    acknowledge = getattr(store, "acknowledge_results", None)
    if context.get("agent_name") != "command-room" or not callable(acknowledge):
        return ToolMessage(
            "Goal Workspace result acknowledgement is unavailable for this run.",
            tool_call_id=tool_call_id,
        )
    if not isinstance(thread_id, str) or not thread_id or not isinstance(run_id, str) or not run_id:
        return ToolMessage(
            "Result acknowledgement requires thread and run identity.",
            tool_call_id=tool_call_id,
        )
    try:
        row = await acknowledge(
            thread_id=thread_id,
            user_id=resolve_runtime_user_id(runtime),
            through_seq=through_seq,
            author_run_id=run_id,
            event_id=_event_id("result_ack", run_id, tool_call_id),
        )
    except ValueError as exc:
        return ToolMessage(f"Could not acknowledge results: {exc}", tool_call_id=tool_call_id)
    return ToolMessage(
        f"Acknowledged result inbox through sequence {through_seq} in event revision {row['revision']}.",
        tool_call_id=tool_call_id,
    )


__all__ = [
    "acknowledge_workspace_results_tool",
    "read_goal_workspace_history_tool",
    "read_workspace_results_tool",
    "record_goal_workspace_tool",
]
