"""Background agent execution.

Runs an agent graph inside an ``asyncio.Task``, publishing events to
a :class:`StreamBridge` as they are produced.

Uses ``graph.astream(stream_mode=[...])`` which gives correct full-state
snapshots for ``values`` mode, proper ``{node: writes}`` for ``updates``,
and ``(chunk, metadata)`` tuples for ``messages`` mode.

Note: ``events`` mode is not supported through the gateway — it requires
``graph.astream_events()`` which cannot simultaneously produce ``values``
snapshots.  The JS open-source LangGraph API server works around this via
internal checkpoint callbacks that are not exposed in the Python public API.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import logging
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint

from deerflow.config.app_config import AppConfig
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import release_runtime_sandbox_lease_async
from deerflow.tracing import inject_langfuse_metadata

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus, run_status_value

logger = logging.getLogger(__name__)

# Valid stream_mode values for LangGraph's graph.astream()
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}
_LEASE_CONTROL_INTERVAL_SECONDS = 2.0
_STREAM_FRAME_CATEGORY = "stream"
_RUN_NO_PROGRESS_TIMEOUT_SECONDS = 30 * 60.0
_RUN_HARD_TIMEOUT_SECONDS = 4 * 60 * 60.0
_STREAM_CLOSE_TIMEOUT_SECONDS = 5.0
_STREAM_RECOVERY_REQUIRED_EVENT = "stream_recovery_required"
_STATUS_COMMIT_FAILED_REASON = "run_status_commit_failed"
_PUBLIC_INTERNAL_ERROR_MESSAGE = "Run failed due to an internal error."
_PUBLIC_INTERNAL_ERROR_NAME = "InternalError"


class RunStreamTimeoutError(TimeoutError):
    """Raised when a live run stops making stream progress."""


class RunNoProgressTimeoutError(RunStreamTimeoutError):
    """Raised when ``agent.astream`` does not yield for the no-progress budget."""


class RunHardTimeoutError(RunStreamTimeoutError):
    """Raised when the whole run exceeds the hard runtime budget."""


def _build_runtime_context(
    thread_id: str,
    run_id: str,
    caller_context: Any | None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """Build the dict that becomes ``ToolRuntime.context`` for the run.

    Always includes ``thread_id`` and ``run_id``. Additional keys from the caller's
    ``config['context']`` (e.g. ``agent_name`` for the bootstrap flow — issue #2677)
    are merged in but never override ``thread_id``/``run_id``. The resolved
    ``AppConfig`` is added by the worker so tools can consume it without ambient
    global lookups.

    langgraph 1.1+ surfaces this as ``runtime.context`` via the parent runtime stored
    under ``config['configurable']['__pregel_runtime']`` — see
    ``langgraph.pregel.main`` where ``parent_runtime.merge(...)`` is invoked.
    """
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
    if isinstance(caller_context, dict):
        for key, value in caller_context.items():
            runtime_ctx.setdefault(key, value)
    if app_config is not None:
        runtime_ctx["app_config"] = app_config
    return runtime_ctx


def _resolve_runtime_agent_name(runtime_context: dict[str, Any], assistant_id: str | None) -> str | None:
    """Resolve a memory agent name without treating the default lead as custom."""
    explicit_name = runtime_context.get("agent_name")
    if isinstance(explicit_name, str) and explicit_name.strip():
        return explicit_name

    fallback_name = str(assistant_id or "").strip()
    if not fallback_name or fallback_name == "lead_agent":
        return None
    return fallback_name


@dataclass(frozen=True)
class RunContext:
    """Infrastructure dependencies for a single agent run.

    Groups checkpointer, store, and persistence-related singletons so that
    ``run_agent`` (and any future callers) receive one object instead of a
    growing list of keyword arguments.
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)
    round_store: Any | None = field(default=None)


def _install_runtime_context(config: dict, runtime_context: dict[str, Any]) -> None:
    existing_context = config.get("context")
    if isinstance(existing_context, dict):
        existing_context.setdefault("thread_id", runtime_context["thread_id"])
        existing_context.setdefault("run_id", runtime_context["run_id"])
        if "app_config" in runtime_context:
            existing_context["app_config"] = runtime_context["app_config"]
        if "__run_journal" in runtime_context:
            existing_context["__run_journal"] = runtime_context["__run_journal"]
        if "round_context" in runtime_context:
            existing_context["round_context"] = runtime_context["round_context"]
        return

    config["context"] = dict(runtime_context)


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # Some callable instances are unhashable; fall back to a direct check.
        return _compute_agent_factory_supports_app_config(agent_factory)


def _is_gateway_lead_agent_factory(agent_factory: Any) -> bool:
    return getattr(agent_factory, "__module__", None) == "deerflow.agents.lead_agent.agent" and getattr(agent_factory, "__name__", None) == "make_lead_agent"


async def _publish_stream_frame(
    bridge: StreamBridge,
    event_store: Any | None,
    *,
    run_id: str,
    thread_id: str,
    user_id: str | None,
    event: str,
    data: Any,
) -> None:
    if event_store is not None:
        try:
            await event_store.put(
                thread_id=thread_id,
                run_id=run_id,
                event_type=f"stream.{event}",
                category=_STREAM_FRAME_CATEGORY,
                content={"event": event, "data": data},
                metadata={"caller": "runtime"},
                user_id=user_id,
            )
        except Exception:
            logger.warning("Failed to persist stream frame for run %s event=%s", run_id, event, exc_info=True)
    await bridge.publish(run_id, event, data)


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: dict,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
    no_progress_timeout_seconds: float | None = None,
    hard_timeout_seconds: float | None = None,
) -> None:
    """Execute an agent in the background, publishing events to *bridge*."""

    # Unpack infrastructure dependencies from RunContext.
    checkpointer = ctx.checkpointer
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store
    round_store = ctx.round_store

    run_id = record.run_id
    thread_id = record.thread_id
    if record.assistant_id == "command-room":
        # Command Room does not use TodoListMiddleware. An explicit empty update
        # prevents todo state from a different assistant leaking across runs.
        graph_input = {**graph_input, "todos": []}
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    # Treat the checkpoint state as unknown until aget_tuple completes.  A
    # cancellation before/during capture must never be mistaken for a known
    # empty thread during rollback.
    snapshot_capture_failed = checkpointer is not None
    llm_error_fallback_message: str | None = None
    latest_command_room_ai_text = ""
    owner_task = asyncio.current_task()
    lease_control_task: asyncio.Task | None = None
    terminal_committed = False

    journal = None
    runtime_ctx: dict[str, Any] | None = None

    # Track whether "events" was requested but skipped
    if "events" in requested_modes:
        logger.info(
            "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
            run_id,
        )

    try:
        # Initialize RunJournal + write human_message event.
        # These are inside the try block so any exception (e.g. a DB
        # error writing the event) flows through the except/finally
        # path that publishes an "end" event to the SSE bridge —
        # otherwise a failure here would leave the stream hanging
        # with no terminator.
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                user_id=record.user_id,
                track_token_usage=getattr(run_events_config, "track_token_usage", True),
                progress_reporter=lambda snapshot: run_manager.update_run_progress(run_id, **snapshot),
                round_store=round_store,
                round_id=record.round_id,
            )

        # 1. Mark running
        if await run_manager.set_status(run_id, RunStatus.running) is False:
            logger.warning("Run %s did not acquire a committed running state; skipping agent execution", run_id)
            return
        if owner_task is not None and record.lease_token is not None:
            lease_control_task = asyncio.create_task(_lease_control_loop(run_manager, record, owner_task))

        # Snapshot the latest pre-run checkpoint so rollback can restore it.
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }
                snapshot_capture_failed = False
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Could not capture pre-run checkpoint snapshot for run %s", run_id, exc_info=True)

        # 2. Publish metadata — useStream needs both run_id AND thread_id
        await _publish_stream_frame(
            bridge,
            event_store,
            run_id=run_id,
            thread_id=thread_id,
            user_id=record.user_id,
            event="metadata",
            data={
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. Build the agent
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # Inject runtime context so middlewares and tools (via ToolRuntime.context) can
        # access thread-level data. langgraph-cli does this automatically; we must do it
        # manually here because we drive the graph through ``agent.astream(config=...)``
        # without passing the official ``context=`` parameter.
        runtime_ctx = _build_runtime_context(thread_id, run_id, config.get("context"), ctx.app_config)
        round_context = (record.metadata or {}).get("round_context")
        if isinstance(round_context, dict):
            runtime_ctx["round_context"] = round_context
        configurable = config.get("configurable")
        if isinstance(configurable, dict) and isinstance(configurable.get("agent_name"), str) and configurable.get("agent_name", "").strip():
            runtime_ctx.setdefault("agent_name", configurable["agent_name"])
        runtime_agent_name = _resolve_runtime_agent_name(runtime_ctx, record.assistant_id)
        if runtime_agent_name is not None:
            runtime_ctx["agent_name"] = runtime_agent_name
        # Expose the run-scoped journal under a sentinel key so middleware can
        # write audit events (e.g. SafetyFinishReasonMiddleware recording
        # suppressed tool calls). Double-underscore prefix marks it as a
        # runtime-internal channel; user code must not depend on the key name.
        if journal is not None:
            runtime_ctx["__run_journal"] = journal
        _install_runtime_context(config, runtime_ctx)
        runtime = Runtime(context=cast(Any, runtime_ctx), store=store)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # Inject RunJournal as a LangChain callback handler.
        # on_llm_end captures token usage; on_chain_start/end captures lifecycle.
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # Inject Langfuse trace-attribute metadata so the langchain CallbackHandler
        # can lift session_id / user_id / trace_name / tags onto the root trace.
        # Shared helper with ``DeerFlowClient.stream`` so both entry points stay
        # in sync; caller-provided metadata wins via setdefault inside the helper.
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=record.assistant_id,
            model_name=record.model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        # Resolve after runtime context installation so context/configurable reflect
        # the agent name that this run will actually execute.
        config["run_name"] = resolve_root_run_name(config, record.assistant_id)
        runnable_config = RunnableConfig(**config)
        supports_app_config = _agent_factory_supports_app_config(agent_factory)
        if ctx.app_config is not None:
            if supports_app_config or _is_gateway_lead_agent_factory(agent_factory):
                from deerflow.agents.lead_agent.prompt import warm_enabled_skills_for_config

                await warm_enabled_skills_for_config(ctx.app_config)
            if supports_app_config:
                agent = agent_factory(config=runnable_config, app_config=ctx.app_config)
            else:
                agent = agent_factory(config=runnable_config)
        else:
            agent = agent_factory(config=runnable_config)

        # Capture the effective (resolved) model name from the agent's metadata.
        # _resolve_model_name in agent.py may return the default model if the
        # requested name is not in the allowlist — this update ensures the
        # persisted model_name reflects the actual model used.
        if record.model_name is not None:
            resolved = getattr(agent, "metadata", {}) or {}
            if isinstance(resolved, dict):
                effective = resolved.get("model_name")
                if effective and effective != record.model_name:
                    await run_manager.update_model_name(record.run_id, effective)

        # 4. Attach checkpointer and store
        if checkpointer is not None:
            agent.checkpointer = checkpointer
        if store is not None:
            agent.store = store

        # 5. Set interrupt nodes
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. Build LangGraph stream_mode list
        #    "events" is NOT a valid astream mode — skip it
        #    "messages-tuple" maps to LangGraph's "messages" mode
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                # Skipped — see log above
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # 7. Stream using graph.astream
        async for mode, chunk in _iterate_agent_stream(
            agent,
            graph_input,
            runnable_config,
            lg_modes,
            stream_subgraphs,
            no_progress_timeout_seconds=no_progress_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
        ):
            if record.abort_event.is_set():
                logger.info("Run %s abort requested — stopping", run_id)
                break
            llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk)
            latest_command_room_ai_text = _extract_latest_ai_text_from_values(chunk, latest_command_room_ai_text) if record.assistant_id == "command-room" and mode == "values" else latest_command_room_ai_text
            sse_event = _lg_mode_to_sse_event(mode)
            await _publish_stream_frame(
                bridge,
                event_store,
                run_id=run_id,
                thread_id=thread_id,
                user_id=record.user_id,
                event=sse_event,
                data=serialize(chunk, mode=mode),
            )

        # 8. Final status
        if record.abort_event.is_set():
            action = record.abort_action
            if action == "rollback":
                terminal_committed = await _finalize_rollback(
                    run_manager,
                    journal,
                    bridge,
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
            else:
                terminal_committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.interrupted, terminal_reason="cancelled", bridge=bridge, thread_id=thread_id)
        elif llm_error_fallback_message or (journal is not None and journal.had_llm_error_fallback):
            error_msg = llm_error_fallback_message
            if error_msg is None and journal is not None:
                error_msg = journal.llm_error_fallback_message
            error_msg = error_msg or "LLM provider failed after retries"
            terminal_committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.error, terminal_reason="failed", error=error_msg, bridge=bridge, thread_id=thread_id)
        else:
            if record.assistant_id == "command-room":
                await _record_command_room_round_from_worker(
                    thread_id=thread_id,
                    run_id=run_id,
                    user_message=_extract_first_human_text(graph_input),
                    final_text=latest_command_room_ai_text,
                    model_name=record.model_name,
                )
            terminal_committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.success, terminal_reason="success", bridge=bridge, thread_id=thread_id)

    except asyncio.CancelledError:
        action = record.abort_action
        if action == "rollback":
            terminal_committed = await _finalize_rollback(
                run_manager,
                journal,
                bridge,
                checkpointer=checkpointer,
                thread_id=thread_id,
                run_id=run_id,
                pre_run_checkpoint_id=pre_run_checkpoint_id,
                pre_run_snapshot=pre_run_snapshot,
                snapshot_capture_failed=snapshot_capture_failed,
            )
        else:
            terminal_committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.interrupted, terminal_reason="cancelled", bridge=bridge, thread_id=thread_id)
            logger.info("Run %s was cancelled", run_id)

    except RunStreamTimeoutError as exc:
        error_msg = str(exc)
        logger.warning("Run %s timed out: %s", run_id, error_msg)
        record.abort_action = "interrupt"
        record.abort_event.set()
        terminal_committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.timeout, terminal_reason="timeout", error=error_msg, bridge=bridge, thread_id=thread_id)
        await _publish_stream_frame(
            bridge,
            event_store,
            run_id=run_id,
            thread_id=thread_id,
            user_id=record.user_id,
            event="error",
            data={
                "message": error_msg,
                "name": type(exc).__name__,
            },
        )

    except Exception as exc:
        error_msg = f"{exc}"
        logger.exception("Run %s failed: %s", run_id, error_msg)
        terminal_committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.error, terminal_reason="failed", error=error_msg, bridge=bridge, thread_id=thread_id)
        await _publish_stream_frame(
            bridge,
            event_store,
            run_id=run_id,
            thread_id=thread_id,
            user_id=record.user_id,
            event="error",
            data={
                "message": _PUBLIC_INTERNAL_ERROR_MESSAGE,
                "name": _PUBLIC_INTERNAL_ERROR_NAME,
            },
        )

    finally:
        task_tool_module = sys.modules.get("deerflow.tools.builtins.task_tool")
        clear_subagent_usage = getattr(
            task_tool_module,
            "clear_cached_subagent_usage_for_run",
            None,
        )
        if callable(clear_subagent_usage):
            clear_subagent_usage(run_id)

        if runtime_ctx is not None:
            try:
                await release_runtime_sandbox_lease_async(runtime_ctx)
            except Exception:
                logger.warning(
                    "Failed to release sandbox lease for run %s",
                    run_id,
                    exc_info=True,
                )

        if lease_control_task is not None:
            lease_control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lease_control_task

        # Flush any buffered journal events and persist completion data
        if journal is not None:
            try:
                await journal.flush()
            except Exception:
                logger.warning("Failed to flush journal for run %s", run_id, exc_info=True)

            if terminal_committed:
                try:
                    # Persist token usage + convenience fields to RunStore
                    completion = journal.get_completion_data()
                    await run_manager.update_run_completion(run_id, status=run_status_value(record.status) or RunStatus.error.value, **completion)
                except Exception:
                    logger.warning("Failed to persist run completion for %s (non-fatal)", run_id, exc_info=True)

        # Sync title only while this run is still the newest run for its owner.
        if terminal_committed and checkpointer is not None and thread_store is not None:
            try:

                async def sync_checkpoint_title() -> None:
                    await _sync_checkpoint_title_to_thread_store(
                        checkpointer,
                        thread_store,
                        thread_id,
                        user_id=record.user_id,
                    )

                await run_manager.execute_thread_action_if_latest(
                    record,
                    sync_checkpoint_title,
                )
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id)

        # Update threads_meta status based on run outcome
        if terminal_committed and thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else run_status_value(record.status) or RunStatus.error.value
                await run_manager.update_thread_status_if_latest(record, thread_store, final_status)
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        if terminal_committed:
            await bridge.publish_end(run_id)
            asyncio.create_task(bridge.cleanup(run_id, delay=60))
        else:
            await _close_stream_for_recovery(bridge, run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _timeout_seconds_from_env(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default %s", name, raw, default)
        return default
    return value if value > 0 else None


def _resolve_timeout_seconds(override: float | None, env_name: str, default: float | None) -> float | None:
    if override is not None:
        return override if override > 0 else None
    return _timeout_seconds_from_env(env_name, default)


async def _next_stream_item(
    iterator: Any,
    *,
    run_id: str,
    no_progress_timeout: float | None,
    hard_deadline: float | None,
) -> Any:
    timeout = no_progress_timeout
    loop = asyncio.get_running_loop()
    if hard_deadline is not None:
        hard_remaining = hard_deadline - loop.time()
        if hard_remaining <= 0:
            raise RunHardTimeoutError("Run exceeded hard timeout while waiting for stream progress.")
        timeout = hard_remaining if timeout is None else min(timeout, hard_remaining)
    try:
        if timeout is None:
            return await iterator.__anext__()
        return await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
    except TimeoutError as exc:
        if timeout is None:
            raise
        if hard_deadline is not None and loop.time() >= hard_deadline:
            raise RunHardTimeoutError("Run exceeded hard timeout while waiting for stream progress.") from exc
        raise RunNoProgressTimeoutError(f"Run made no stream progress for {timeout:.1f}s.") from exc


async def _close_stream_iterator(iterator: Any, run_id: str) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await asyncio.wait_for(close(), timeout=_STREAM_CLOSE_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("Timed out closing stream iterator for run %s", run_id)
    except Exception:
        logger.debug("Failed to close stream iterator for run %s", run_id, exc_info=True)


async def _iterate_agent_stream(
    agent: Any,
    graph_input: dict,
    runnable_config: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
    *,
    no_progress_timeout_seconds: float | None,
    hard_timeout_seconds: float | None,
):
    run_id = runnable_config.get("context", {}).get("run_id", "unknown") if isinstance(runnable_config, dict) else "unknown"
    no_progress_timeout = _resolve_timeout_seconds(
        no_progress_timeout_seconds,
        "DEER_FLOW_RUN_NO_PROGRESS_TIMEOUT_SECONDS",
        _RUN_NO_PROGRESS_TIMEOUT_SECONDS,
    )
    hard_timeout = _resolve_timeout_seconds(
        hard_timeout_seconds,
        "DEER_FLOW_RUN_HARD_TIMEOUT_SECONDS",
        _RUN_HARD_TIMEOUT_SECONDS,
    )
    hard_deadline = asyncio.get_running_loop().time() + hard_timeout if hard_timeout is not None else None

    if len(lg_modes) == 1 and not stream_subgraphs:
        single_mode = lg_modes[0]
        iterator = agent.astream(graph_input, config=runnable_config, stream_mode=single_mode).__aiter__()
        try:
            while True:
                try:
                    chunk = await _next_stream_item(
                        iterator,
                        run_id=run_id,
                        no_progress_timeout=no_progress_timeout,
                        hard_deadline=hard_deadline,
                    )
                except StopAsyncIteration:
                    break
                yield single_mode, chunk
        finally:
            await _close_stream_iterator(iterator, run_id)
        return

    iterator = agent.astream(
        graph_input,
        config=runnable_config,
        stream_mode=lg_modes,
        subgraphs=stream_subgraphs,
    ).__aiter__()
    try:
        while True:
            try:
                item = await _next_stream_item(
                    iterator,
                    run_id=run_id,
                    no_progress_timeout=no_progress_timeout,
                    hard_deadline=hard_deadline,
                )
            except StopAsyncIteration:
                break
            mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
            if mode is not None:
                yield mode, chunk
    finally:
        await _close_stream_iterator(iterator, run_id)


async def _flush_journal_before_terminal_status(journal: Any | None, run_id: str) -> None:
    if journal is None:
        return
    try:
        await journal.flush()
    except Exception:
        logger.warning("Failed to flush journal before terminal status for run %s", run_id, exc_info=True)


async def _lease_control_loop(run_manager: RunManager, record: RunRecord, owner_task: asyncio.Task) -> None:
    while not owner_task.done():
        await asyncio.sleep(_LEASE_CONTROL_INTERVAL_SECONDS)
        if owner_task.done() or record.lease_terminal_committing or record.lease_terminal_committed:
            return
        if not await run_manager.heartbeat_active_lease(record):
            if record.lease_terminal_committing or record.lease_terminal_committed:
                return
            record.abort_action = "interrupt"
            record.abort_event.set()
            owner_task.cancel()
            return
        if owner_task.done() or record.lease_terminal_committing or record.lease_terminal_committed:
            return
        intent = await run_manager.consume_cancel_intent(record)
        if intent is not None:
            if owner_task.done() or record.lease_terminal_committing or record.lease_terminal_committed:
                return
            record.abort_action = intent.action
            record.abort_event.set()
            owner_task.cancel()
            return


async def _finalize_rollback(
    run_manager: RunManager,
    journal: Any | None,
    bridge: StreamBridge,
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> bool:
    try:
        await _rollback_to_pre_run_checkpoint(
            checkpointer=checkpointer,
            thread_id=thread_id,
            run_id=run_id,
            pre_run_checkpoint_id=pre_run_checkpoint_id,
            pre_run_snapshot=pre_run_snapshot,
            snapshot_capture_failed=snapshot_capture_failed,
        )
    except asyncio.CancelledError:
        error = "Rollback failed: checkpoint restore was cancelled"
        logger.warning("%s for run %s", error, run_id)
        return await _set_terminal_status(run_manager, journal, run_id, RunStatus.error, terminal_reason="rollback_failed", error=error, bridge=bridge, thread_id=thread_id)
    except Exception as exc:
        error = f"Rollback failed: {exc}"
        logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
        return await _set_terminal_status(run_manager, journal, run_id, RunStatus.error, terminal_reason="rollback_failed", error=error, bridge=bridge, thread_id=thread_id)
    else:
        committed = await _set_terminal_status(run_manager, journal, run_id, RunStatus.error, terminal_reason="rolled_back", error="Rolled back by user", bridge=bridge, thread_id=thread_id)
        logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
        return committed


async def _set_terminal_status(
    run_manager: RunManager,
    journal: Any | None,
    run_id: str,
    status: RunStatus,
    *,
    terminal_reason: str,
    error: str | None = None,
    bridge: StreamBridge | None = None,
    thread_id: str | None = None,
) -> bool:
    await _flush_journal_before_terminal_status(journal, run_id)
    committed = await run_manager.set_status(run_id, status, error=error, terminal_reason=terminal_reason)
    if committed is False:
        return False
    if journal is not None:
        journal.record_run_terminal(status=status.value, terminal_reason=terminal_reason)
        await _flush_journal_before_terminal_status(journal, run_id)
    if bridge is not None and thread_id is not None:
        try:
            await bridge.publish(
                run_id,
                "custom",
                {
                    "type": "run.terminal",
                    "event_type": "run.terminal",
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "status": status.value,
                    "terminal_reason": terminal_reason,
                },
            )
        except Exception:
            logger.warning("Failed to publish terminal event for run %s", run_id, exc_info=True)
    return True


async def _close_stream_for_recovery(bridge: StreamBridge, run_id: str) -> None:
    """Finish live subscribers without claiming an uncommitted terminal state."""
    try:
        await bridge.publish(
            run_id,
            _STREAM_RECOVERY_REQUIRED_EVENT,
            {"reason": _STATUS_COMMIT_FAILED_REASON},
        )
    except Exception:
        logger.warning("Failed to publish stream recovery marker for run %s", run_id, exc_info=True)
    await bridge.publish_end(run_id)
    asyncio.create_task(bridge.cleanup(run_id, delay=60))


async def _sync_checkpoint_title_to_thread_store(checkpointer, thread_store, thread_id: str, *, user_id: str | None = None) -> None:
    ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
    if ckpt_tuple is None:
        return
    ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
    channel_values = ckpt.get("channel_values", {})
    title = channel_values.get("title")
    if not title:
        return
    record = await thread_store.get(thread_id, user_id=user_id)
    display_name = record.get("display_name") if record else None
    if display_name:
        return
    await thread_store.update_display_name_if_empty(thread_id, title, user_id=user_id)


async def _call_checkpointer_method(checkpointer: Any, async_name: str, sync_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a checkpointer method, supporting async and sync variants."""
    method = getattr(checkpointer, async_name, None) or getattr(checkpointer, sync_name, None)
    if method is None:
        raise AttributeError(f"Missing checkpointer method: {async_name}/{sync_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """Restore thread state to the checkpoint snapshot captured before run start."""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        raise RuntimeError(f"Run {run_id} rollback failed: pre-run checkpoint snapshot capture failed")

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise RuntimeError(f"Run {run_id} rollback failed: invalid pre-run checkpoint snapshot")
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        raise RuntimeError(f"Run {run_id} rollback failed: pre-run checkpoint has no checkpoint id")
    restore_marker = _new_checkpoint_marker()
    checkpoint_to_restore = {
        **checkpoint_to_restore,
        "id": restore_marker["id"],
        "ts": restore_marker["ts"],
    }
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _new_checkpoint_marker() -> dict[str, str]:
    marker = empty_checkpoint()
    return {"id": marker["id"], "ts": marker["ts"]}


def _extract_latest_ai_text_from_values(value: Any, current: str) -> str:
    if not isinstance(value, dict):
        return current
    messages = value.get("messages")
    if not isinstance(messages, (list, tuple)):
        return current

    latest = current
    for msg in messages:
        text = ""
        if isinstance(msg, AIMessage):
            from deerflow.command_room.round_record import extract_text

            text = extract_text(msg.content)
        elif isinstance(msg, dict) and msg.get("type") == "ai":
            from deerflow.command_room.round_record import extract_text

            text = extract_text(msg.get("content"))
        if text:
            latest = text
    return latest


def _extract_first_human_text(graph_input: dict) -> str | None:
    messages = graph_input.get("messages") if isinstance(graph_input, dict) else None
    if not isinstance(messages, (list, tuple)):
        return None

    for msg in messages:
        if isinstance(msg, HumanMessage):
            from deerflow.command_room.round_record import extract_text

            return extract_text(msg.content)
        if isinstance(msg, dict) and msg.get("type") in {"human", "user"}:
            from deerflow.command_room.round_record import extract_text

            return extract_text(msg.get("content"))
    return None


async def _record_command_room_round_from_worker(
    *,
    thread_id: str,
    run_id: str,
    user_message: str | None,
    final_text: str | None,
    model_name: str | None,
) -> None:
    try:
        from deerflow.command_room.round_record import record_command_room_round

        record_task = asyncio.create_task(
            asyncio.to_thread(
                record_command_room_round,
                thread_id=thread_id,
                agent_name="command-room",
                user_id=get_effective_user_id(),
                final_text=final_text,
                user_message=user_message,
                run_id=run_id,
                usage=None,
                source="gateway",
            )
        )
        try:
            await asyncio.shield(record_task)
        except asyncio.CancelledError:
            # The agent stream has already produced its final answer. Keep the
            # RoundRecord and terminal run projection on the same success side
            # of this post-processing cancellation boundary.
            await record_task
    except Exception:
        logger.debug(
            "Failed to record command-room RoundRecord for run %s model=%s",
            run_id,
            model_name,
            exc_info=True,
        )


def _lg_mode_to_sse_event(mode: str) -> str:
    """Map LangGraph internal stream_mode name to SSE event name.

    LangGraph's ``astream(stream_mode="messages")`` produces message
    tuples.  The SSE protocol calls this ``messages-tuple`` when the
    client explicitly requests it, but the default SSE event name used
    by LangGraph Platform is simply ``"messages"``.
    """
    # All LG modes map 1:1 to SSE event names — "messages" stays "messages"
    return mode


def _error_fallback_message_from_metadata(metadata: dict[str, Any], content: Any) -> str:
    detail = metadata.get("error_detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    reason = metadata.get("error_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    if isinstance(content, str) and content.strip():
        return content.strip()[:2000]
    return "LLM provider failed after retries"


def _try_extract_from_message(obj: Any) -> str | None:
    """Try to extract fallback marker from a single message object or dict."""
    additional_kwargs = getattr(obj, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback"):
        return _error_fallback_message_from_metadata(additional_kwargs, getattr(obj, "content", None))

    if isinstance(obj, dict):
        nested_kwargs = obj.get("additional_kwargs")
        if isinstance(nested_kwargs, dict) and nested_kwargs.get("deerflow_error_fallback"):
            return _error_fallback_message_from_metadata(nested_kwargs, obj.get("content"))
    return None


def _extract_llm_error_fallback_message(value: Any) -> str | None:
    """Find LLM fallback markers in streamed LangGraph chunks.

    Error fallback messages returned by model-call middleware are not guaranteed
    to pass through LLM end callbacks, but they do appear in graph state chunks.
    """
    # Fast path: large state chunks produced by stream_mode="values" have a
    # top-level "messages" list. Scanning only that list avoids expensive deep
    # recursion into large state dicts.
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, (list, tuple)):
            for msg in messages:
                result = _try_extract_from_message(msg)
                if result is not None:
                    return result
            # Fallback marker is attached to an AI message in the messages
            # channel; it will never appear elsewhere in a values chunk.
            return None
        # No top-level "messages" — this is likely an "updates" chunk (small
        # dict keyed by node name). Fall through to deep walk, which is cheap
        # for these payloads.

    # Deep walk for updates / messages / tuple / list modes. Payloads are
    # small, so full recursion is acceptable here.
    seen: set[int] = set()

    def walk(obj: Any) -> str | None:
        oid = id(obj)
        if oid in seen:
            return None
        seen.add(oid)

        result = _try_extract_from_message(obj)
        if result is not None:
            return result

        if isinstance(obj, dict):
            for item in obj.values():
                result = walk(item)
                if result is not None:
                    return result
            return None

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                result = walk(item)
                if result is not None:
                    return result
        return None

    return walk(value)


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """Unpack a multi-mode or subgraph stream item into (mode, chunk).

    Returns ``(None, None)`` if the item cannot be parsed.
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # Fallback: single-element output from first mode
    return lg_modes[0] if lg_modes else None, item
