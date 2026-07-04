"""Runs endpoints — create, stream, wait, cancel.

Implements the LangGraph Platform runs API on top of
:class:`deerflow.agents.runs.RunManager` and
:class:`deerflow.agents.stream_bridge.StreamBridge`.

SSE format is aligned with the LangGraph Platform protocol so that
the ``useStream`` React hook from ``@langchain/langgraph-sdk/react``
works without modification.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import mimetypes
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_checkpointer, get_current_user, get_feedback_repo, get_run_event_store, get_run_manager, get_run_store, get_stream_bridge
from app.gateway.pagination import trim_run_message_page
from app.gateway.path_utils import get_request_storage_user_id, resolve_thread_virtual_path
from app.gateway.services import resolve_thread_run, sse_consumer, start_run, wait_for_run_completion
from app.gateway.utils import sanitize_log_param
from deerflow.runtime import RunRecord, RunStatus, serialize_channel_values_for_api
from deerflow.runtime.artifacts import build_artifact_index
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY, get_original_user_content_text, message_to_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["runs"])
REGENERATE_HISTORY_SCAN_LIMIT = 200
HIDDEN_CONTROL_MESSAGE_NAMES = frozenset({"summary", "loop_warning", "todo_reminder", "todo_completion_reminder"})
INTERNAL_CONTEXT_TAG_RE = re.compile(r"<(uploaded_files|slash_skill_activation)>[\s\S]*?</\1>")
_CANCEL_WAIT_TIMEOUT_SECONDS = 10.0
_RUN_TERMINAL_REASON_ALIASES = {
    "boundary_blocked": "boundary_stopped",
    "lease_expired_recovered": "worker_lost",
    "polling_timed_out": "timeout",
    "rollback_failed_owner_lost": "rollback_failed",
    "timed_out": "timeout",
    "user_cancelled": "cancelled",
}
_WORKER_LOST_ERROR_MARKERS = (
    "gateway restarted before this run reached a durable final state",
    "worker lost",
    "owner lost",
)
_ACTIVE_ARTIFACT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}
_ARTIFACT_HASH_CHUNK_BYTES = 1024 * 1024
_ARTIFACT_TEXT_SAMPLE_BYTES = 8192
_SENSITIVE_RUN_ERROR_MARKERS = (
    "secret",
    "stack trace",
    "traceback",
    "token",
    "api key",
    "password",
)
_MAX_PUBLIC_RUN_ERROR_CHARS = 200


async def _bounded_wait_for_cancelled_task(task: asyncio.Task) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_CANCEL_WAIT_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=202, detail="Cancel requested; run did not settle before wait timeout")
    except (asyncio.CancelledError, Exception):
        pass


def compute_run_durations(runs) -> dict[str, int]:
    """Map run_id -> duration in seconds from run timestamps."""
    from datetime import datetime

    durations: dict[str, int] = {}
    for r in runs:
        if r.created_at and r.updated_at:
            try:
                created = datetime.fromisoformat(r.created_at.replace("Z", "+00:00"))
                updated = datetime.fromisoformat(r.updated_at.replace("Z", "+00:00"))
                # Note: updated_at - created_at represents the row's total lifetime,
                # which can slightly overshoot the actual AI turn end if the row is mutated later.
                durations[r.run_id] = int((updated - created).total_seconds())
            except Exception:
                logger.warning("Failed to parse timestamps for run %s", r.run_id, exc_info=True)
    return durations


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    assistant_id: str | None = Field(default=None, description="Agent / assistant to use")
    input: dict[str, Any] | None = Field(default=None, description="Graph input (e.g. {messages: [...]})")
    command: dict[str, Any] | None = Field(default=None, description="LangGraph Command")
    metadata: dict[str, Any] | None = Field(default=None, description="Run metadata")
    config: dict[str, Any] | None = Field(default=None, description="RunnableConfig overrides")
    context: dict[str, Any] | None = Field(default=None, description="DeerFlow context overrides (model_name, thinking_enabled, etc.)")
    webhook: str | None = Field(default=None, description="Completion callback URL")
    checkpoint_id: str | None = Field(default=None, description="Resume from checkpoint")
    checkpoint: dict[str, Any] | None = Field(default=None, description="Full checkpoint object")
    interrupt_before: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt before")
    interrupt_after: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt after")
    stream_mode: list[str] | str | None = Field(default=None, description="Stream mode(s)")
    stream_subgraphs: bool = Field(default=False, description="Include subgraph events")
    stream_resumable: bool | None = Field(default=None, description="SSE resumable mode")
    on_disconnect: Literal["cancel", "continue"] = Field(default="cancel", description="Behaviour on SSE disconnect")
    on_completion: Literal["delete", "keep"] = Field(default="keep", description="Delete temp thread on completion")
    multitask_strategy: Literal["reject", "rollback", "interrupt", "enqueue"] = Field(default="reject", description="Concurrency strategy")
    after_seconds: float | None = Field(default=None, description="Delayed execution")
    if_not_exists: Literal["reject", "create"] = Field(default="create", description="Thread creation policy")
    feedback_keys: list[str] | None = Field(default=None, description="LangSmith feedback keys")


class RegeneratePrepareRequest(BaseModel):
    message_id: str = Field(..., min_length=1, description="Assistant message id to regenerate")


class RegeneratePrepareResponse(BaseModel):
    input: dict[str, Any]
    checkpoint: dict[str, Any]
    metadata: dict[str, Any]
    target_run_id: str


class RunResponse(BaseModel):
    run_id: str
    thread_id: str
    assistant_id: str | None = None
    status: str
    terminal_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    multitask_strategy: str = "reject"
    created_at: str = ""
    updated_at: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    message_count: int = 0


class ThreadTokenUsageModelBreakdown(BaseModel):
    tokens: int = 0
    runs: int = Field(
        default=0,
        description="Number of runs in which this model appeared; counts are non-exclusive for runs that used multiple models.",
    )


class ThreadTokenUsageCallerBreakdown(BaseModel):
    lead_agent: int = 0
    subagent: int = 0
    middleware: int = 0


class ThreadTokenUsageResponse(BaseModel):
    thread_id: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_runs: int = 0
    by_model: dict[str, ThreadTokenUsageModelBreakdown] = Field(default_factory=dict)
    by_caller: ThreadTokenUsageCallerBreakdown = Field(default_factory=ThreadTokenUsageCallerBreakdown)


class ThreadContextUsageSnapshot(BaseModel):
    run_id: str
    caller: str = "lead_agent"
    llm_call_index: int = 0
    message_count: int = 0
    tool_schema_count: int = 0
    char_count: int = 0
    estimated_tokens: int = 0
    role_counts: dict[str, int] = Field(default_factory=dict)
    seq: int = 0
    created_at: str = ""


class ThreadContextUsageResponse(BaseModel):
    thread_id: str
    latest: ThreadContextUsageSnapshot | None = None
    latest_lead: ThreadContextUsageSnapshot | None = None
    by_caller: dict[str, ThreadContextUsageSnapshot] = Field(default_factory=dict)
    recent: list[ThreadContextUsageSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _supports_user_id_keyword(callable_obj: Any) -> bool:
    """Return True when a store method can accept ``user_id=...``."""
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    return "user_id" in parameters or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def _cancel_conflict_detail(run_id: str, record: RunRecord) -> str:
    if record.status in (RunStatus.pending, RunStatus.running):
        return f"Run {run_id} is not active on this worker and cannot be cancelled"
    return f"Run {run_id} is not cancellable (status: {record.status.value})"


async def _resolve_thread_run_for_user(thread_id: str, run_id: str, request: Request, *, user_id: str) -> RunRecord:
    run_mgr = get_run_manager(request)
    record = await run_mgr.get(run_id, user_id=user_id)
    if record is None or record.thread_id != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return record


def _normalize_run_terminal_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    return _RUN_TERMINAL_REASON_ALIASES.get(reason, reason)


def _run_terminal_reason(record: RunRecord) -> str | None:
    stored_reason = _normalize_run_terminal_reason(getattr(record, "terminal_reason", None))
    if stored_reason is not None:
        return stored_reason
    if record.status == RunStatus.success:
        return "success"
    if record.status == RunStatus.timeout:
        return "timeout"
    if record.status == RunStatus.interrupted:
        return "cancelled"
    if record.status == RunStatus.error:
        error = (record.error or "").strip().lower()
        if "rolled back by user" in error:
            return "rolled_back"
        if any(marker in error for marker in _WORKER_LOST_ERROR_MARKERS):
            return "worker_lost"
        return "failed"
    return None


def run_error_for_response(error: str | None) -> str | None:
    if not error:
        return None
    text = error.strip()
    if len(text) > _MAX_PUBLIC_RUN_ERROR_CHARS or "\n" in text or any(marker in text.lower() for marker in _SENSITIVE_RUN_ERROR_MARKERS):
        return "Run failed"
    return text


def _artifact_file_metadata(thread_id: str, virtual_path: str, *, user_id: str | None) -> dict[str, Any]:
    if ".skill/" in virtual_path:
        return {}
    actual_path = resolve_thread_virtual_path(thread_id, virtual_path, user_id=user_id)
    if not actual_path.is_file():
        return {}

    mime_type, _ = mimetypes.guess_type(actual_path)
    digest = hashlib.sha256()
    size = 0
    sample = b""
    with actual_path.open("rb") as file:
        while chunk := file.read(_ARTIFACT_HASH_CHUNK_BYTES):
            size += len(chunk)
            if len(sample) < _ARTIFACT_TEXT_SAMPLE_BYTES:
                sample += chunk[: _ARTIFACT_TEXT_SAMPLE_BYTES - len(sample)]
            digest.update(chunk)

    display_policy = "inline"
    if mime_type in _ACTIVE_ARTIFACT_MIME_TYPES or (mime_type is None and b"\x00" in sample):
        display_policy = "attachment"

    metadata: dict[str, Any] = {
        "display_policy": display_policy,
        "sha256": digest.hexdigest(),
        "size_bytes": size,
    }
    if mime_type:
        metadata["mime_type"] = mime_type
    return metadata


def _attach_artifact_file_metadata(entries: list[dict[str, Any]], thread_id: str, *, user_id: str | None) -> list[dict[str, Any]]:
    for entry in entries:
        virtual_path = entry.get("virtual_path")
        if not isinstance(virtual_path, str) or not virtual_path:
            continue
        try:
            entry.update(_artifact_file_metadata(thread_id, virtual_path, user_id=user_id))
        except Exception:
            logger.debug("Could not fingerprint artifact %s for thread %s", virtual_path, sanitize_log_param(thread_id), exc_info=True)
    return entries


def _record_to_response(record: RunRecord) -> RunResponse:
    return RunResponse(
        run_id=record.run_id,
        thread_id=record.thread_id,
        assistant_id=record.assistant_id,
        status=record.status.value,
        terminal_reason=_run_terminal_reason(record),
        metadata=record.metadata,
        kwargs=record.kwargs,
        multitask_strategy=record.multitask_strategy,
        created_at=record.created_at,
        updated_at=record.updated_at,
        total_input_tokens=record.total_input_tokens,
        total_output_tokens=record.total_output_tokens,
        total_tokens=record.total_tokens,
        llm_call_count=record.llm_call_count,
        lead_agent_tokens=record.lead_agent_tokens,
        subagent_tokens=record.subagent_tokens,
        middleware_tokens=record.middleware_tokens,
        message_count=record.message_count,
    )


def _message_id(message: Any) -> str | None:
    value = getattr(message, "id", None)
    if value is None and isinstance(message, dict):
        value = message.get("id")
    return str(value) if value else None


def _message_type(message: Any) -> str | None:
    value = getattr(message, "type", None)
    if value is None and isinstance(message, dict):
        value = message.get("type") or message.get("role")
    if value == "assistant":
        return "ai"
    return str(value) if value else None


def _message_name(message: Any) -> str | None:
    value = getattr(message, "name", None)
    if value is None and isinstance(message, dict):
        value = message.get("name")
    return str(value) if value else None


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _message_text(message: Any) -> str:
    return message_to_text(message)


def _message_additional_kwargs(message: Any) -> dict[str, Any]:
    value = getattr(message, "additional_kwargs", None)
    if value is None and isinstance(message, dict):
        value = message.get("additional_kwargs")
    return dict(value or {}) if isinstance(value, dict) else {}


def _is_hidden_or_control_message(message: Any) -> bool:
    message_type = _message_type(message)
    additional_kwargs = _message_additional_kwargs(message)
    name = _message_name(message)
    return message_type == "remove" or (name is not None and name in HIDDEN_CONTROL_MESSAGE_NAMES) or additional_kwargs.get("hide_from_ui") is True


def _is_visible_human_message(message: Any) -> bool:
    return _message_type(message) == "human" and not _is_hidden_or_control_message(message)


def _is_visible_ai_message(message: Any) -> bool:
    return _message_type(message) == "ai" and not _is_hidden_or_control_message(message)


def _is_slash_skill_activation_only(message: Any) -> bool:
    if _message_type(message) != "human":
        return False
    text = _message_text(message)
    if "<slash_skill_activation>" not in text:
        return False
    public_text = INTERNAL_CONTEXT_TAG_RE.sub("", text)
    public_text = re.sub(r"^--- BEGIN USER INPUT ---\n?", "", public_text)
    public_text = re.sub(r"\n?--- END USER INPUT ---$", "", public_text).strip()
    return public_text == ""


def _message_display(content: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata.get("caller") == "task_event":
        return {"visible_in_chat": False, "surface": "control", "reason": "task_event"}
    additional_kwargs = _message_additional_kwargs(content)
    if additional_kwargs.get("hide_from_ui") is True:
        return {"visible_in_chat": False, "surface": "hidden", "reason": "hide_from_ui"}
    if _message_type(content) == "remove":
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}
    name = _message_name(content)
    if name is not None and name in HIDDEN_CONTROL_MESSAGE_NAMES:
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}
    if _is_slash_skill_activation_only(content):
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}

    message_type = _message_type(content)
    caller = str(metadata.get("caller") or "")
    if message_type == "human":
        reason = "human_message"
    elif message_type == "ai":
        reason = "middleware_message" if caller.startswith("middleware:") else "lead_ai_response"
    elif message_type == "tool":
        reason = "tool_message"
    else:
        reason = "message"
    return {"visible_in_chat": True, "surface": "chat", "reason": reason}


def attach_message_display(rows: list[dict]) -> list[dict]:
    for row in rows:
        metadata = row.get("metadata")
        row["display"] = _message_display(
            row.get("content"),
            metadata if isinstance(metadata, dict) else {},
        )
    return rows


def _context_usage_snapshot_from_event(event: dict[str, Any]) -> ThreadContextUsageSnapshot | None:
    content = event.get("content")
    if not isinstance(content, dict):
        return None

    role_counts = content.get("role_counts")
    if not isinstance(role_counts, dict):
        role_counts = {}

    def _int_value(key: str) -> int:
        value = content.get(key)
        return value if isinstance(value, int) else 0

    return ThreadContextUsageSnapshot(
        run_id=str(event.get("run_id") or content.get("run_id") or ""),
        caller=str(content.get("caller") or event.get("metadata", {}).get("caller") or "lead_agent"),
        llm_call_index=_int_value("llm_call_index"),
        message_count=_int_value("message_count"),
        tool_schema_count=_int_value("tool_schema_count"),
        char_count=_int_value("char_count"),
        estimated_tokens=_int_value("estimated_tokens"),
        role_counts={str(k): int(v) for k, v in role_counts.items() if isinstance(v, int)},
        seq=event.get("seq") if isinstance(event.get("seq"), int) else 0,
        created_at=str(event.get("created_at") or ""),
    )


def _checkpoint_messages(checkpoint_tuple: Any) -> list[Any]:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
    return messages if isinstance(messages, list) else []


def _checkpoint_configurable(checkpoint_tuple: Any) -> dict[str, Any]:
    config = getattr(checkpoint_tuple, "config", None) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    return dict(configurable) if isinstance(configurable, dict) else {}


def _checkpoint_response(checkpoint_tuple: Any) -> dict[str, Any]:
    configurable = _checkpoint_configurable(checkpoint_tuple)
    checkpoint_id = configurable.get("checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(status_code=409, detail="Checkpoint is missing checkpoint_id")
    return {
        "checkpoint_ns": str(configurable.get("checkpoint_ns") or ""),
        "checkpoint_id": str(checkpoint_id),
        "checkpoint_map": configurable.get("checkpoint_map"),
    }


def _clean_human_message_for_regenerate(message: Any) -> dict[str, Any]:
    additional_kwargs = _message_additional_kwargs(message)
    content = get_original_user_content_text(_message_content(message), additional_kwargs)
    additional_kwargs.pop(ORIGINAL_USER_CONTENT_KEY, None)
    additional_kwargs.pop("hide_from_ui", None)

    clean_message: dict[str, Any] = {
        "type": "human",
        "content": [{"type": "text", "text": content}],
        "additional_kwargs": additional_kwargs,
    }
    message_id = _message_id(message)
    if message_id:
        clean_message["id"] = message_id
    name = _message_name(message)
    if name:
        clean_message["name"] = name
    return clean_message


def _event_message_id(row: dict[str, Any]) -> str | None:
    content = row.get("content")
    if isinstance(content, BaseMessage):
        return _message_id(content)
    if isinstance(content, dict):
        return _message_id(content)
    return None


def _run_last_ai_matches_message(record: RunRecord, message: Any) -> bool:
    last_ai_message = (record.last_ai_message or "").strip()
    if not last_ai_message:
        return False
    target_text = _message_text(message).strip()
    if not target_text:
        return False
    return last_ai_message == target_text[: len(last_ai_message)]


async def _find_target_run_id(thread_id: str, message_id: str, target_message: Any, request: Request) -> str:
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    list_messages_kwargs: dict[str, Any] = {"limit": REGENERATE_HISTORY_SCAN_LIMIT}
    if _supports_user_id_keyword(event_store.list_messages):
        list_messages_kwargs["user_id"] = user_id
    rows = await event_store.list_messages(thread_id, **list_messages_kwargs)
    for row in reversed(rows):
        if row.get("event_type") not in {"ai_message", "llm.ai.response"}:
            continue
        if _event_message_id(row) == message_id:
            run_id = row.get("run_id")
            if isinstance(run_id, str) and run_id:
                return run_id
    run_mgr = get_run_manager(request)
    records = await run_mgr.list_by_thread(thread_id, user_id=user_id, limit=10)
    fallback_record = next(
        (record for record in records if record.status == RunStatus.success and _run_last_ai_matches_message(record, target_message)),
        None,
    )
    if fallback_record is not None:
        return fallback_record.run_id
    if len(rows) >= REGENERATE_HISTORY_SCAN_LIMIT:
        logger.warning(
            "Could not find source run for regenerate message %s in recent run events for thread %s (limit=%s)",
            message_id,
            thread_id,
            REGENERATE_HISTORY_SCAN_LIMIT,
        )
    raise HTTPException(status_code=409, detail="Could not find source run for assistant message")


async def _find_base_checkpoint_before_human(thread_id: str, human_message_id: str, request: Request) -> Any:
    checkpointer = get_checkpointer(request)
    base_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    try:
        checkpoints = [item async for item in checkpointer.alist(base_config, limit=REGENERATE_HISTORY_SCAN_LIMIT)]
    except Exception as exc:
        logger.exception("Failed to list checkpoints for regenerate thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to inspect checkpoint history") from exc

    previous_checkpoint = None
    for checkpoint_tuple in reversed(checkpoints):
        messages = _checkpoint_messages(checkpoint_tuple)
        message_ids = {_message_id(message) for message in messages}
        if human_message_id in message_ids:
            if previous_checkpoint is None:
                raise HTTPException(
                    status_code=409,
                    detail="Could not find an addressable checkpoint before the target user message",
                )
            return previous_checkpoint
        if _checkpoint_configurable(checkpoint_tuple).get("checkpoint_id"):
            previous_checkpoint = checkpoint_tuple

    if len(checkpoints) >= REGENERATE_HISTORY_SCAN_LIMIT:
        logger.warning(
            "Could not locate target user message %s in recent checkpoint history for thread %s (limit=%s)",
            human_message_id,
            thread_id,
            REGENERATE_HISTORY_SCAN_LIMIT,
        )
    raise HTTPException(
        status_code=409,
        detail=(f"Could not locate target user message in recent checkpoint history (limit={REGENERATE_HISTORY_SCAN_LIMIT})"),
    )


async def _prepare_regenerate_payload(thread_id: str, message_id: str, request: Request) -> RegeneratePrepareResponse:
    checkpointer = get_checkpointer(request)
    latest_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    try:
        latest_checkpoint = await checkpointer.aget_tuple(latest_config)
    except Exception as exc:
        logger.exception("Failed to read latest checkpoint for regenerate thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to read latest checkpoint") from exc
    if latest_checkpoint is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} has no checkpoint")

    messages = _checkpoint_messages(latest_checkpoint)
    target_index = next((i for i, message in enumerate(messages) if _message_id(message) == message_id), None)
    if target_index is None:
        raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
    target_message = messages[target_index]
    if not _is_visible_ai_message(target_message):
        raise HTTPException(status_code=409, detail="Only visible assistant messages can be regenerated")

    latest_visible_ai = next((message for message in reversed(messages) if _is_visible_ai_message(message)), None)
    if _message_id(latest_visible_ai) != message_id:
        raise HTTPException(status_code=409, detail="Only the latest assistant message can be regenerated")

    previous_human = next((message for message in reversed(messages[:target_index]) if _is_visible_human_message(message)), None)
    if previous_human is None:
        raise HTTPException(status_code=409, detail="Could not find the user message for this assistant response")
    previous_human_id = _message_id(previous_human)
    if not previous_human_id:
        raise HTTPException(status_code=409, detail="The source user message is missing an id")

    base_checkpoint_tuple = await _find_base_checkpoint_before_human(thread_id, previous_human_id, request)
    target_run_id = await _find_target_run_id(thread_id, message_id, target_message, request)
    checkpoint = _checkpoint_response(base_checkpoint_tuple)
    metadata = {
        "regenerate_from_message_id": message_id,
        "regenerate_from_run_id": target_run_id,
        "regenerate_checkpoint_id": checkpoint["checkpoint_id"],
    }
    return RegeneratePrepareResponse(
        input={"messages": [_clean_human_message_for_regenerate(previous_human)]},
        checkpoint=checkpoint,
        metadata=metadata,
        target_run_id=target_run_id,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{thread_id}/runs/regenerate/prepare", response_model=RegeneratePrepareResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def prepare_regenerate_run(
    thread_id: str,
    body: RegeneratePrepareRequest,
    request: Request,
) -> RegeneratePrepareResponse:
    """Prepare input and checkpoint for regenerating the latest assistant turn."""
    return await _prepare_regenerate_payload(thread_id, body.message_id, request)


@router.post("/{thread_id}/runs", response_model=RunResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def create_run(thread_id: str, body: RunCreateRequest, request: Request) -> RunResponse:
    """Create a background run (returns immediately)."""
    record = await start_run(body, thread_id, request)
    return _record_to_response(record)


@router.post("/{thread_id}/runs/stream")
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def stream_run(thread_id: str, body: RunCreateRequest, request: Request) -> StreamingResponse:
    """Create a run and stream events via SSE.

    The response includes a ``Content-Location`` header with the run's
    resource URL, matching the LangGraph Platform protocol.  The
    ``useStream`` React hook uses this to extract run metadata.
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # LangGraph Platform includes run metadata in this header.
            # The SDK uses a greedy regex to extract the run id from this path,
            # so it must point at the canonical run resource without extra suffixes.
            "Content-Location": f"/api/threads/{thread_id}/runs/{record.run_id}",
        },
    )


@router.post("/{thread_id}/runs/wait", response_model=dict)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def wait_run(thread_id: str, body: RunCreateRequest, request: Request) -> dict:
    """Create a run and block until it completes, returning the final state."""
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    completed = True
    if record.task is not None:
        completed = await wait_for_run_completion(bridge, record, request, run_mgr)

    if completed:
        record = await run_mgr.get(record.run_id) or record
    if completed and record.status == RunStatus.success:
        checkpointer = get_checkpointer(request)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            checkpoint_tuple = await checkpointer.aget_tuple(config)
            if checkpoint_tuple is not None:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                channel_values = checkpoint.get("channel_values", {})
                return serialize_channel_values_for_api(channel_values)
        except Exception:
            logger.exception("Failed to fetch final state for run %s", record.run_id)

    return {"status": record.status.value, "error": run_error_for_response(record.error)}


@router.get("/{thread_id}/runs", response_model=list[RunResponse])
@require_permission("runs", "read", owner_check=True)
async def list_runs(thread_id: str, request: Request) -> list[RunResponse]:
    """List all runs for a thread."""
    run_mgr = get_run_manager(request)
    user_id = await get_current_user(request)
    records = await run_mgr.list_by_thread(thread_id, user_id=user_id)
    return [_record_to_response(r) for r in records]


@router.get("/{thread_id}/runs/{run_id}", response_model=RunResponse)
@require_permission("runs", "read", owner_check=True)
async def get_run(thread_id: str, run_id: str, request: Request) -> RunResponse:
    """Get details of a specific run."""
    record = await resolve_thread_run(thread_id, run_id, request)
    return _record_to_response(record)


@router.post("/{thread_id}/runs/{run_id}/cancel")
@require_permission("runs", "cancel", owner_check=True, require_existing=True)
async def cancel_run(
    thread_id: str,
    run_id: str,
    request: Request,
    wait: bool = Query(default=False, description="Block until run completes after cancel"),
    action: Literal["interrupt", "rollback"] = Query(default="interrupt", description="Cancel action"),
) -> Response:
    """Cancel a running or pending run.

    - action=interrupt: Stop execution, keep current checkpoint (can be resumed)
    - action=rollback: Stop execution, revert to pre-run checkpoint state
    - wait=true: Block until the run fully stops, return 204
    - wait=false: Return immediately with 202
    """
    run_mgr = get_run_manager(request)
    record = await resolve_thread_run(thread_id, run_id, request)

    cancelled = await run_mgr.cancel(run_id, action=action)
    if not cancelled:
        raise HTTPException(status_code=409, detail=_cancel_conflict_detail(run_id, record))

    if wait and record.task is not None:
        try:
            await _bounded_wait_for_cancelled_task(record.task)
        except HTTPException:
            raise
        return Response(status_code=204)

    return Response(status_code=202)


@router.get("/{thread_id}/runs/{run_id}/join")
@require_permission("runs", "read", owner_check=True)
async def join_run(thread_id: str, run_id: str, request: Request) -> StreamingResponse:
    """Join an existing run's SSE stream."""
    run_mgr = get_run_manager(request)
    record = await resolve_thread_run(thread_id, run_id, request)
    if record.store_only and record.status in (RunStatus.pending, RunStatus.running):
        raise HTTPException(status_code=409, detail=f"Run {run_id} is not active on this worker and cannot be streamed")

    bridge = get_stream_bridge(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr, event_store=event_store, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Register GET and POST as separate routes so each method gets a unique OpenAPI
# operationId. ``api_route(methods=["GET", "POST"])`` shares one route registration
# across both methods, which makes FastAPI emit the same ``operationId`` twice and
# warn about a duplicate operation id during OpenAPI generation.
@router.get("/{thread_id}/runs/{run_id}/stream", response_model=None)
@router.post("/{thread_id}/runs/{run_id}/stream", response_model=None)
@require_permission("runs", "read", owner_check=True)
async def stream_existing_run(
    thread_id: str,
    run_id: str,
    request: Request,
    action: Literal["interrupt", "rollback"] | None = Query(default=None, description="Cancel action"),
    wait: int = Query(default=0, description="Block until cancelled (1) or return immediately (0)"),
):
    """Join an existing run's SSE stream (GET), or cancel-then-stream (POST).

    The LangGraph SDK's ``joinStream`` and ``useStream`` stop button both use
    ``POST`` to this endpoint.  When ``action=interrupt`` or ``action=rollback``
    is present the run is cancelled first; the response then streams any
    remaining buffered events so the client observes a clean shutdown.
    """
    run_mgr = get_run_manager(request)
    record = await resolve_thread_run(thread_id, run_id, request)
    if record.store_only and action is None and record.status in (RunStatus.pending, RunStatus.running):
        raise HTTPException(status_code=409, detail=f"Run {run_id} is not active on this worker and cannot be streamed")

    # Cancel if an action was requested (stop-button / interrupt flow)
    if action is not None:
        cancelled = await run_mgr.cancel(run_id, action=action)
        if not cancelled:
            raise HTTPException(status_code=409, detail=_cancel_conflict_detail(run_id, record))
        if wait and record.task is not None:
            try:
                await _bounded_wait_for_cancelled_task(record.task)
            except HTTPException:
                raise
            return Response(status_code=204)

    bridge = get_stream_bridge(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr, event_store=event_store, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Messages / Events / Token usage endpoints
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_thread_messages(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, le=200),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
    run_id: str | None = Query(default=None),
) -> list[dict]:
    """Return displayable messages for a thread, optionally scoped to a single run, with feedback attached."""
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    scoped_run: RunRecord | None = None
    if run_id is not None:
        scoped_run = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
        list_messages_kwargs: dict[str, Any] = {"limit": limit, "before_seq": before_seq, "after_seq": after_seq}
        if _supports_user_id_keyword(event_store.list_messages_by_run):
            list_messages_kwargs["user_id"] = user_id
        messages = await event_store.list_messages_by_run(thread_id, run_id, **list_messages_kwargs)
    else:
        list_messages_kwargs = {"limit": limit, "before_seq": before_seq, "after_seq": after_seq}
        if _supports_user_id_keyword(event_store.list_messages):
            list_messages_kwargs["user_id"] = user_id
        messages = await event_store.list_messages(thread_id, **list_messages_kwargs)
    attach_message_display(messages)

    # Find the last AI message per run_id. AI messages are persisted by
    # RunJournal with event_type "llm.ai.response" (see runtime/journal.py);
    # the event store returns that value verbatim, so match on it here.
    last_ai_per_run: dict[str, int] = {}  # run_id -> index in messages list
    for i, msg in enumerate(messages):
        if msg.get("event_type") == "llm.ai.response":
            last_ai_per_run[msg["run_id"]] = i

    # Attach feedback to the last AI message of each run. Only query when there
    # is an AI message to attach it to — threads with no completed AI turn yet
    # would otherwise pay for a grouped feedback lookup whose result is unused.
    feedback_map: dict[str, dict] = {}
    if last_ai_per_run:
        feedback_repo = get_feedback_repo(request)
        feedback_map = await feedback_repo.list_by_thread_grouped(thread_id, user_id=user_id)

    last_ai_indices = set(last_ai_per_run.values())
    for i, msg in enumerate(messages):
        if i in last_ai_indices:
            run_id = msg["run_id"]
            fb = feedback_map.get(run_id)
            msg["feedback"] = (
                {
                    "feedback_id": fb["feedback_id"],
                    "rating": fb["rating"],
                    "comment": fb.get("comment"),
                }
                if fb
                else None
            )
        else:
            msg["feedback"] = None

    if scoped_run is not None:
        runs = [scoped_run]
    else:
        run_mgr = get_run_manager(request)
        runs = await run_mgr.list_by_thread(thread_id, user_id=user_id)
    run_durations = compute_run_durations(runs)

    if run_durations:
        for msg in messages:
            content = msg.get("content", {})
            if isinstance(content, dict) and content.get("type") == "ai":
                rid = msg.get("run_id")
                if rid and rid in run_durations:
                    if "additional_kwargs" not in content:
                        content["additional_kwargs"] = {}
                    content["additional_kwargs"]["turn_duration"] = run_durations[rid]

    return messages


@router.get("/{thread_id}/runs/{run_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_run_messages(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """Return paginated messages for a specific run.

    Response: { data: [...], has_more: bool }
    """
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    event_store = get_run_event_store(request)
    list_messages_kwargs: dict[str, Any] = {
        "limit": limit + 1,
        "before_seq": before_seq,
        "after_seq": after_seq,
    }
    if _supports_user_id_keyword(event_store.list_messages_by_run):
        list_messages_kwargs["user_id"] = user_id
    rows = await event_store.list_messages_by_run(
        thread_id,
        run_id,
        **list_messages_kwargs,
    )
    data, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)
    attach_message_display(data)

    if data:
        durations = compute_run_durations([record])
        duration = durations.get(run_id)
        if duration is not None:
            for msg in reversed(data):
                content = msg.get("content")
                metadata = msg.get("metadata", {})
                is_middleware = str(metadata.get("caller", "")).startswith("middleware:")
                if isinstance(content, dict) and content.get("type") == "ai" and not is_middleware:
                    if "additional_kwargs" not in content:
                        content["additional_kwargs"] = {}
                    content["additional_kwargs"]["turn_duration"] = duration

    return {"data": data, "has_more": has_more}


@router.get("/{thread_id}/runs/{run_id}/events")
@require_permission("runs", "read", owner_check=True)
async def list_run_events(
    thread_id: str,
    run_id: str,
    request: Request,
    event_types: str | None = Query(default=None),
    limit: int = Query(default=500, le=2000),
    after_seq: int | None = Query(default=None),
) -> list[dict]:
    """Return the full event stream for a run (debug/audit)."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    event_store = get_run_event_store(request)
    types = event_types.split(",") if event_types else None
    list_events_kwargs: dict[str, Any] = {"event_types": types, "limit": limit, "after_seq": after_seq}
    if _supports_user_id_keyword(event_store.list_events):
        list_events_kwargs["user_id"] = user_id
    return await event_store.list_events(thread_id, run_id, **list_events_kwargs)


@router.get("/{thread_id}/runs/{run_id}/artifacts")
@require_permission("runs", "read", owner_check=True)
async def list_run_artifacts(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=500, le=2000),
) -> list[dict[str, Any]]:
    """Return runtime-observed artifacts for a run."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    event_store = get_run_event_store(request)
    event_types = ["artifact.presented", "task_completed", "task_failed", "task_cancelled", "task_timed_out"]
    list_events_kwargs: dict[str, Any] = {"event_types": event_types, "limit": limit}
    if _supports_user_id_keyword(event_store.list_events):
        list_events_kwargs["user_id"] = user_id
    events = await event_store.list_events(thread_id, run_id, **list_events_kwargs)
    index = build_artifact_index(events)
    return await asyncio.to_thread(_attach_artifact_file_metadata, index, thread_id, user_id=user_id)


@router.get("/{thread_id}/token-usage", response_model=ThreadTokenUsageResponse)
@require_permission("threads", "read", owner_check=True)
async def thread_token_usage(
    thread_id: str,
    request: Request,
    include_active: bool = Query(default=False, description="Include running run progress snapshots"),
) -> ThreadTokenUsageResponse:
    """Thread-level token usage aggregation."""
    run_store = get_run_store(request)
    if include_active:
        agg = await run_store.aggregate_tokens_by_thread(thread_id, include_active=True)
    else:
        agg = await run_store.aggregate_tokens_by_thread(thread_id)
    return ThreadTokenUsageResponse(thread_id=thread_id, **agg)


@router.get("/{thread_id}/context-usage", response_model=ThreadContextUsageResponse)
@require_permission("threads", "read", owner_check=True)
async def thread_context_usage(
    thread_id: str,
    request: Request,
    run_limit: int = Query(default=20, ge=1, le=50, description="Recent runs to scan for context snapshots"),
    limit: int = Query(default=20, ge=1, le=50, description="Recent context snapshots to return"),
) -> ThreadContextUsageResponse:
    """Latest model-input context estimates for a thread."""
    run_store = get_run_store(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    runs = await run_store.list_by_thread(thread_id, user_id=user_id, limit=run_limit)

    snapshots: list[ThreadContextUsageSnapshot] = []
    for run in runs:
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        list_events_kwargs: dict[str, Any] = {"event_types": ["llm.context"], "limit": 200}
        if _supports_user_id_keyword(event_store.list_events):
            list_events_kwargs["user_id"] = user_id
        events = await event_store.list_events(thread_id, run_id, **list_events_kwargs)
        for event in events:
            snapshot = _context_usage_snapshot_from_event(event)
            if snapshot is not None:
                snapshots.append(snapshot)

    snapshots.sort(key=lambda item: (item.created_at, item.seq), reverse=True)
    recent = snapshots[:limit]
    by_caller: dict[str, ThreadContextUsageSnapshot] = {}
    for snapshot in snapshots:
        by_caller.setdefault(snapshot.caller, snapshot)

    return ThreadContextUsageResponse(
        thread_id=thread_id,
        latest=recent[0] if recent else None,
        latest_lead=by_caller.get("lead_agent"),
        by_caller=by_caller,
        recent=recent,
    )
