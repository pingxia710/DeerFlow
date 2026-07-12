"""Task tool for delegating work to subagents."""

import asyncio
import hashlib
import inspect
import logging
import re
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer

from deerflow.command_room.task_action_result import task_action_result_event, task_action_result_from_terminal_event
from deerflow.config import get_app_config
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.audit import record_subagent_handoff
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from deerflow.subagents.status_contract import SubagentStatusValue, make_subagent_additional_kwargs
from deerflow.tools.types import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)
_TASK_EVENT_SCHEMA_VERSION = "deerflow.task-event/v1"
_EVENT_PREVIEW_MAX_CHARS = 240
_SAFE_ACTION_RESULT_EVENT_KEYS = {
    "action_id",
    "description",
    "status",
    "terminal_reason",
    "evidence_refs",
    "output_ref",
    "risks",
    "conflicts",
    "open_questions",
}
_TASK_EVENT_IDENTITY_KEYS = {
    "type",
    "event_type",
    "schema_version",
    "thread_id",
    "run_id",
    "round_id",
    "task_id",
    "status",
    "started_at",
    "completed_at",
    "duration_ms",
}
_SECRET_LIKE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|ak(?:ia|as)[a-z0-9]{12,}|"
    r"(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*['\"]?[^'\"\s]+)"
)

# Scope subagent token usage by run plus tool call so concurrent conversations
# cannot consume each other's accounting before TokenUsageMiddleware merges it.
_subagent_usage_cache: dict[tuple[str | None, str], dict[str, int]] = {}


async def _record_subagent_handoff_async(**kwargs: Any) -> None:
    await asyncio.to_thread(record_subagent_handoff, **kwargs)


def _call_supports_run_id(func: Any) -> bool:
    """Return whether *func* can be called with a ``run_id=`` keyword."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        # If the signature cannot be inspected, prefer the modern scoped call so
        # real TypeError exceptions from inside the callable are not masked.
        return True

    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == "run_id" and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def _scoped_get_background_task_result(task_id: str, run_id: str | None = None) -> Any | None:
    if _call_supports_run_id(get_background_task_result):
        return get_background_task_result(task_id, run_id=run_id)
    return get_background_task_result(task_id)


def _scoped_cleanup_background_task(task_id: str, run_id: str | None = None) -> None:
    if _call_supports_run_id(cleanup_background_task):
        cleanup_background_task(task_id, run_id=run_id)
        return
    cleanup_background_task(task_id)


def _scoped_request_cancel_background_task(task_id: str, run_id: str | None = None) -> None:
    if _call_supports_run_id(request_cancel_background_task):
        request_cancel_background_task(task_id, run_id=run_id)
        return
    request_cancel_background_task(task_id)


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _subagent_usage_cache_key(tool_call_id: str, run_id: str | None = None) -> tuple[str | None, str]:
    return (str(run_id) if run_id else None, tool_call_id)


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, run_id: str | None = None, enabled: bool = True) -> None:
    if enabled and usage:
        _subagent_usage_cache[_subagent_usage_cache_key(tool_call_id, run_id)] = usage


def _iter_runtime_callbacks(runtime: Any) -> list[Any]:
    if runtime is None:
        return []
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return []
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks or not isinstance(callbacks, list):
        return []
    return callbacks


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _elapsed_ms(started_monotonic: float) -> int:
    return max(0, int((time.monotonic() - started_monotonic) * 1000))


def _task_event_base(
    event_type: str,
    task_id: str,
    *,
    thread_id: Any,
    run_id: Any,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": event_type,
        "event_type": event_type,
        "schema_version": _TASK_EVENT_SCHEMA_VERSION,
        "task_id": task_id,
        "redacted": True,
        "status": None,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "result_preview": None,
        "error_preview": None,
        "artifact_refs": [],
        "action_result": None,
        "usage": None,
    }
    if isinstance(thread_id, str) and thread_id:
        event["thread_id"] = thread_id
    if isinstance(run_id, str) and run_id:
        event["run_id"] = run_id
    return event


def _terminal_task_event_base(
    event_type: str,
    task_id: str,
    *,
    thread_id: Any,
    run_id: Any,
    started_at: str,
    started_monotonic: float,
) -> dict[str, Any]:
    return _task_event_base(
        event_type,
        task_id,
        thread_id=thread_id,
        run_id=run_id,
        started_at=started_at,
        completed_at=_utc_now_iso(),
        duration_ms=_elapsed_ms(started_monotonic),
    )


def _redacted_preview(value: Any, *, fallback: str = "") -> str:
    text = _redacted_tool_text(fallback if value is None else value)
    text = " ".join(text.split())
    if len(text) > _EVENT_PREVIEW_MAX_CHARS:
        return f"{text[:_EVENT_PREVIEW_MAX_CHARS]}..."
    return text


def _redacted_tool_text(value: Any) -> str:
    return _SECRET_LIKE_RE.sub("[redacted]", "" if value is None else str(value))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_task_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_task_event_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_task_event_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_task_event_value(item) for item in value]
    if isinstance(value, str):
        return _redacted_preview(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _redacted_preview(value)


def _sanitize_task_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_task_event_value(event)
    if not isinstance(sanitized, dict):
        return {}
    # These fields route the event to its durable run/round projection. Redacting
    # them can make an otherwise valid task event fail the journal's identity
    # check (for example a thread ID containing an incidental ``sk-`` substring).
    for key in _TASK_EVENT_IDENTITY_KEYS:
        if key not in event:
            continue
        value = event.get(key)
        if value is None or isinstance(value, bool | int | float | str):
            sanitized[key] = value
    return sanitized


def _compact_action_result_event(action_result: Any) -> dict[str, Any]:
    payload = task_action_result_event(action_result)["action_result"]
    compact = {key: payload[key] for key in _SAFE_ACTION_RESULT_EVENT_KEYS if key in payload}
    for key in ("summary", "error"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value)
        compact[key] = _redacted_preview(text)
        compact[f"{key}_sha256"] = _sha256_text(text)
        compact[f"{key}_chars"] = len(text)
    return compact


def _artifact_refs(action_result: Any) -> list[str]:
    refs: list[str] = []
    output_ref = getattr(action_result, "output_ref", None)
    if isinstance(output_ref, str) and output_ref:
        refs.append(output_ref)
    evidence_refs = getattr(action_result, "evidence_refs", None)
    if isinstance(evidence_refs, list):
        refs.extend(ref for ref in evidence_refs if isinstance(ref, str) and ref)
    return refs


def _runtime_observed_evidence_refs(result: Any) -> list[str]:
    refs = getattr(result, "evidence_refs", None)
    if not isinstance(refs, list):
        return []
    return list(dict.fromkeys(_redacted_preview(ref) for ref in refs if isinstance(ref, str) and ref.strip()))


def _format_task_success(result_text: str, *, observed_evidence_refs: list[str]) -> str:
    prefix = "Task Succeeded."
    if not observed_evidence_refs:
        return f"{prefix} Result: {result_text}"
    evidence = "\n".join(f"- {ref}" for ref in observed_evidence_refs)
    return f"{prefix} Runtime-observed evidence:\n{evidence}\nResult: {result_text}"


def _terminal_task_message(
    content: str,
    *,
    tool_call_id: str,
    status: SubagentStatusValue,
) -> ToolMessage:
    return ToolMessage(
        content=content,
        name="task",
        tool_call_id=tool_call_id,
        additional_kwargs=make_subagent_additional_kwargs(status),
    )


def pop_cached_subagent_usage(tool_call_id: str, *, run_id: str | None = None) -> dict | None:
    return _subagent_usage_cache.pop(_subagent_usage_cache_key(tool_call_id, run_id), None)


def clear_cached_subagent_usage_for_run(run_id: str) -> None:
    normalized_run_id = str(run_id)
    stale_keys = [key for key in _subagent_usage_cache if key[0] == normalized_run_id]
    for key in stale_keys:
        _subagent_usage_cache.pop(key, None)


def _is_subagent_terminal(result: Any) -> bool:
    """Return whether a background subagent result is safe to clean up."""
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None


async def _await_subagent_terminal(task_id: str, max_polls: int, run_id: str | None = None) -> Any | None:
    """Poll until the background subagent reaches a terminal status or we run out of polls."""
    for _ in range(max_polls):
        result = _scoped_get_background_task_result(task_id, run_id=run_id)
        if result is None:
            return None
        if _is_subagent_terminal(result):
            return result
        await asyncio.sleep(5)
    return None


async def _deferred_cleanup_subagent_task(task_id: str, trace_id: str, max_polls: int, run_id: str | None = None) -> None:
    """Keep polling a cancelled subagent until it can be safely removed."""
    cleanup_poll_count = 0
    while True:
        result = _scoped_get_background_task_result(task_id, run_id=run_id)
        if result is None:
            return
        if _is_subagent_terminal(result):
            _scoped_cleanup_background_task(task_id, run_id=run_id)
            return
        if cleanup_poll_count >= max_polls:
            logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
            return
        await asyncio.sleep(5)
        cleanup_poll_count += 1


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(task_id: str, trace_id: str, max_polls: int, run_id: str | None = None) -> None:
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(task_id, trace_id, max_polls, run_id=run_id))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id))


def _find_usage_recorder(runtime: Any) -> Any | None:
    """Find a callback handler with ``record_external_llm_usage_records`` in the runtime config.

    LangChain may pass ``config["callbacks"]`` in three different shapes:

    - ``None`` (no callbacks registered): no recorder.
    - A plain ``list[BaseCallbackHandler]``: iterate it directly.
    - A ``BaseCallbackManager`` instance (e.g. ``AsyncCallbackManager`` on async
      tool runs): managers are not iterable, so we unwrap ``.handlers`` first.

    Any other shape (e.g. a single handler object accidentally passed without a
    list wrapper) cannot be iterated safely; treat it as "no recorder" rather
    than raise.
    """
    for cb in _iter_runtime_callbacks(runtime):
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _find_task_event_recorder(runtime: Any) -> Any | None:
    context = getattr(runtime, "context", None) if runtime is not None else None
    if isinstance(context, dict):
        journal = context.get("__run_journal")
        if hasattr(journal, "record_task_event"):
            return journal
    for cb in _iter_runtime_callbacks(runtime):
        if hasattr(cb, "record_task_event"):
            return cb
    return None


def _runtime_round_id(runtime: Any) -> str | None:
    context = getattr(runtime, "context", None) if runtime is not None else None
    if not isinstance(context, dict):
        return None
    round_id = context.get("round_id")
    if isinstance(round_id, str) and round_id:
        return round_id
    round_context = context.get("round_context")
    if isinstance(round_context, dict):
        round_id = round_context.get("round_id")
        if isinstance(round_id, str) and round_id:
            return round_id
    return None


def _emit_task_event(writer: Any, runtime: Any, event: dict[str, Any]) -> None:
    if "round_id" not in event:
        round_id = _runtime_round_id(runtime)
        if round_id:
            event = {**event, "round_id": round_id}
    event = _sanitize_task_event(event)
    recorder = _find_task_event_recorder(runtime)
    if recorder is not None:
        try:
            recorder.record_task_event(event)
        except Exception:
            logger.warning("Failed to persist task event %s", event.get("type"), exc_info=True)
    writer(event)


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """Summarize token usage records into a compact dict for SSE events."""
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """Report subagent token usage to the parent RunJournal, if available.

    Each subagent task must be reported only once (guarded by usage_reported).
    """
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _resolve_inheritable_parent_model(parent_model: str | None, app_config: "AppConfig | None") -> str | None:
    if parent_model is None or app_config is None:
        return parent_model

    get_model_config = getattr(app_config, "get_model_config", None)
    if not callable(get_model_config):
        return parent_model

    model_config = get_model_config(parent_model)
    if model_config is not None and not model_config.subagents_inherit:
        return None
    return parent_model


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """Return the effective subagent skill allowlist under the parent policy."""
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> ToolMessage:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
    """
    runtime_app_config = _get_runtime_app_config(runtime)
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()

    # Get subagent configuration
    config = get_subagent_config(subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return _terminal_task_message(
            f"Error: Unknown subagent type '{subagent_type}'. Available: {available}",
            tool_call_id=tool_call_id,
            status="failed",
        )
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return _terminal_task_message(
                f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}",
                tool_call_id=tool_call_id,
                status="failed",
            )

    # Build config overrides
    overrides: dict = {}

    # Skills are loaded by SubagentExecutor per-session (aligned with Codex's pattern:
    # each subagent loads its own skills based on config, injected as conversation items).
    # No longer appended to system_prompt here.

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    parent_reasoning_effort = None
    trace_id = None
    user_id = None
    metadata: dict = {}

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")
        parent_reasoning_effort = metadata.get("reasoning_effort")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # Get user_id for tracing (uses standard resolution order)
    user_id = resolve_runtime_user_id(runtime)

    # Propagate the authenticated runtime context so delegated tool calls are
    # evaluated by GuardrailMiddleware with the same identity/attribution as
    # the lead agent. Sourced from the server-side context written by
    # inject_authenticated_user_context (and run_id by the run worker); stays
    # None when absent (e.g. internal-auth runs) so guardrail behavior is
    # unchanged. Without this, role-aware policy silently mis-attributes any
    # tool call delegated to a subagent (user_role=None).
    parent_context = runtime.context if runtime is not None else None
    parent_context = parent_context if isinstance(parent_context, dict) else {}
    user_role = parent_context.get("user_role")
    oauth_provider = parent_context.get("oauth_provider")
    oauth_id = parent_context.get("oauth_id")
    run_id = parent_context.get("run_id")

    parent_available_skills = metadata.get("available_skills")
    if config.skills is not None and "subagent_available_skills" in metadata:
        parent_available_skills = metadata.get("subagent_available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Inherit delegated tool_groups when provided. Coordinator-only agents can
    # restrict their own direct tools while still releasing sub-AIs to the
    # normal configured tools.
    parent_tool_groups = metadata.get("subagent_tool_groups") if "subagent_tool_groups" in metadata else metadata.get("tool_groups")
    resolved_app_config = runtime_app_config
    inheritable_parent_model = _resolve_inheritable_parent_model(parent_model, resolved_app_config)
    if config.model == "inherit" and inheritable_parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
        inheritable_parent_model = _resolve_inheritable_parent_model(parent_model, resolved_app_config)
    effective_model = resolve_subagent_model_name(config, inheritable_parent_model, app_config=resolved_app_config)

    # Subagents should not have subagent tools enabled (prevent recursive nesting)
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    # Create executor
    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": inheritable_parent_model,
        "parent_reasoning_effort": parent_reasoning_effort,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "trace_id": trace_id,
        "user_id": user_id,
        "user_role": user_role,
        "oauth_provider": oauth_provider,
        "oauth_id": oauth_id,
        "run_id": run_id,
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = executor.execute_async(prompt, task_id=tool_call_id)
    started_at = _utc_now_iso()
    started_monotonic = time.monotonic()
    await _record_subagent_handoff_async(
        thread_id=thread_id,
        run_id=run_id,
        task_id=task_id,
        trace_id=trace_id,
        user_id=user_id,
        subagent_type=subagent_type,
        description=description,
        prompt=prompt,
        status="started",
    )

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    # Polling timeout: execution timeout + 60s buffer, checked every 5s
    max_poll_count = (config.timeout_seconds + 60) // 5
    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    # Send Task Started message'
    _emit_task_event(
        writer,
        runtime,
        {
            **_task_event_base("task_started", task_id, thread_id=thread_id, run_id=run_id, started_at=started_at),
            "description": description,
            "subagent_type": subagent_type,
            "status": "in_progress",
            "summary": _redacted_preview(description, fallback="Task started"),
        },
    )

    try:
        while True:
            result = _scoped_get_background_task_result(task_id, run_id=run_id)

            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                error = "Task disappeared from background tasks"
                action_result = task_action_result_from_terminal_event(
                    task_id=task_id,
                    status="failed",
                    description=description,
                    error=error,
                    terminal_reason="failed",
                )
                _emit_task_event(
                    writer,
                    runtime,
                    {
                        **_terminal_task_event_base(
                            "task_failed",
                            task_id,
                            thread_id=thread_id,
                            run_id=run_id,
                            started_at=started_at,
                            started_monotonic=started_monotonic,
                        ),
                        "status": "failed",
                        "summary": "Task failed",
                        "error_preview": error,
                        "artifact_refs": _artifact_refs(action_result),
                        "action_result": _compact_action_result_event(action_result),
                    },
                )
                _scoped_cleanup_background_task(task_id, run_id=run_id)
                return _terminal_task_message(
                    f"Error: Task {task_id} disappeared from background tasks",
                    tool_call_id=tool_call_id,
                    status="failed",
                )

            # Log status changes for debugging
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # Check for new AI messages and send task_running events
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    _emit_task_event(
                        writer,
                        runtime,
                        {
                            **_task_event_base("task_running", task_id, thread_id=thread_id, run_id=run_id, started_at=started_at),
                            "status": "in_progress",
                            "summary": f"Subagent message {i + 1}/{current_message_count}",
                            "message_index": i + 1,  # 1-based index for display
                            "total_messages": current_message_count,
                        },
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # Check if task completed, failed, or timed out
            usage = _summarize_usage(getattr(result, "token_usage_records", None))
            if result.status == SubagentStatus.COMPLETED:
                _cache_subagent_usage(tool_call_id, usage, run_id=run_id, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                observed_evidence_refs = _runtime_observed_evidence_refs(result)
                action_result = task_action_result_from_terminal_event(
                    task_id=task_id,
                    status="completed",
                    description=description,
                    result=result.result,
                    observed_evidence_refs=observed_evidence_refs,
                )
                await _record_subagent_handoff_async(
                    thread_id=thread_id,
                    run_id=run_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    status="completed",
                    result=result.result,
                    usage=usage,
                    action_result=task_action_result_event(action_result)["action_result"],
                )
                _emit_task_event(
                    writer,
                    runtime,
                    {
                        **_terminal_task_event_base(
                            "task_completed",
                            task_id,
                            thread_id=thread_id,
                            run_id=run_id,
                            started_at=started_at,
                            started_monotonic=started_monotonic,
                        ),
                        "status": "completed",
                        "summary": "Task completed",
                        "result_preview": _redacted_preview(result.result, fallback="Task completed"),
                        "usage": usage,
                        "artifact_refs": _artifact_refs(action_result),
                        "action_result": _compact_action_result_event(action_result),
                    },
                )
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                _scoped_cleanup_background_task(task_id, run_id=run_id)
                result_text = _redacted_tool_text(result.result)
                return _terminal_task_message(
                    _format_task_success(result_text, observed_evidence_refs=observed_evidence_refs),
                    tool_call_id=tool_call_id,
                    status="completed",
                )
            elif result.status == SubagentStatus.FAILED:
                _cache_subagent_usage(tool_call_id, usage, run_id=run_id, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                action_result = task_action_result_from_terminal_event(task_id=task_id, status="failed", description=description, error=result.error, terminal_reason="failed")
                await _record_subagent_handoff_async(
                    thread_id=thread_id,
                    run_id=run_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    status="failed",
                    error=result.error,
                    usage=usage,
                    action_result=task_action_result_event(action_result)["action_result"],
                )
                _emit_task_event(
                    writer,
                    runtime,
                    {
                        **_terminal_task_event_base(
                            "task_failed",
                            task_id,
                            thread_id=thread_id,
                            run_id=run_id,
                            started_at=started_at,
                            started_monotonic=started_monotonic,
                        ),
                        "status": "failed",
                        "summary": "Task failed",
                        "error_preview": _redacted_preview(result.error, fallback="Task failed"),
                        "usage": usage,
                        "artifact_refs": _artifact_refs(action_result),
                        "action_result": _compact_action_result_event(action_result),
                    },
                )
                error_text = _redacted_tool_text(result.error)
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {error_text}")
                _scoped_cleanup_background_task(task_id, run_id=run_id)
                return _terminal_task_message(
                    f"Task failed. Error: {error_text}",
                    tool_call_id=tool_call_id,
                    status="failed",
                )
            elif result.status == SubagentStatus.CANCELLED:
                _cache_subagent_usage(tool_call_id, usage, run_id=run_id, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                action_result = task_action_result_from_terminal_event(
                    task_id=task_id,
                    status="cancelled",
                    description=description,
                    error=result.error,
                    terminal_reason="user_cancelled",
                )
                await _record_subagent_handoff_async(
                    thread_id=thread_id,
                    run_id=run_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    status="cancelled",
                    error=result.error,
                    usage=usage,
                    action_result=task_action_result_event(action_result)["action_result"],
                )
                _emit_task_event(
                    writer,
                    runtime,
                    {
                        **_terminal_task_event_base(
                            "task_cancelled",
                            task_id,
                            thread_id=thread_id,
                            run_id=run_id,
                            started_at=started_at,
                            started_monotonic=started_monotonic,
                        ),
                        "status": "cancelled",
                        "summary": "Task cancelled",
                        "error_preview": _redacted_preview(result.error, fallback="Task cancelled"),
                        "usage": usage,
                        "artifact_refs": _artifact_refs(action_result),
                        "action_result": _compact_action_result_event(action_result),
                    },
                )
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {_redacted_tool_text(result.error)}")
                _scoped_cleanup_background_task(task_id, run_id=run_id)
                return _terminal_task_message(
                    "Task cancelled by user.",
                    tool_call_id=tool_call_id,
                    status="cancelled",
                )
            elif result.status == SubagentStatus.TIMED_OUT:
                _cache_subagent_usage(tool_call_id, usage, run_id=run_id, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                action_result = task_action_result_from_terminal_event(
                    task_id=task_id,
                    status="timed_out",
                    description=description,
                    error=result.error,
                    terminal_reason="timed_out",
                )
                await _record_subagent_handoff_async(
                    thread_id=thread_id,
                    run_id=run_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    status="timed_out",
                    error=result.error,
                    usage=usage,
                    action_result=task_action_result_event(action_result)["action_result"],
                )
                _emit_task_event(
                    writer,
                    runtime,
                    {
                        **_terminal_task_event_base(
                            "task_timed_out",
                            task_id,
                            thread_id=thread_id,
                            run_id=run_id,
                            started_at=started_at,
                            started_monotonic=started_monotonic,
                        ),
                        "status": "timed_out",
                        "summary": "Task timed out",
                        "error_preview": _redacted_preview(result.error, fallback="Task timed out"),
                        "usage": usage,
                        "artifact_refs": _artifact_refs(action_result),
                        "action_result": _compact_action_result_event(action_result),
                    },
                )
                error_text = _redacted_tool_text(result.error)
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {error_text}")
                _scoped_cleanup_background_task(task_id, run_id=run_id)
                return _terminal_task_message(
                    f"Task timed out. Error: {error_text}",
                    tool_call_id=tool_call_id,
                    status="timed_out",
                )

            # Still running, wait before next poll
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                error = f"Polling timed out after {timeout_minutes} minutes; status={result.status.value}"
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, run_id=run_id, enabled=cache_token_usage)
                action_result = task_action_result_from_terminal_event(
                    task_id=task_id,
                    status="timed_out",
                    description=description,
                    error=error,
                    terminal_reason="timed_out",
                )
                await _record_subagent_handoff_async(
                    thread_id=thread_id,
                    run_id=run_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    status="polling_timed_out",
                    error=error,
                    usage=usage,
                    action_result=task_action_result_event(action_result)["action_result"],
                )
                _emit_task_event(
                    writer,
                    runtime,
                    {
                        **_terminal_task_event_base(
                            "task_timed_out",
                            task_id,
                            thread_id=thread_id,
                            run_id=run_id,
                            started_at=started_at,
                            started_monotonic=started_monotonic,
                        ),
                        "status": "timed_out",
                        "summary": "Task polling timed out",
                        "error_preview": _redacted_preview(error, fallback="Task polling timed out"),
                        "usage": usage,
                        "artifact_refs": _artifact_refs(action_result),
                        "action_result": _compact_action_result_event(action_result),
                    },
                )
                # The task may still be running in the background. Signal cooperative
                # cancellation and schedule deferred cleanup to remove the entry from
                # _background_tasks once the background thread reaches a terminal state.
                _scoped_request_cancel_background_task(task_id, run_id=run_id)
                _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count, run_id=run_id)
                return _terminal_task_message(
                    f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}",
                    tool_call_id=tool_call_id,
                    status="polling_timed_out",
                )
    except asyncio.CancelledError:
        # Signal the background subagent thread to stop cooperatively.
        _scoped_request_cancel_background_task(task_id, run_id=run_id)

        # Wait (shielded) for the subagent to reach a terminal state so the
        # final token usage snapshot is reported to the parent RunJournal
        # before the parent worker persists get_completion_data().
        terminal_result = None
        try:
            terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count, run_id=run_id))
        except asyncio.CancelledError:
            pass

        # Report whatever the subagent collected (even if we timed out).
        final_result = terminal_result or _scoped_get_background_task_result(task_id, run_id=run_id)
        usage = _summarize_usage(getattr(final_result, "token_usage_records", None)) if final_result is not None else None
        if final_result is not None:
            _report_subagent_usage(runtime, final_result)
        action_result = task_action_result_from_terminal_event(
            task_id=task_id,
            status="cancelled",
            description=description,
            error=getattr(final_result, "error", None) if final_result is not None else "Parent run cancelled",
            terminal_reason="user_cancelled",
        )
        await _record_subagent_handoff_async(
            thread_id=thread_id,
            run_id=run_id,
            task_id=task_id,
            trace_id=trace_id,
            user_id=user_id,
            subagent_type=subagent_type,
            description=description,
            prompt=prompt,
            status="cancelled",
            error=getattr(final_result, "error", None) if final_result is not None else "Parent run cancelled",
            usage=usage,
            action_result=task_action_result_event(action_result)["action_result"],
        )
        _emit_task_event(
            writer,
            runtime,
            {
                **_terminal_task_event_base(
                    "task_cancelled",
                    task_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                ),
                "status": "cancelled",
                "summary": "Task cancelled",
                "error_preview": _redacted_preview(getattr(final_result, "error", None), fallback="Task cancelled"),
                "usage": usage,
                "artifact_refs": _artifact_refs(action_result),
                "action_result": _compact_action_result_event(action_result),
            },
        )
        if final_result is not None and _is_subagent_terminal(final_result):
            _scoped_cleanup_background_task(task_id, run_id=run_id)
        else:
            _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count, run_id=run_id)
        _subagent_usage_cache.pop(_subagent_usage_cache_key(tool_call_id, run_id), None)
        raise
    except Exception:
        _subagent_usage_cache.pop(_subagent_usage_cache_key(tool_call_id, run_id), None)
        raise
