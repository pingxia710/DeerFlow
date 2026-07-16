"""Task tool that delegates one-shot work to the installed Codex CLI."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer

from deerflow.command_room.ai_workspace import (
    CommandRoomContainer,
    CommandRoomContainerTask,
    ContainerArtifact,
    container_artifact_is_ai_authored,
    ensure_command_room_ai_workspace,
    format_ai_workspace_for_model,
    format_container_task_for_model,
    prepare_command_room_container_task,
    record_container_task_completion,
    record_container_task_terminal,
    validate_work_package_id,
)
from deerflow.command_room.task_action_result import task_action_result_event, task_action_result_from_terminal_event
from deerflow.config.paths import ensure_directory_no_symlinks
from deerflow.config.subagents_config import get_subagents_app_config
from deerflow.runtime.background_tasks import CommandRoomBackgroundJob, CommandRoomBackgroundOutcome
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.security import is_unrestricted_host_access_allowed
from deerflow.subagents.audit import record_subagent_handoff
from deerflow.subagents.codex_cli import CodexSandboxMode, run_codex_cli_task
from deerflow.subagents.registry import get_subagent_config
from deerflow.subagents.status_contract import SubagentStatusValue, make_subagent_additional_kwargs
from deerflow.tools.types import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig


logger = logging.getLogger(__name__)
_TASK_EVENT_SCHEMA_VERSION = "deerflow.task-event/v1"
_EVENT_PREVIEW_MAX_CHARS = 240
_REVIEW_TASK_TIMEOUT_SECONDS = 900
_BACKGROUND_DISPATCHER_CONTEXT_KEY = "__command_room_background_dispatcher"
_BACKGROUND_WAKE_CONTEXT_KEYS = frozenset(
    {
        "model_name",
        "mode",
        "thinking_enabled",
        "reasoning_effort",
        "reasoning_summary",
        "text_verbosity",
        "is_plan_mode",
        "subagent_enabled",
        "max_concurrent_subagents",
        "agent_name",
    }
)
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
    "work_package_id",
}
_SECRET_LIKE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|ak(?:ia|as)[a-z0-9]{12,}|"
    r"(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*['\"]?[^'\"\s]+)"
)

# The token-usage middleware imports these functions dynamically. Codex CLI's
# JSON stream currently has no stable usage contract, so direct CLI tasks do not
# populate this cache.
_subagent_usage_cache: dict[tuple[str | None, str], dict[str, int]] = {}


async def _record_subagent_handoff_async(**kwargs: Any) -> None:
    await asyncio.to_thread(record_subagent_handoff, **kwargs)


def _subagent_usage_cache_key(tool_call_id: str, run_id: str | None = None) -> tuple[str | None, str]:
    return (str(run_id) if run_id else None, tool_call_id)


def pop_cached_subagent_usage(tool_call_id: str, *, run_id: str | None = None) -> dict | None:
    return _subagent_usage_cache.pop(_subagent_usage_cache_key(tool_call_id, run_id), None)


def clear_cached_subagent_usage_for_run(run_id: str) -> None:
    normalized_run_id = str(run_id)
    for key in [key for key in _subagent_usage_cache if key[0] == normalized_run_id]:
        _subagent_usage_cache.pop(key, None)


def _iter_runtime_callbacks(runtime: Any) -> list[Any]:
    if runtime is None:
        return []
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return []
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    return callbacks if isinstance(callbacks, list) else []


def _find_usage_recorder(runtime: Any) -> Any | None:
    for callback in _iter_runtime_callbacks(runtime):
        if hasattr(callback, "record_external_llm_usage_records"):
            return callback
    return None


def _find_task_event_recorder(runtime: Any) -> Any | None:
    context = getattr(runtime, "context", None) if runtime is not None else None
    if isinstance(context, dict):
        journal = context.get("__run_journal")
        if hasattr(journal, "record_task_event"):
            return journal
    for callback in _iter_runtime_callbacks(runtime):
        if hasattr(callback, "record_task_event"):
            return callback
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


def _redacted_tool_text(value: Any) -> str:
    return _SECRET_LIKE_RE.sub("[redacted]", "" if value is None else str(value))


def _redacted_preview(value: Any, *, fallback: str = "") -> str:
    text = " ".join(_redacted_tool_text(fallback if value is None else value).split())
    return f"{text[:_EVENT_PREVIEW_MAX_CHARS]}..." if len(text) > _EVENT_PREVIEW_MAX_CHARS else text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_task_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_task_event_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize_task_event_value(item) for item in value]
    if isinstance(value, str):
        return _redacted_preview(value)
    return value if value is None or isinstance(value, bool | int | float) else _redacted_preview(value)


def _sanitize_task_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_task_event_value(event)
    if not isinstance(sanitized, dict):
        return {}
    for key in _TASK_EVENT_IDENTITY_KEYS:
        if key in event and (event[key] is None or isinstance(event[key], bool | int | float | str)):
            sanitized[key] = event[key]
    return sanitized


def _compact_action_result_event(action_result: Any) -> dict[str, Any]:
    payload = task_action_result_event(action_result)["action_result"]
    compact = {key: payload[key] for key in _SAFE_ACTION_RESULT_EVENT_KEYS if key in payload}
    for key in ("summary", "error"):
        if (value := payload.get(key)) is not None:
            text = str(value)
            compact[key] = _redacted_preview(text)
            compact[f"{key}_sha256"] = _sha256_text(text)
            compact[f"{key}_chars"] = len(text)
    return compact


def _artifact_refs(action_result: Any) -> list[str]:
    refs: list[str] = []
    if isinstance((output_ref := getattr(action_result, "output_ref", None)), str) and output_ref:
        refs.append(output_ref)
    evidence_refs = getattr(action_result, "evidence_refs", None)
    if isinstance(evidence_refs, list):
        refs.extend(ref for ref in evidence_refs if isinstance(ref, str) and ref)
    return refs


def _terminal_task_message(
    content: str,
    *,
    tool_call_id: str,
    status: SubagentStatusValue,
    round_id: str | None,
    container_task: CommandRoomContainerTask | None = None,
    container_artifact_written: bool | None = None,
) -> ToolMessage:
    additional_kwargs = make_subagent_additional_kwargs(status)
    if round_id:
        additional_kwargs["round_id"] = round_id
    if container_task is not None:
        additional_kwargs["command_room_container"] = container_task.container
        if container_task.work_package_id is not None:
            additional_kwargs["work_package_id"] = container_task.work_package_id
        additional_kwargs["container_artifact_path"] = str(container_task.output_path)
        additional_kwargs["container_artifact_kind"] = container_task.artifact_kind
        if container_task.delivery_cycle_index is not None:
            additional_kwargs["delivery_cycle_index"] = container_task.delivery_cycle_index
        if container_artifact_written is not None:
            additional_kwargs["container_artifact_written"] = container_artifact_written
    return ToolMessage(
        content=content,
        name="task",
        tool_call_id=tool_call_id,
        additional_kwargs=additional_kwargs,
    )


def _record_persisted_terminal_task_message(
    recorder: Any | None,
    *,
    content: str,
    task_id: str,
    status: SubagentStatusValue,
    runtime: Any,
    container_task: CommandRoomContainerTask | None,
    container_artifact_written: bool | None,
) -> None:
    record_tool_message = getattr(recorder, "record_tool_message", None)
    if callable(record_tool_message):
        record_tool_message(
            _terminal_task_message(
                content,
                tool_call_id=task_id,
                status=status,
                round_id=_runtime_round_id(runtime),
                container_task=container_task,
                container_artifact_written=container_artifact_written,
            )
        )


def _background_task_message(
    *,
    tool_call_id: str,
    round_id: str | None,
    task_fields: Mapping[str, Any],
) -> ToolMessage:
    additional_kwargs: dict[str, Any] = {
        "background_task": True,
        "background_task_id": tool_call_id,
        **task_fields,
    }
    if round_id:
        additional_kwargs["round_id"] = round_id
    return ToolMessage(
        content=(f"Task {tool_call_id} was accepted for background execution. Do not claim or infer its result. End this turn after a short status update; the completed child result will automatically wake the Command Room."),
        name="task",
        tool_call_id=tool_call_id,
        additional_kwargs=additional_kwargs,
    )


def _command_room_background_dispatcher(context: Mapping[str, Any]) -> Any | None:
    dispatcher = context.get(_BACKGROUND_DISPATCHER_CONTEXT_KEY)
    return dispatcher if callable(getattr(dispatcher, "dispatch", None)) else None


def _background_wake_context(context: Mapping[str, Any]) -> dict[str, Any]:
    wake_context = {key: context[key] for key in _BACKGROUND_WAKE_CONTEXT_KEYS if key in context}
    wake_context["agent_name"] = "command-room"
    wake_context["subagent_enabled"] = True
    return wake_context


def _fork_background_runtime(runtime: Any) -> tuple[Any, Any | None]:
    context = getattr(runtime, "context", None)
    background_context = dict(context) if isinstance(context, Mapping) else {}
    background_context.pop(_BACKGROUND_DISPATCHER_CONTEXT_KEY, None)
    recorder = _find_task_event_recorder(runtime)
    fork = getattr(recorder, "fork_for_background_task", None)
    background_recorder = fork() if callable(fork) else recorder
    if background_recorder is not None:
        background_context["__run_journal"] = background_recorder

    config = getattr(runtime, "config", None)
    background_config = dict(config) if isinstance(config, Mapping) else {}
    return SimpleNamespace(context=background_context, config=background_config), background_recorder


def _container_task_event_fields(
    container_task: CommandRoomContainerTask | None,
    *,
    container: CommandRoomContainer | None = None,
    delivery_cycle_index: int | None = None,
    work_package_id: str | None = None,
    container_artifact_written: bool | None = None,
) -> dict[str, Any]:
    """Expose factual AI-AI handoff metadata without interpreting AI prose."""

    fields: dict[str, Any] = {}
    if container_task is not None:
        fields.update(
            command_room_container=container_task.container,
            container_artifact_path=str(container_task.output_path),
            container_artifact_kind=container_task.artifact_kind,
        )
    elif container is not None:
        fields["command_room_container"] = container
    package_id = container_task.work_package_id if container_task is not None else work_package_id
    cycle_index = container_task.delivery_cycle_index if container_task is not None else delivery_cycle_index
    if package_id is not None:
        fields["work_package_id"] = package_id
    if cycle_index is not None:
        fields["delivery_cycle_index"] = cycle_index
    if container_artifact_written is not None:
        fields["container_artifact_written"] = container_artifact_written
    return fields


def _emit_task_event(writer: Any, runtime: Any, event: dict[str, Any]) -> None:
    if "round_id" not in event and (round_id := _runtime_round_id(runtime)):
        event = {**event, "round_id": round_id}
    event = _sanitize_task_event(event)
    recorder = _find_task_event_recorder(runtime)
    if recorder is not None:
        try:
            recorder.record_task_event(event)
        except Exception:
            logger.warning("Failed to persist task event %s", event.get("type"), exc_info=True)
    writer(event)


def _get_runtime_app_config(runtime: Any) -> AppConfig | None:
    context = getattr(runtime, "context", None)
    if isinstance(context, dict) and context.get("app_config") is not None:
        return cast("AppConfig", context["app_config"])
    return None


def _task_timeout_seconds(app_config: AppConfig | None) -> int:
    if app_config is not None:
        subagents = getattr(app_config, "subagents", None)
        timeout_seconds = getattr(subagents, "timeout_seconds", None)
        if isinstance(timeout_seconds, int) and timeout_seconds > 0:
            return timeout_seconds
    return get_subagents_app_config().timeout_seconds


def _task_model_options(app_config: AppConfig | None, subagent_type: str) -> tuple[str | None, str | None]:
    subagents = getattr(app_config, "subagents", None) if app_config is not None else get_subagents_app_config()
    get_model_for = getattr(subagents, "get_model_for", None)
    model = get_model_for(subagent_type) if callable(get_model_for) else None
    if app_config is not None and isinstance(model, str):
        get_model_config = getattr(app_config, "get_model_config", None)
        model_config = get_model_config(model) if callable(get_model_config) else None
        raw_model_id = getattr(model_config, "model", None)
        if isinstance(raw_model_id, str) and raw_model_id:
            model = raw_model_id
    reasoning_effort = getattr(subagents, "reasoning_effort", None)
    return model, reasoning_effort if isinstance(reasoning_effort, str) else None


def _task_sandbox_mode(app_config: AppConfig | None) -> CodexSandboxMode:
    return "danger-full-access" if is_unrestricted_host_access_allowed(app_config) else "workspace-write"


def _task_worker_prompt(
    app_config: AppConfig | None,
    subagent_type: str,
    prompt: str,
    task_paths: dict[str, str],
    container_task: CommandRoomContainerTask | None = None,
    container: CommandRoomContainer | None = None,
) -> str:
    role = get_subagent_config(subagent_type, app_config=app_config)
    role_prompt = getattr(role, "system_prompt", None) or getattr(role, "description", None)
    if not isinstance(role_prompt, str) or not role_prompt.strip():
        role_prompt = f'Work as the professional role "{subagent_type}" selected by the lead AI.'
    sections = [f"# Professional role: {subagent_type}\n\n{role_prompt.strip()}"]
    sections.append(f"# Command Room task\n\n{prompt}")
    sections.append(
        "# Applicable project instructions\n\n"
        "Before working on any target path, locate and read the complete AGENTS.md instruction chain "
        "that applies to that path, including ancestor, project, and nearer subdirectory files. Follow "
        "the nearest applicable rules when instructions conflict. Do not edit an AGENTS.md file unless "
        "this task explicitly authorizes that edit."
    )
    path_lines = [
        f"- Workspace: {task_paths['workspace_path']}",
    ]
    if uploads_path := task_paths.get("uploads_path"):
        path_lines.append(f"- Uploaded files (read): {uploads_path}")
    if outputs_path := task_paths.get("outputs_path"):
        path_lines.append(f"- Output artifacts (write): {outputs_path}")
    path_lines.append("- Any /mnt/user-data paths in the handoff refer to the matching host paths above.")
    sections.append("# DeerFlow task paths\n\n" + "\n".join(path_lines))
    if ai_workspace := format_ai_workspace_for_model(task_paths.get("ai_workspace_path")):
        sections.append("# AI-AI workspace\n\n" + ai_workspace)
    if container_task is not None:
        sections.append("# Required Command Room handoff\n\n" + format_container_task_for_model(container_task))
        if container_task.container == "review":
            sections.append(
                "# Review boundary\n\n"
                "Verify only whether the bounded execution landed as requested. Read the relevant execution note and "
                "inspect the actual changed result. Use the smallest targeted check needed to support that judgment. "
                "Do not implement, repair, refactor, or broaden the review, and do not run a full test suite unless the "
                "Chair explicitly named it as an acceptance check. Record concrete landed facts and any exact gaps, then "
                "stop. Stop as soon as the landing judgment is supported."
            )
    elif container == "review":
        sections.append(
            "# Review boundary\n\n"
            "Verify only whether the bounded execution landed as requested. Inspect the actual changed result and "
            "use the smallest targeted check needed to support that judgment. Do not implement, repair, refactor, or "
            "broaden the review, and do not run a full test suite unless the Chair explicitly named it as an acceptance "
            "check. Record concrete landed facts and any exact gaps, then stop. Stop as soon as the landing judgment is supported."
        )
    return "\n\n".join(sections)


def _task_paths(thread_data: dict[str, Any]) -> dict[str, str]:
    workspace_path = thread_data.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path:
        raise RuntimeError("No thread workspace is available for the Codex CLI task.")
    paths = {"workspace_path": workspace_path}
    for key in ("uploads_path", "outputs_path"):
        value = thread_data.get(key)
        if isinstance(value, str) and value:
            paths[key] = value
    return paths


def _ensure_task_paths(task_paths: dict[str, str]) -> dict[str, str]:
    try:
        return {key: str(ensure_directory_no_symlinks(value, mode=0o777)) for key, value in task_paths.items()}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Codex CLI task paths could not be prepared: {exc}") from exc


def _add_command_room_ai_workspace(
    task_paths: dict[str, str],
    *,
    agent_name: Any,
    workspace_key: Any,
) -> dict[str, str]:
    if agent_name != "command-room" or not isinstance(workspace_key, str) or not workspace_key:
        return task_paths
    try:
        ai_workspace_path = ensure_command_room_ai_workspace(task_paths["workspace_path"], workspace_key)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Command Room AI-AI workspace could not be prepared: {exc}") from exc
    return {**task_paths, "ai_workspace_path": str(ai_workspace_path)}


async def _record_terminal_task(
    *,
    writer: Any,
    runtime: Any,
    task_id: str,
    thread_id: str | None,
    run_id: str | None,
    trace_id: str,
    user_id: str | None,
    subagent_type: str,
    description: str,
    prompt: str,
    status: str,
    started_at: str,
    started_monotonic: float,
    result: str | None = None,
    error: str | None = None,
    container_task: CommandRoomContainerTask | None = None,
    container_artifact_written: bool | None = None,
) -> None:
    terminal_reason = "user_cancelled" if status == "cancelled" else status
    action_result = task_action_result_from_terminal_event(
        task_id=task_id,
        status=status,
        description=description,
        result=result,
        error=error,
        terminal_reason=terminal_reason,
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
        status=status,
        result=result,
        error=error,
        action_result=task_action_result_event(action_result)["action_result"],
    )
    event_type = {
        "completed": "task_completed",
        "failed": "task_failed",
        "cancelled": "task_cancelled",
        "timed_out": "task_timed_out",
    }[status]
    _emit_task_event(
        writer,
        runtime,
        {
            **_terminal_task_event_base(
                event_type,
                task_id,
                thread_id=thread_id,
                run_id=run_id,
                started_at=started_at,
                started_monotonic=started_monotonic,
            ),
            "status": status,
            "summary": f"Task {status.replace('_', ' ')}",
            "result_preview": _redacted_preview(result, fallback="Task completed") if result is not None else None,
            "error_preview": _redacted_preview(error, fallback=f"Task {status.replace('_', ' ')}") if error is not None else None,
            "artifact_refs": _artifact_refs(action_result),
            "action_result": _compact_action_result_event(action_result),
            **_container_task_event_fields(
                container_task,
                container_artifact_written=container_artifact_written,
            ),
        },
    )


async def _execute_started_task(
    *,
    writer: Any,
    runtime: Any,
    recorder_to_flush: Any | None,
    task_id: str,
    thread_id: str,
    run_id: str,
    trace_id: str,
    user_id: str | None,
    subagent_type: str,
    description: str,
    worker_prompt: str,
    started_at: str,
    started_monotonic: float,
    prepared_paths: dict[str, str],
    timeout_seconds: int,
    model: str | None,
    reasoning_effort: str | None,
    sandbox_mode: CodexSandboxMode,
    container_task: CommandRoomContainerTask | None,
) -> CommandRoomBackgroundOutcome:
    container_artifact_written: bool | None = None
    try:
        result = await run_codex_cli_task(
            worker_prompt,
            workspace_path=prepared_paths["workspace_path"],
            timeout_seconds=timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
            sandbox_mode=sandbox_mode,
            additional_writable_paths=([prepared_paths["outputs_path"]] if "outputs_path" in prepared_paths else []),
        )
    except TimeoutError as exc:
        error = str(exc)
        if container_task is not None:
            container_artifact_written = await asyncio.to_thread(container_artifact_is_ai_authored, container_task)
            await asyncio.to_thread(record_container_task_terminal, container_task, status="timed_out")
        await _record_terminal_task(
            writer=writer,
            runtime=runtime,
            task_id=task_id,
            thread_id=thread_id,
            run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            subagent_type=subagent_type,
            description=description,
            prompt=worker_prompt,
            status="timed_out",
            started_at=started_at,
            started_monotonic=started_monotonic,
            error=error,
            container_task=container_task,
            container_artifact_written=container_artifact_written,
        )
        _record_persisted_terminal_task_message(
            recorder_to_flush,
            content=f"Task timed out. Error: {_redacted_tool_text(error)}",
            task_id=task_id,
            status="timed_out",
            runtime=runtime,
            container_task=container_task,
            container_artifact_written=container_artifact_written,
        )
        return CommandRoomBackgroundOutcome(
            status="timed_out",
            error=error,
            container_artifact_written=container_artifact_written,
        )
    except RuntimeError as exc:
        error = str(exc)
        if container_task is not None:
            container_artifact_written = await asyncio.to_thread(container_artifact_is_ai_authored, container_task)
            await asyncio.to_thread(record_container_task_terminal, container_task, status="failed")
        await _record_terminal_task(
            writer=writer,
            runtime=runtime,
            task_id=task_id,
            thread_id=thread_id,
            run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            subagent_type=subagent_type,
            description=description,
            prompt=worker_prompt,
            status="failed",
            started_at=started_at,
            started_monotonic=started_monotonic,
            error=error,
            container_task=container_task,
            container_artifact_written=container_artifact_written,
        )
        _record_persisted_terminal_task_message(
            recorder_to_flush,
            content=f"Task failed. Error: {_redacted_tool_text(error)}",
            task_id=task_id,
            status="failed",
            runtime=runtime,
            container_task=container_task,
            container_artifact_written=container_artifact_written,
        )
        return CommandRoomBackgroundOutcome(
            status="failed",
            error=error,
            container_artifact_written=container_artifact_written,
        )
    except asyncio.CancelledError:
        error = "Background task cancelled"
        if container_task is not None:
            container_artifact_written = await asyncio.to_thread(container_artifact_is_ai_authored, container_task)
            await asyncio.to_thread(record_container_task_terminal, container_task, status="cancelled")
        await _record_terminal_task(
            writer=writer,
            runtime=runtime,
            task_id=task_id,
            thread_id=thread_id,
            run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            subagent_type=subagent_type,
            description=description,
            prompt=worker_prompt,
            status="cancelled",
            started_at=started_at,
            started_monotonic=started_monotonic,
            error=error,
            container_task=container_task,
            container_artifact_written=container_artifact_written,
        )
        _record_persisted_terminal_task_message(
            recorder_to_flush,
            content=f"Task cancelled by user. Error: {_redacted_tool_text(error)}",
            task_id=task_id,
            status="cancelled",
            runtime=runtime,
            container_task=container_task,
            container_artifact_written=container_artifact_written,
        )
        raise
    finally:
        flush = getattr(recorder_to_flush, "flush", None)
        if callable(flush):
            await flush()

    if container_task is not None:
        container_artifact_written = await asyncio.to_thread(record_container_task_completion, container_task)

    await _record_terminal_task(
        writer=writer,
        runtime=runtime,
        task_id=task_id,
        thread_id=thread_id,
        run_id=run_id,
        trace_id=trace_id,
        user_id=user_id,
        subagent_type=subagent_type,
        description=description,
        prompt=worker_prompt,
        status="completed",
        started_at=started_at,
        started_monotonic=started_monotonic,
        result=result,
        container_task=container_task,
        container_artifact_written=container_artifact_written,
    )
    _record_persisted_terminal_task_message(
        recorder_to_flush,
        content=result,
        task_id=task_id,
        status="completed",
        runtime=runtime,
        container_task=container_task,
        container_artifact_written=container_artifact_written,
    )
    flush = getattr(recorder_to_flush, "flush", None)
    if callable(flush):
        await flush()
    return CommandRoomBackgroundOutcome(
        status="completed",
        result=result,
        container_artifact_written=container_artifact_written,
    )


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    container: CommandRoomContainer | None = None,
    container_artifact: ContainerArtifact | None = None,
    delivery_cycle_index: int | None = None,
    work_package_id: str | None = None,
) -> ToolMessage:
    """Delegate one natural-language task to the installed Codex CLI.

    DeerFlow combines the selected developer-authored professional role context
    with the lead AI's task prompt and records factual lifecycle events. Ordinary
    agents wait and receive the complete final answer unchanged. Command Room
    receives an admission receipt immediately; the Gateway runs that child in
    the background and starts a fresh sequential Chair run with the complete
    result. Codex CLI owns planning, tool use, and task completion.

    Command Room may call this tool with only description, prompt, and role.
    Container, cycle, package, and artifact fields are optional factual labels
    for display and optional Markdown routing. The tool validates their local
    shape and prevents concurrent writes to one requested artifact; it never
    uses them to authorize, block, sequence, or choose a task.

    Args:
        description: A short task label for the user interface and audit log.
        prompt: Complete instructions for the Codex CLI worker.
        subagent_type: The professional role label selected by the lead AI.
        container: Optional factual label for display and artifact routing.
        container_artifact: Optional request for a fixed Command Room Markdown artifact.
        delivery_cycle_index: Optional factual cycle label.
        work_package_id: Optional namespace label and artifact path scope.
    """
    state = getattr(runtime, "state", None)
    state = state if isinstance(state, dict) else {}
    thread_data = state.get("thread_data")
    thread_data = thread_data if isinstance(thread_data, dict) else {}
    context = getattr(runtime, "context", None)
    context = context if isinstance(context, dict) else {}
    config = getattr(runtime, "config", None)
    config = config if isinstance(config, dict) else {}
    metadata = config.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    thread_id = context.get("thread_id") or config.get("configurable", {}).get("thread_id")
    run_id = context.get("run_id")
    trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]
    user_id = resolve_runtime_user_id(runtime)
    task_id = tool_call_id
    round_id = _runtime_round_id(runtime)
    started_at = _utc_now_iso()
    started_monotonic = time.monotonic()
    writer = get_stream_writer()
    runtime_app_config = _get_runtime_app_config(runtime)
    timeout_seconds = _task_timeout_seconds(runtime_app_config)
    model, reasoning_effort = _task_model_options(runtime_app_config, subagent_type)
    sandbox_mode = _task_sandbox_mode(runtime_app_config)
    worker_prompt = prompt
    is_command_room = context.get("agent_name") == "command-room"
    if is_command_room and container == "review":
        timeout_seconds = min(timeout_seconds, _REVIEW_TASK_TIMEOUT_SECONDS)
    background_dispatcher = _command_room_background_dispatcher(context) if is_command_room else None
    container_task: CommandRoomContainerTask | None = None
    task_fields: dict[str, Any] = {}

    try:
        prepared_paths = _ensure_task_paths(_task_paths(thread_data))
        prepared_paths = _add_command_room_ai_workspace(
            prepared_paths,
            agent_name=context.get("agent_name"),
            workspace_key=thread_id,
        )
        if is_command_room:
            ai_workspace_path = prepared_paths.get("ai_workspace_path")
            if not ai_workspace_path:
                raise RuntimeError("Command Room task requires an active run workspace.")
            try:
                package_id = validate_work_package_id(work_package_id)
                if container_artifact is not None and container is None:
                    raise ValueError("container_artifact requires a container label for artifact routing.")
                if delivery_cycle_index is not None and (isinstance(delivery_cycle_index, bool) or not isinstance(delivery_cycle_index, int) or delivery_cycle_index < 1):
                    raise ValueError("delivery_cycle_index must be a positive integer when provided.")
                prepare_artifact = container_artifact is not None or (container in {"execution", "review", "project-steward"} and delivery_cycle_index is not None) or container in {"debt-curation", "learning-curation"}
                if prepare_artifact:
                    container_task = prepare_command_room_container_task(
                        ai_workspace_path,
                        container=container,
                        task_id=task_id,
                        container_artifact=container_artifact,
                        delivery_cycle_index=delivery_cycle_index,
                        work_package_id=package_id,
                    )
                task_fields = _container_task_event_fields(
                    container_task,
                    container=container,
                    delivery_cycle_index=delivery_cycle_index,
                    work_package_id=package_id,
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        worker_prompt = _task_worker_prompt(
            runtime_app_config,
            subagent_type,
            prompt,
            prepared_paths,
            container_task=container_task,
            container=container,
        )
        await _record_subagent_handoff_async(
            thread_id=thread_id,
            run_id=run_id,
            task_id=task_id,
            trace_id=trace_id,
            user_id=user_id,
            subagent_type=subagent_type,
            description=description,
            prompt=worker_prompt,
            status="started",
        )
        _emit_task_event(
            writer,
            runtime,
            {
                **_task_event_base(
                    "task_started",
                    task_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    started_at=started_at,
                ),
                "description": description,
                "subagent_type": subagent_type,
                "status": "in_progress",
                "background_task": background_dispatcher is not None,
                "summary": _redacted_preview(description, fallback="Task started"),
                **task_fields,
            },
        )
    except RuntimeError as exc:
        error = str(exc)
        if container_task is not None:
            await asyncio.to_thread(record_container_task_terminal, container_task, status="failed")
        await _record_terminal_task(
            writer=writer,
            runtime=runtime,
            task_id=task_id,
            thread_id=thread_id,
            run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            subagent_type=subagent_type,
            description=description,
            prompt=worker_prompt,
            status="failed",
            started_at=started_at,
            started_monotonic=started_monotonic,
            error=error,
            container_task=container_task,
            container_artifact_written=None,
        )
        return _terminal_task_message(
            f"Task failed. Error: {_redacted_tool_text(error)}",
            tool_call_id=tool_call_id,
            status="failed",
            round_id=round_id,
            container_task=container_task,
            container_artifact_written=None,
        )

    if background_dispatcher is not None:
        if not isinstance(thread_id, str) or not thread_id or not isinstance(run_id, str) or not run_id:
            raise RuntimeError("Command Room background dispatch requires complete thread and run identity.")
        background_runtime, background_recorder = _fork_background_runtime(runtime)

        async def execute_background() -> CommandRoomBackgroundOutcome:
            return await _execute_started_task(
                writer=lambda _event: None,
                runtime=background_runtime,
                recorder_to_flush=background_recorder,
                task_id=task_id,
                thread_id=thread_id,
                run_id=run_id,
                trace_id=trace_id,
                user_id=user_id,
                subagent_type=subagent_type,
                description=description,
                worker_prompt=worker_prompt,
                started_at=started_at,
                started_monotonic=started_monotonic,
                prepared_paths=prepared_paths,
                timeout_seconds=timeout_seconds,
                model=model,
                reasoning_effort=reasoning_effort,
                sandbox_mode=sandbox_mode,
                container_task=container_task,
            )

        job = CommandRoomBackgroundJob(
            thread_id=thread_id,
            source_run_id=run_id,
            task_id=task_id,
            description=description,
            subagent_type=subagent_type,
            execute=execute_background,
            wake_context=_background_wake_context(context),
            command_room_container=task_fields.get("command_room_container"),
            container_artifact_path=task_fields.get("container_artifact_path"),
            delivery_cycle_index=task_fields.get("delivery_cycle_index"),
            work_package_id=task_fields.get("work_package_id"),
        )
        try:
            await background_dispatcher.dispatch(job)
        except Exception as exc:
            error = f"Command Room background task could not be scheduled: {exc}"
            await asyncio.to_thread(record_container_task_terminal, container_task, status="failed")
            await _record_terminal_task(
                writer=writer,
                runtime=runtime,
                task_id=task_id,
                thread_id=thread_id,
                run_id=run_id,
                trace_id=trace_id,
                user_id=user_id,
                subagent_type=subagent_type,
                description=description,
                prompt=worker_prompt,
                status="failed",
                started_at=started_at,
                started_monotonic=started_monotonic,
                error=error,
                container_task=container_task,
                container_artifact_written=None,
            )
            return _terminal_task_message(
                f"Task failed. Error: {_redacted_tool_text(error)}",
                tool_call_id=tool_call_id,
                status="failed",
                round_id=round_id,
                container_task=container_task,
                container_artifact_written=None,
            )
        return _background_task_message(tool_call_id=tool_call_id, round_id=round_id, task_fields=task_fields)

    outcome = await _execute_started_task(
        writer=writer,
        runtime=runtime,
        recorder_to_flush=None,
        task_id=task_id,
        thread_id=thread_id,
        run_id=run_id,
        trace_id=trace_id,
        user_id=user_id,
        subagent_type=subagent_type,
        description=description,
        worker_prompt=worker_prompt,
        started_at=started_at,
        started_monotonic=started_monotonic,
        prepared_paths=prepared_paths,
        timeout_seconds=timeout_seconds,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox_mode=sandbox_mode,
        container_task=container_task,
    )
    if outcome.status == "completed":
        content = outcome.result or ""
    elif outcome.status == "timed_out":
        content = f"Task timed out. Error: {_redacted_tool_text(outcome.error or '')}"
    else:
        content = f"Task failed. Error: {_redacted_tool_text(outcome.error or '')}"
    return _terminal_task_message(
        content,
        tool_call_id=tool_call_id,
        status=outcome.status,
        round_id=round_id,
        container_task=container_task,
        container_artifact_written=outcome.container_artifact_written,
    )
