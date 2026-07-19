import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.config.subagents_config import SubagentsAppConfig
from deerflow.persistence.round_state import MemoryRoundStateStore
from deerflow.runtime.checkpoint_owner import (
    owner_checkpoint_config,
    owner_checkpoint_thread_id,
)
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.runs.worker import (
    _RUN_NO_PROGRESS_TIMEOUT_SECONDS,
    RunContext,
    _agent_factory_supports_app_config,
    _build_runtime_context,
    _extract_llm_error_fallback_message,
    _install_runtime_context,
    _lease_control_loop,
    _resolve_runtime_agent_name,
    _rollback_to_pre_run_checkpoint,
    _sync_checkpoint_title_to_thread_store,
    _try_extract_from_message,
    run_agent,
)


def test_default_run_watchdog_exceeds_default_codex_task_timeout():
    assert _RUN_NO_PROGRESS_TIMEOUT_SECONDS > SubagentsAppConfig().timeout_seconds


class FakeCheckpointer:
    def __init__(self, *, put_result):
        self.adelete_thread = AsyncMock()
        self.aput = AsyncMock(return_value=put_result)
        self.aput_writes = AsyncMock()


def _make_checkpoint(checkpoint_id: str, messages: list[str], version: int):
    checkpoint = empty_checkpoint()
    checkpoint["id"] = checkpoint_id
    checkpoint["channel_values"] = {"messages": messages}
    checkpoint["channel_versions"] = {"messages": version}
    return checkpoint


def test_build_runtime_context_includes_app_config_when_present():
    app_config = object()

    context = _build_runtime_context("thread-1", "run-1", None, app_config)

    assert context["thread_id"] == "thread-1"
    assert context["run_id"] == "run-1"
    assert context["app_config"] is app_config


def test_resolve_runtime_agent_name_omits_default_lead_agent():
    assert _resolve_runtime_agent_name({}, "lead_agent") is None


def test_resolve_runtime_agent_name_uses_custom_assistant_id():
    assert _resolve_runtime_agent_name({}, "command-room") == "command-room"


def test_resolve_runtime_agent_name_prefers_explicit_context():
    assert _resolve_runtime_agent_name({"agent_name": "finalis"}, "lead_agent") == "finalis"


def test_install_runtime_context_preserves_existing_thread_id_and_threads_app_config():
    app_config = object()
    config = {"context": {"thread_id": "caller-thread"}}

    _install_runtime_context(
        config,
        {
            "thread_id": "record-thread",
            "run_id": "run-1",
            "app_config": app_config,
        },
    )

    assert config["context"]["thread_id"] == "caller-thread"
    assert config["context"]["run_id"] == "run-1"
    assert config["context"]["app_config"] is app_config


def test_install_runtime_context_exposes_run_journal_to_tools():
    journal = object()
    config = {"context": {"thread_id": "caller-thread"}}

    _install_runtime_context(
        config,
        {
            "thread_id": "record-thread",
            "run_id": "run-1",
            "__run_journal": journal,
            "round_context": {"round_id": "round-1"},
        },
    )

    assert config["context"]["__run_journal"] is journal
    assert config["context"]["round_context"] == {"round_id": "round-1"}


@pytest.mark.anyio
async def test_sync_checkpoint_title_fills_empty_display_name():
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(return_value=SimpleNamespace(checkpoint={"channel_values": {"title": "Auto Title"}})),
        aput=AsyncMock(),
    )
    thread_store = SimpleNamespace(
        get=AsyncMock(return_value={"thread_id": "thread-1", "display_name": None}),
        update_display_name=AsyncMock(),
        update_display_name_if_empty=AsyncMock(return_value=True),
    )

    await _sync_checkpoint_title_to_thread_store(checkpointer, thread_store, "thread-1", user_id="owner-1")

    thread_store.get.assert_awaited_once_with("thread-1", user_id="owner-1")
    thread_store.update_display_name_if_empty.assert_awaited_once_with("thread-1", "Auto Title", user_id="owner-1")
    thread_store.update_display_name.assert_not_awaited()
    checkpointer.aput.assert_not_awaited()


@pytest.mark.anyio
async def test_sync_checkpoint_title_does_not_overwrite_existing_display_name():
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(
            return_value=SimpleNamespace(
                checkpoint={"channel_values": {"title": "Auto Title"}},
                metadata={"created_at": "now"},
            )
        ),
        aput=AsyncMock(),
    )
    thread_store = SimpleNamespace(
        get=AsyncMock(return_value={"thread_id": "thread-1", "display_name": "Manual Title"}),
        update_display_name=AsyncMock(),
        update_display_name_if_empty=AsyncMock(return_value=False),
    )

    await _sync_checkpoint_title_to_thread_store(checkpointer, thread_store, "thread-1", user_id="owner-1")

    thread_store.get.assert_awaited_once_with("thread-1", user_id="owner-1")
    thread_store.update_display_name.assert_not_awaited()
    thread_store.update_display_name_if_empty.assert_not_awaited()
    checkpointer.aput.assert_not_awaited()


@pytest.mark.anyio
async def test_sync_checkpoint_title_uses_atomic_fill_if_empty_after_stale_read():
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(return_value=SimpleNamespace(checkpoint={"channel_values": {"title": "Auto Title"}})),
    )
    display_name = None
    stale_read_finished = asyncio.Event()
    allow_auto_write = asyncio.Event()

    class RacingThreadStore:
        async def get(self, thread_id, *, user_id=None):
            stale_read_finished.set()
            return {"thread_id": thread_id, "display_name": display_name}

        async def update_display_name(self, thread_id, title, *, user_id=None):
            nonlocal display_name
            await allow_auto_write.wait()
            display_name = title

        async def update_display_name_if_empty(self, thread_id, title, *, user_id=None):
            nonlocal display_name
            await allow_auto_write.wait()
            if display_name:
                return False
            display_name = title
            return True

    store = RacingThreadStore()
    sync_task = asyncio.create_task(_sync_checkpoint_title_to_thread_store(checkpointer, store, "thread-1", user_id="owner-1"))
    await stale_read_finished.wait()
    display_name = "Manual Title"
    allow_auto_write.set()
    await sync_task

    assert display_name == "Manual Title"


@pytest.mark.anyio
async def test_repeated_cancel_does_not_interrupt_post_terminal_projection(monkeypatch):
    agent_started = asyncio.Event()
    terminal_flush_started = asyncio.Event()
    allow_terminal_flush = asyncio.Event()

    class ControlledJournal:
        def __init__(self, *args, **kwargs):
            self.flush_count = 0
            self.had_llm_error_fallback = False
            self.llm_error_fallback_message = None

        async def flush(self):
            self.flush_count += 1
            if self.flush_count == 2:
                terminal_flush_started.set()
                await allow_terminal_flush.wait()

        def record_run_terminal(self, **kwargs):
            return None

        def get_completion_data(self):
            return {
                "total_input_tokens": 1,
                "total_output_tokens": 1,
                "total_tokens": 2,
                "llm_call_count": 1,
                "lead_agent_tokens": 2,
                "subagent_tokens": 0,
                "middleware_tokens": 0,
                "token_usage_by_model": {},
                "message_count": 0,
                "last_ai_message": None,
                "first_human_message": None,
            }

    class BlockingAgent:
        async def astream(self, *args, **kwargs):
            agent_started.set()
            await asyncio.Event().wait()
            yield {}

    monkeypatch.setattr("deerflow.runtime.journal.RunJournal", ControlledJournal)
    store = MemoryRunStore()
    run_manager = RunManager(store=store)
    record = await run_manager.create_or_reject("thread-double-cancel", user_id="owner-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    thread_store = SimpleNamespace(update_status=AsyncMock())
    worker = asyncio.create_task(
        run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(
                checkpointer=None,
                event_store=MemoryRunEventStore(),
                thread_store=thread_store,
            ),
            agent_factory=lambda *, config: BlockingAgent(),
            graph_input={},
            config={},
        )
    )
    record.task = worker

    await asyncio.wait_for(agent_started.wait(), timeout=2)
    assert await run_manager.cancel(record.run_id) is True
    await asyncio.wait_for(terminal_flush_started.wait(), timeout=2)
    try:
        assert await run_manager.cancel(record.run_id) is True
    finally:
        allow_terminal_flush.set()
    await asyncio.wait_for(worker, timeout=2)

    durable = await store.get(record.run_id)
    assert durable is not None
    assert durable["status"] == RunStatus.interrupted.value
    assert durable["terminal_reason"] == "cancelled"
    assert durable["total_tokens"] == 2
    thread_store.update_status.assert_awaited_once_with(
        record.thread_id,
        RunStatus.interrupted.value,
        user_id="owner-1",
    )
    terminal_payloads = [call.args[2] for call in bridge.publish.await_args_list if call.args[1] == "custom" and call.args[2].get("type") == "run.terminal"]
    assert [payload["terminal_reason"] for payload in terminal_payloads] == ["cancelled"]


@pytest.mark.anyio
async def test_run_agent_gates_checkpoint_title_sync_on_latest_run():
    run_manager = RunManager()
    record = await run_manager.create("thread-title-latest", user_id="owner-1")
    latest_guard = AsyncMock(return_value=False)
    run_manager.execute_thread_action_if_latest = latest_guard
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(return_value=None),
        aput=AsyncMock(),
    )
    thread_store = SimpleNamespace(
        get=AsyncMock(),
        update_display_name=AsyncMock(),
        update_status=AsyncMock(),
    )
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=checkpointer, thread_store=thread_store),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    latest_guard.assert_awaited_once()
    assert checkpointer.aget_tuple.await_count == 1
    thread_store.get.assert_not_awaited()
    thread_store.update_display_name.assert_not_awaited()


@pytest.mark.anyio
async def test_run_agent_threads_explicit_app_config_into_config_only_factory():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    app_config = object()
    captured: dict[str, object] = {}

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            captured["astream_context"] = config["context"]
            yield {"messages": []}

    def factory(*, config):
        captured["factory_context"] = config["context"]
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, app_config=app_config),
        agent_factory=factory,
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    assert captured["factory_context"]["app_config"] is app_config
    assert captured["astream_context"]["app_config"] is app_config
    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.success
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.cleanup.assert_awaited_once_with(record.run_id, delay=60)


@pytest.mark.anyio
async def test_run_agent_offloads_explicit_config_skill_scan(monkeypatch):
    from deerflow.agents.lead_agent import agent as lead_agent_module
    from deerflow.agents.lead_agent import prompt as prompt_module

    event_loop_thread = threading.get_ident()
    load_threads: list[int] = []
    app_config = object()

    def load_skills(*, enabled_only):
        assert enabled_only is True
        load_threads.append(threading.get_ident())
        return []

    monkeypatch.setattr(
        prompt_module,
        "get_or_new_skill_storage",
        lambda **kwargs: SimpleNamespace(load_skills=load_skills),
    )
    with prompt_module._enabled_skills_lock:
        prompt_module._enabled_skills_by_config_cache.clear()

    run_manager = RunManager()
    record = await run_manager.create("thread-skill-scan")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, *args, **kwargs):
            yield {"messages": []}

    def make_agent(config, *, app_config):
        prompt_module.get_enabled_skills_for_config(app_config)
        return DummyAgent()

    monkeypatch.setattr(lead_agent_module, "_make_lead_agent", make_agent)

    try:
        await run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None, app_config=app_config),
            agent_factory=lead_agent_module.make_lead_agent,
            graph_input={},
            config={},
        )

        assert load_threads
        assert event_loop_thread not in load_threads
    finally:
        with prompt_module._enabled_skills_lock:
            prompt_module._enabled_skills_by_config_cache.clear()


@pytest.mark.anyio
async def test_run_agent_updates_thread_meta_with_run_owner():
    run_manager = RunManager()
    record = await run_manager.create("thread-1", user_id="owner-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    thread_store = SimpleNamespace(update_status=AsyncMock())

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    def factory(*, config):
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, thread_store=thread_store),
        agent_factory=factory,
        graph_input={},
        config={},
    )

    thread_store.update_status.assert_awaited_once_with("thread-1", "idle", user_id="owner-1")


@pytest.mark.anyio
async def test_run_agent_success_path_persists_runtime_facts():
    """The worker records the actual Run result and child-task result."""
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    run_manager = RunManager(store=MemoryRunStore(), round_store=round_store)
    record = await run_manager.create("thread-main-path", assistant_id="lead_agent", user_id="owner-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    thread_store = SimpleNamespace(update_status=AsyncMock())

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            journal = config["context"]["__run_journal"]
            llm_run_id = uuid4()
            journal.on_chat_model_start(
                {},
                [[HumanMessage(content="main path prompt")]],
                run_id=llm_run_id,
                tags=["lead_agent"],
            )
            journal.record_task_event(
                {
                    "schema_version": "deerflow.task-event/v1",
                    "type": "task_completed",
                    "event_type": "task_completed",
                    "thread_id": record.thread_id,
                    "run_id": record.run_id,
                    "task_id": "task-main",
                    "subagent_type": "evidence",
                    "status": "completed",
                    "result_preview": "task done",
                    "action_result": {
                        "status": "completed",
                        "summary": "task done",
                        "evidence_refs": ["command: fake; exit code: 0"],
                    },
                }
            )
            journal.on_llm_end(
                SimpleNamespace(generations=[[SimpleNamespace(message=AIMessage(content="Final answer"))]]),
                run_id=llm_run_id,
                tags=["lead_agent"],
            )
            yield {"messages": [AIMessage(content="Final answer")]}

    def factory(*, config):
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=None,
            event_store=event_store,
            thread_store=thread_store,
            round_store=round_store,
        ),
        agent_factory=factory,
        graph_input={"messages": [HumanMessage(content="main path prompt")]},
        config={},
    )

    fetched = await run_manager.get(record.run_id, user_id="owner-1")
    assert fetched is not None
    assert fetched.status == RunStatus.success
    assert fetched.terminal_reason == "success"

    message_events = await event_store.list_messages_by_run(
        "thread-main-path",
        record.run_id,
        user_id="owner-1",
    )
    event_types = [event["event_type"] for event in message_events]
    assert "llm.human.input" in event_types
    assert "llm.ai.response" in event_types
    assert any(event["content"]["content"] == "Final answer" for event in message_events if event["event_type"] == "llm.ai.response")

    terminal_events = await event_store.list_events(
        "thread-main-path",
        record.run_id,
        event_types=["run.terminal"],
        user_id="owner-1",
    )
    assert [event["content"] for event in terminal_events] == [
        {"status": "success", "terminal_reason": "success"},
    ]

    rounds = await round_store.list_by_thread("thread-main-path", user_id="owner-1")
    assert rounds[0]["round_id"] == record.round_id
    assert rounds[0]["current_run_id"] == record.run_id
    lanes = await round_store.list_task_lanes_by_round(
        thread_id="thread-main-path",
        round_id=record.round_id,
        user_id="owner-1",
    )
    assert [(lane["task_id"], lane["status"], lane["evidence_ref"]) for lane in lanes] == [
        ("task-main", "completed", "command: fake; exit code: 0"),
    ]
    thread_store.update_status.assert_awaited_once_with("thread-main-path", "idle", user_id="owner-1")


@pytest.mark.anyio
async def test_command_room_run_clears_todos_inherited_from_another_assistant():
    run_manager = RunManager()
    record = await run_manager.create("thread-command-room-todos", assistant_id="command-room")
    captured_input = {}
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            captured_input.update(graph_input)
            yield {"messages": [AIMessage(content="done")]}

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={
            "messages": [],
            "todos": [{"content": "stale lead-agent task", "status": "in_progress"}],
        },
        config={},
    )

    assert captured_input["todos"] == []


@pytest.mark.anyio
async def test_run_agent_marks_llm_error_fallback_as_error_status():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {
                "messages": [
                    AIMessage(
                        content="The configured LLM provider is temporarily unavailable after multiple retries.",
                        additional_kwargs={
                            "deerflow_error_fallback": True,
                            "error_type": "APIConnectionError",
                            "error_reason": "transient",
                            "error_detail": "Connection error.",
                        },
                    )
                ]
            }

    def factory(*, config):
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=factory,
        graph_input={},
        config={},
    )

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.error == "Connection error."
    bridge.publish_end.assert_awaited_once_with(record.run_id)


@pytest.mark.anyio
async def test_run_agent_rolls_back_checkpoint_after_llm_error_fallback():
    run_manager = RunManager()
    record = await run_manager.create("thread-llm-error", user_id="owner-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    pre_run_checkpoint = {
        "id": "ckpt-before",
        "channel_versions": {"messages": 2},
        "channel_values": {"messages": [HumanMessage(content="before"), AIMessage(content="ok")]},
    }
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(
            return_value=SimpleNamespace(
                config={"configurable": {"checkpoint_id": "ckpt-before", "checkpoint_ns": ""}},
                checkpoint=pre_run_checkpoint,
                metadata={"source": "before"},
                pending_writes=[],
            )
        ),
        aput=AsyncMock(
            return_value=owner_checkpoint_config(
                "thread-llm-error",
                "owner-1",
                checkpoint_ns="",
                checkpoint_id="ckpt-restored",
            )
        ),
        adelete_thread=AsyncMock(),
        aput_writes=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {
                "messages": [
                    AIMessage(
                        content="The configured LLM provider is temporarily unavailable after multiple retries.",
                        additional_kwargs={
                            "deerflow_error_fallback": True,
                            "error_type": "CodexRetryExhaustedError",
                            "error_reason": "transient",
                            "error_detail": "Codex provider retry budget exhausted after 3 attempts",
                        },
                    )
                ]
            }

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=checkpointer),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={"messages": [HumanMessage(content="this run will fail")]},
        config={},
    )

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.terminal_reason == "failed"
    assert fetched.error == "Codex provider retry budget exhausted after 3 attempts"
    checkpointer.adelete_thread.assert_not_awaited()
    checkpointer.aput.assert_awaited_once()
    _, restored_checkpoint, restored_metadata, restored_versions = checkpointer.aput.await_args.args
    assert restored_checkpoint["id"] != "ckpt-before"
    assert restored_checkpoint["channel_values"] == pre_run_checkpoint["channel_values"]
    assert restored_metadata == {"source": "before"}
    assert restored_versions == {"messages": 2}
    bridge.publish_end.assert_awaited_once_with(record.run_id)


@pytest.mark.anyio
async def test_run_agent_keeps_internal_exception_but_redacts_stream_error():
    run_manager = RunManager()
    record = await run_manager.create("thread-private-error")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class FailingAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise RuntimeError("failed at /Users/private/secrets.txt token=abc")
            yield  # pragma: no cover

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=lambda *, config: FailingAgent(),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.error
    assert record.error == "failed at /Users/private/secrets.txt token=abc"
    bridge.publish.assert_any_await(
        record.run_id,
        "error",
        {
            "message": "Run failed due to an internal error.",
            "name": "InternalError",
        },
    )


@pytest.mark.anyio
async def test_lease_loop_does_not_cancel_terminal_commit_after_slot_release(monkeypatch):
    class CommitThenPauseStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.committed = asyncio.Event()
            self.allow_return = asyncio.Event()

        async def complete_run(self, *args, **kwargs):
            committed = await super().complete_run(*args, **kwargs)
            self.committed.set()
            await self.allow_return.wait()
            return committed

    monkeypatch.setattr(
        "deerflow.runtime.runs.worker._LEASE_CONTROL_INTERVAL_SECONDS",
        0,
    )
    store = CommitThenPauseStore()
    manager = RunManager(store=store)
    record = await manager.create_or_reject("thread-terminal-commit-window")
    assert await manager.set_status(record.run_id, RunStatus.running)
    terminal_task = asyncio.create_task(
        manager.set_status(
            record.run_id,
            RunStatus.success,
            terminal_reason="success",
        )
    )
    lease_task = asyncio.create_task(_lease_control_loop(manager, record, terminal_task))

    try:
        await store.committed.wait()
        await asyncio.sleep(0)
        assert not terminal_task.cancelled()
        store.allow_return.set()
        assert await terminal_task is True
        await lease_task
    finally:
        store.allow_return.set()
        if not terminal_task.done():
            terminal_task.cancel()
        if not lease_task.done():
            lease_task.cancel()

    assert record.status == RunStatus.success
    assert record.run_id in manager._runs


@pytest.mark.anyio
async def test_run_agent_times_out_stalled_stream_and_publishes_end():
    run_manager = RunManager()
    record = await run_manager.create("thread-1", user_id="owner-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    event_store = MemoryRunEventStore()
    thread_store = SimpleNamespace(update_status=AsyncMock())
    stream_entered = asyncio.Event()

    class StalledAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            stream_entered.set()
            await asyncio.Event().wait()
            yield {"messages": []}

    def factory(*, config):
        return StalledAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=None,
            event_store=event_store,
            thread_store=thread_store,
        ),
        agent_factory=factory,
        graph_input={},
        config={},
        no_progress_timeout_seconds=0.05,
        hard_timeout_seconds=0,
    )

    assert stream_entered.is_set()
    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.timeout
    assert fetched.terminal_reason == "timeout"
    assert fetched.error == "Run made no stream progress for 0.1s."
    thread_store.update_status.assert_awaited_once_with("thread-1", "timeout", user_id="owner-1")
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.publish.assert_any_await(
        record.run_id,
        "custom",
        {
            "type": "run.terminal",
            "event_type": "run.terminal",
            "thread_id": "thread-1",
            "run_id": record.run_id,
            "status": "timeout",
            "terminal_reason": "timeout",
        },
    )
    events = await event_store.list_events(
        "thread-1",
        record.run_id,
        event_types=["run.terminal"],
        user_id="owner-1",
    )
    assert [event["content"] for event in events] == [
        {"status": "timeout", "terminal_reason": "timeout"},
    ]


@pytest.mark.anyio
async def test_run_agent_hard_timeout_finalizes_and_releases_active_slot():
    run_manager = RunManager(store=MemoryRunStore())
    record = await run_manager.create_or_reject("thread-hard-timeout", user_id="owner-1")
    bridge_events: list[tuple[str, str | None, object | None]] = []
    bridge = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda run_id, event, data: bridge_events.append(("publish", event, data))),
        publish_end=AsyncMock(side_effect=lambda run_id: bridge_events.append(("end", None, run_id))),
        cleanup=AsyncMock(),
    )
    event_store = MemoryRunEventStore()
    thread_store = SimpleNamespace(update_status=AsyncMock())
    stream_entered = asyncio.Event()

    class StalledAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            stream_entered.set()
            await asyncio.Event().wait()
            yield {"messages": []}

    def factory(*, config):
        return StalledAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=None,
            event_store=event_store,
            thread_store=thread_store,
        ),
        agent_factory=factory,
        graph_input={},
        config={},
        no_progress_timeout_seconds=60,
        hard_timeout_seconds=0.05,
    )

    assert stream_entered.is_set()
    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.timeout
    assert fetched.terminal_reason == "timeout"
    assert fetched.error == "Run exceeded hard timeout while waiting for stream progress."
    thread_store.update_status.assert_awaited_once_with("thread-hard-timeout", "timeout", user_id="owner-1")
    bridge.publish.assert_any_await(
        record.run_id,
        "custom",
        {
            "type": "run.terminal",
            "event_type": "run.terminal",
            "thread_id": "thread-hard-timeout",
            "run_id": record.run_id,
            "status": "timeout",
            "terminal_reason": "timeout",
        },
    )
    bridge.publish.assert_any_await(
        record.run_id,
        "error",
        {
            "message": "Run exceeded hard timeout while waiting for stream progress.",
            "name": "RunHardTimeoutError",
        },
    )
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    assert [event for _, event, _ in bridge_events] == [
        "metadata",
        "custom",
        "error",
        None,
    ]

    terminal_events = await event_store.list_events(
        "thread-hard-timeout",
        record.run_id,
        event_types=["run.terminal"],
        user_id="owner-1",
    )
    assert [event["content"] for event in terminal_events] == [
        {"status": "timeout", "terminal_reason": "timeout"},
    ]

    replacement = await run_manager.create_or_reject("thread-hard-timeout", user_id="owner-1")
    assert replacement.run_id != record.run_id
    await run_manager.set_status(replacement.run_id, RunStatus.interrupted, terminal_reason="test_cleanup")


@pytest.mark.anyio
async def test_run_agent_closes_live_stream_truthfully_when_lease_completion_fails():
    class RejectingTerminalStore(MemoryRunStore):
        async def complete_run(self, *args, **kwargs):
            return False

    event_store = MemoryRunEventStore()
    run_manager = RunManager(store=RejectingTerminalStore())
    record = await run_manager.create_or_reject("thread-terminal-cas", user_id="owner-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": [AIMessage(content="done")]}

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=event_store),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    assert record.status == RunStatus.running
    terminal_payloads = [call.args[2] for call in bridge.publish.await_args_list if call.args[1] == "custom" and call.args[2].get("type") == "run.terminal"]
    assert terminal_payloads == []
    bridge.publish.assert_any_await(
        record.run_id,
        "stream_recovery_required",
        {"reason": "run_status_commit_failed"},
    )
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.cleanup.assert_awaited_once_with(record.run_id, delay=60)
    terminal_events = await event_store.list_events(
        record.thread_id,
        record.run_id,
        event_types=["run.terminal"],
        user_id=record.user_id,
    )
    assert terminal_events == []


@pytest.mark.anyio
async def test_run_agent_does_not_overwrite_competing_terminal_status_after_lease_cas_failure():
    class CompetingTerminalStore(MemoryRunStore):
        async def complete_run(self, run_id, *args, **kwargs):
            run = self._runs[run_id]
            run["status"] = RunStatus.error.value
            run["terminal_reason"] = "lease_expired_recovered"
            self._active_slots.pop(run["thread_id"], None)
            return False

    store = CompetingTerminalStore()
    run_manager = RunManager(store=store)
    record = await run_manager.create_or_reject("thread-terminal-owner-race")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": [AIMessage(content="done")]}

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == RunStatus.error.value
    assert stored["terminal_reason"] == "lease_expired_recovered"


@pytest.mark.anyio
async def test_run_agent_does_not_write_checkpoint_or_thread_meta_after_terminal_cas_failure():
    class CompetingTerminalStore(MemoryRunStore):
        async def complete_run(self, run_id, *args, **kwargs):
            run = self._runs[run_id]
            run["status"] = RunStatus.error.value
            run["terminal_reason"] = "lease_expired_recovered"
            self._active_slots.pop(run["thread_id"], None)
            return False

    checkpoint = SimpleNamespace(
        config={"configurable": {"checkpoint_id": "checkpoint-before-run"}},
        checkpoint={"channel_values": {"title": "stale worker title"}},
        metadata={},
        pending_writes=[],
    )
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(return_value=checkpoint),
        aput=AsyncMock(),
    )
    thread_store = SimpleNamespace(
        get=AsyncMock(return_value={"display_name": "new owner title"}),
        update_display_name=AsyncMock(),
        update_status=AsyncMock(),
    )
    store = CompetingTerminalStore()
    run_manager = RunManager(store=store)
    record = await run_manager.create_or_reject(
        "thread-terminal-side-effect-race",
        user_id="owner-1",
    )
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": [AIMessage(content="done")]}

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(
            checkpointer=checkpointer,
            event_store=MemoryRunEventStore(),
            thread_store=thread_store,
        ),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )

    assert checkpointer.aget_tuple.await_count == 1
    checkpointer.aput.assert_not_awaited()
    thread_store.get.assert_not_awaited()
    thread_store.update_display_name.assert_not_awaited()
    thread_store.update_status.assert_not_awaited()


@pytest.mark.anyio
async def test_terminal_thread_status_update_serializes_with_replacement_run():
    class BlockingThreadStore:
        def __init__(self):
            self.status = "running"
            self.update_started = asyncio.Event()
            self.allow_update = asyncio.Event()

        async def update_status(self, thread_id, status, *, user_id=None):
            self.update_started.set()
            await self.allow_update.wait()
            self.status = status

    store = MemoryRunStore()
    run_manager = RunManager(store=store)
    record = await run_manager.create_or_reject(
        "thread-terminal-replacement-race",
        user_id="owner-a",
    )
    thread_store = BlockingThreadStore()
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": [AIMessage(content="done")]}

    worker = asyncio.create_task(
        run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None, thread_store=thread_store),
            agent_factory=lambda *, config: DummyAgent(),
            graph_input={},
            config={},
        )
    )
    await thread_store.update_started.wait()
    replacement_task = asyncio.create_task(
        run_manager.create_or_reject(
            "thread-terminal-replacement-race",
            user_id="owner-a",
        )
    )
    await asyncio.sleep(0)
    assert not replacement_task.done()

    thread_store.allow_update.set()
    await worker
    replacement = await replacement_task
    await thread_store.update_status(
        "thread-terminal-replacement-race",
        "running",
        user_id="owner-a",
    )

    assert replacement.run_id != record.run_id
    assert thread_store.status == "running"


@pytest.mark.anyio
async def test_run_agent_stops_before_agent_factory_when_running_transition_is_rejected():
    class RejectRunningManager(RunManager):
        async def set_status(self, run_id, status, **kwargs):
            if status == RunStatus.running:
                return False
            return await super().set_status(run_id, status, **kwargs)

    run_manager = RejectRunningManager()
    record = await run_manager.create("thread-rejected-start")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    factory_called = False

    def agent_factory(*, config):
        nonlocal factory_called
        factory_called = True
        raise AssertionError("agent factory must not run without a committed running transition")

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=agent_factory,
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    assert factory_called is False
    bridge.publish.assert_awaited_once_with(
        record.run_id,
        "stream_recovery_required",
        {"reason": "run_status_commit_failed"},
    )
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.cleanup.assert_awaited_once_with(record.run_id, delay=60)


@pytest.mark.anyio
async def test_run_agent_does_not_revive_run_after_active_lease_recovery():
    store = MemoryRunStore()
    run_manager = RunManager(store=store)
    record = await run_manager.create_or_reject("thread-lease-recovered-before-start")
    lease = store._active_slots[record.thread_id]
    now = datetime.now(UTC)
    store._active_slots[record.thread_id] = replace(lease, lease_expires_at=now - timedelta(seconds=1))
    assert await store.recover_expired_lease(
        record.run_id,
        generation=lease.generation,
        now=now,
        error="worker lease expired before start",
    )
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    factory_called = False

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": [AIMessage(content="must not execute")]}

    def agent_factory(*, config):
        nonlocal factory_called
        factory_called = True
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=agent_factory,
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == RunStatus.error.value
    assert stored["terminal_reason"] == "lease_expired_recovered"
    assert record.status == RunStatus.pending
    assert factory_called is False
    bridge.publish.assert_awaited_once_with(
        record.run_id,
        "stream_recovery_required",
        {"reason": "run_status_commit_failed"},
    )
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.cleanup.assert_awaited_once_with(record.run_id, delay=60)


@pytest.mark.anyio
async def test_run_agent_does_not_start_with_expired_unrecovered_lease():
    store = MemoryRunStore()
    run_manager = RunManager(store=store)
    record = await run_manager.create_or_reject("thread-lease-expired-before-start")
    lease = store._active_slots[record.thread_id]
    store._active_slots[record.thread_id] = replace(
        lease,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    factory_called = False

    def agent_factory(*, config):
        nonlocal factory_called
        factory_called = True
        raise AssertionError("expired lease must prevent agent startup")

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=agent_factory,
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    assert record.status == RunStatus.pending
    assert factory_called is False
    bridge.publish.assert_awaited_once_with(
        record.run_id,
        "stream_recovery_required",
        {"reason": "run_status_commit_failed"},
    )
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.cleanup.assert_awaited_once_with(record.run_id, delay=60)


@pytest.mark.anyio
async def test_rollback_cancelled_during_snapshot_capture_fails_without_deleting_thread():
    run_manager = RunManager()
    record = await run_manager.create(
        "thread-rollback-capture",
        user_id="owner-1",
    )
    record.abort_action = "rollback"
    checkpointer = SimpleNamespace(
        aget_tuple=AsyncMock(side_effect=[asyncio.CancelledError(), None]),
        adelete_thread=AsyncMock(),
    )
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=checkpointer),
        agent_factory=lambda *, config: None,
        graph_input={},
        config={},
    )

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.terminal_reason == "rollback_failed"
    assert "snapshot capture failed" in (fetched.error or "")
    checkpointer.adelete_thread.assert_not_awaited()
    terminal_payloads = [call.args[2] for call in bridge.publish.await_args_list if call.args[1] == "custom" and call.args[2].get("type") == "run.terminal"]
    assert [payload["terminal_reason"] for payload in terminal_payloads] == ["rollback_failed"]


@pytest.mark.anyio
async def test_run_agent_defaults_root_run_name_from_assistant_id():
    run_manager = RunManager()
    record = await run_manager.create("thread-1", assistant_id="lead_agent")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    captured: dict[str, object] = {}

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            captured["astream_run_name"] = config["run_name"]
            yield {"messages": []}

    def factory(*, config):
        captured["factory_run_name"] = config["run_name"]
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=factory,
        graph_input={},
        config={},
    )

    assert captured["factory_run_name"] == "lead_agent"
    assert captured["astream_run_name"] == "lead_agent"


@pytest.mark.anyio
async def test_run_agent_defaults_root_run_name_from_context_agent_name():
    run_manager = RunManager()
    record = await run_manager.create("thread-1", assistant_id="lead_agent")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    captured: dict[str, object] = {}

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            captured["astream_run_name"] = config["run_name"]
            yield {"messages": []}

    def factory(*, config):
        captured["factory_run_name"] = config["run_name"]
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=factory,
        graph_input={},
        config={"context": {"agent_name": "finalis"}},
    )

    assert captured["factory_run_name"] == "finalis"
    assert captured["astream_run_name"] == "finalis"


@pytest.mark.anyio
async def test_run_agent_defaults_root_run_name_from_configurable_agent_name():
    run_manager = RunManager()
    record = await run_manager.create("thread-1", assistant_id="lead_agent")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    captured: dict[str, object] = {}

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            captured["astream_run_name"] = config["run_name"]
            yield {"messages": []}

    def factory(*, config):
        captured["factory_run_name"] = config["run_name"]
        return DummyAgent()

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=factory,
        graph_input={},
        config={"configurable": {"agent_name": "finalis"}},
    )

    assert captured["factory_run_name"] == "finalis"
    assert captured["astream_run_name"] == "finalis"


@pytest.mark.anyio
async def test_rollback_restores_snapshot_without_deleting_thread():
    restored_config = owner_checkpoint_config(
        "thread-1",
        "owner-1",
        checkpoint_ns="",
        checkpoint_id="restored-1",
    )
    checkpointer = FakeCheckpointer(put_result=restored_config)

    await _rollback_to_pre_run_checkpoint(
        checkpointer=checkpointer,
        thread_id="thread-1",
        user_id="owner-1",
        run_id="run-1",
        pre_run_checkpoint_id="ckpt-1",
        pre_run_snapshot={
            "checkpoint_ns": "",
            "checkpoint": {
                "id": "ckpt-1",
                "channel_versions": {"messages": 3},
                "channel_values": {"messages": ["before"]},
            },
            "metadata": {"source": "input"},
            "pending_writes": [
                ("task-a", "messages", {"content": "first"}),
                ("task-a", "status", "done"),
                ("task-b", "events", {"type": "tool"}),
            ],
        },
        snapshot_capture_failed=False,
    )

    checkpointer.adelete_thread.assert_not_awaited()
    checkpointer.aput.assert_awaited_once()
    restore_config, restored_checkpoint, restored_metadata, new_versions = checkpointer.aput.await_args.args
    assert restore_config == owner_checkpoint_config(
        "thread-1",
        "owner-1",
        checkpoint_ns="",
    )
    assert restored_checkpoint["id"] != "ckpt-1"
    assert "channel_versions" in restored_checkpoint
    assert "channel_values" in restored_checkpoint
    assert restored_checkpoint["channel_versions"] == {"messages": 3}
    assert restored_checkpoint["channel_values"] == {"messages": ["before"]}
    assert restored_metadata == {"source": "input"}
    assert new_versions == {"messages": 3}
    assert checkpointer.aput_writes.await_args_list == [
        call(
            restored_config,
            [("messages", {"content": "first"}), ("status", "done")],
            task_id="task-a",
        ),
        call(
            restored_config,
            [("events", {"type": "tool"})],
            task_id="task-b",
        ),
    ]


@pytest.mark.anyio
async def test_rollback_restored_checkpoint_becomes_latest_with_real_checkpointer():
    checkpointer = InMemorySaver()
    thread_config = owner_checkpoint_config(
        "thread-1",
        "owner-1",
        checkpoint_ns="",
    )
    before_checkpoint = _make_checkpoint("0001", ["before"], 1)
    before_config = checkpointer.put(thread_config, before_checkpoint, {"step": 1}, {"messages": 1})
    after_checkpoint = _make_checkpoint("0002", ["after"], 2)
    after_config = checkpointer.put(before_config, after_checkpoint, {"step": 2}, {"messages": 2})
    checkpointer.put_writes(after_config, [("messages", "pending-after")], task_id="task-after")

    await _rollback_to_pre_run_checkpoint(
        checkpointer=checkpointer,
        thread_id="thread-1",
        user_id="owner-1",
        run_id="run-1",
        pre_run_checkpoint_id="0001",
        pre_run_snapshot={
            "checkpoint_ns": "",
            "checkpoint": before_checkpoint,
            "metadata": {"step": 1},
            "pending_writes": [("task-before", "messages", "pending-before")],
        },
        snapshot_capture_failed=False,
    )

    latest = checkpointer.get_tuple(thread_config)

    assert latest is not None
    assert latest.config["configurable"]["checkpoint_id"] != "0001"
    assert latest.config["configurable"]["checkpoint_id"] != "0002"
    assert latest.checkpoint["channel_values"] == {"messages": ["before"]}
    assert latest.pending_writes == [("task-before", "messages", "pending-before")]
    assert ("task-after", "messages", "pending-after") not in latest.pending_writes


@pytest.mark.anyio
async def test_rollback_deletes_thread_when_no_snapshot_exists():
    checkpointer = FakeCheckpointer(put_result=None)

    await _rollback_to_pre_run_checkpoint(
        checkpointer=checkpointer,
        thread_id="thread-1",
        user_id="owner-1",
        run_id="run-1",
        pre_run_checkpoint_id=None,
        pre_run_snapshot=None,
        snapshot_capture_failed=False,
    )

    checkpointer.adelete_thread.assert_awaited_once_with(owner_checkpoint_thread_id("thread-1", "owner-1"))
    checkpointer.aput.assert_not_awaited()
    checkpointer.aput_writes.assert_not_awaited()


@pytest.mark.anyio
async def test_rollback_raises_when_restore_config_has_no_checkpoint_id():
    checkpointer = FakeCheckpointer(
        put_result=owner_checkpoint_config(
            "thread-1",
            "owner-1",
            checkpoint_ns="",
        )
    )

    with pytest.raises(RuntimeError, match="did not return checkpoint_id"):
        await _rollback_to_pre_run_checkpoint(
            checkpointer=checkpointer,
            thread_id="thread-1",
            user_id="owner-1",
            run_id="run-1",
            pre_run_checkpoint_id="ckpt-1",
            pre_run_snapshot={
                "checkpoint_ns": "",
                "checkpoint": {"id": "ckpt-1", "channel_versions": {}},
                "metadata": {},
                "pending_writes": [("task-a", "messages", "value")],
            },
            snapshot_capture_failed=False,
        )

    checkpointer.adelete_thread.assert_not_awaited()
    checkpointer.aput.assert_awaited_once()
    checkpointer.aput_writes.assert_not_awaited()


@pytest.mark.anyio
async def test_rollback_normalizes_none_checkpoint_ns_to_root_namespace():
    checkpointer = FakeCheckpointer(
        put_result=owner_checkpoint_config(
            "thread-1",
            "owner-1",
            checkpoint_ns="",
            checkpoint_id="restored-1",
        )
    )

    await _rollback_to_pre_run_checkpoint(
        checkpointer=checkpointer,
        thread_id="thread-1",
        user_id="owner-1",
        run_id="run-1",
        pre_run_checkpoint_id="ckpt-1",
        pre_run_snapshot={
            "checkpoint_ns": None,
            "checkpoint": {"id": "ckpt-1", "channel_versions": {}},
            "metadata": {},
            "pending_writes": [],
        },
        snapshot_capture_failed=False,
    )

    checkpointer.aput.assert_awaited_once()
    restore_config, restored_checkpoint, restored_metadata, new_versions = checkpointer.aput.await_args.args
    assert restore_config == owner_checkpoint_config(
        "thread-1",
        "owner-1",
        checkpoint_ns="",
    )
    assert restored_checkpoint["id"] != "ckpt-1"
    assert restored_checkpoint["channel_versions"] == {}
    assert restored_metadata == {}
    assert new_versions == {}


@pytest.mark.anyio
async def test_rollback_raises_on_malformed_pending_write_not_a_tuple():
    """pending_writes containing a non-3-tuple item should raise RuntimeError."""
    checkpointer = FakeCheckpointer(put_result={"configurable": {"thread_id": "thread-1", "checkpoint_ns": "", "checkpoint_id": "restored-1"}})

    with pytest.raises(RuntimeError, match="rollback failed: pending_write is not a 3-tuple"):
        await _rollback_to_pre_run_checkpoint(
            checkpointer=checkpointer,
            thread_id="thread-1",
            user_id="owner-1",
            run_id="run-1",
            pre_run_checkpoint_id="ckpt-1",
            pre_run_snapshot={
                "checkpoint_ns": "",
                "checkpoint": {"id": "ckpt-1", "channel_versions": {}},
                "metadata": {},
                "pending_writes": [
                    ("task-a", "messages", "valid"),  # valid
                    ["only", "two"],  # malformed: only 2 elements
                ],
            },
            snapshot_capture_failed=False,
        )

    # aput succeeded but aput_writes should not be called due to malformed data
    checkpointer.aput.assert_awaited_once()
    checkpointer.aput_writes.assert_not_awaited()


@pytest.mark.anyio
async def test_rollback_raises_on_malformed_pending_write_non_string_channel():
    """pending_writes containing a non-string channel should raise RuntimeError."""
    checkpointer = FakeCheckpointer(put_result={"configurable": {"thread_id": "thread-1", "checkpoint_ns": "", "checkpoint_id": "restored-1"}})

    with pytest.raises(RuntimeError, match="rollback failed: pending_write has non-string channel"):
        await _rollback_to_pre_run_checkpoint(
            checkpointer=checkpointer,
            thread_id="thread-1",
            user_id="owner-1",
            run_id="run-1",
            pre_run_checkpoint_id="ckpt-1",
            pre_run_snapshot={
                "checkpoint_ns": "",
                "checkpoint": {"id": "ckpt-1", "channel_versions": {}},
                "metadata": {},
                "pending_writes": [
                    ("task-a", 123, "value"),  # malformed: channel is not a string
                ],
            },
            snapshot_capture_failed=False,
        )

    checkpointer.aput.assert_awaited_once()
    checkpointer.aput_writes.assert_not_awaited()


@pytest.mark.anyio
async def test_rollback_propagates_aput_writes_failure():
    """If aput_writes fails, the exception should propagate (not be swallowed)."""
    checkpointer = FakeCheckpointer(put_result={"configurable": {"thread_id": "thread-1", "checkpoint_ns": "", "checkpoint_id": "restored-1"}})
    # Simulate aput_writes failure
    checkpointer.aput_writes.side_effect = RuntimeError("Database connection lost")

    with pytest.raises(RuntimeError, match="Database connection lost"):
        await _rollback_to_pre_run_checkpoint(
            checkpointer=checkpointer,
            thread_id="thread-1",
            user_id="owner-1",
            run_id="run-1",
            pre_run_checkpoint_id="ckpt-1",
            pre_run_snapshot={
                "checkpoint_ns": "",
                "checkpoint": {"id": "ckpt-1", "channel_versions": {}},
                "metadata": {},
                "pending_writes": [
                    ("task-a", "messages", "value"),
                ],
            },
            snapshot_capture_failed=False,
        )

    # aput succeeded, aput_writes was called but failed
    checkpointer.aput.assert_awaited_once()
    checkpointer.aput_writes.assert_awaited_once()


def test_agent_factory_supports_app_config_detects_supported_signature():
    def factory(*, config, app_config=None):
        return (config, app_config)

    assert _agent_factory_supports_app_config(factory) is True


def test_build_runtime_context_defaults_to_thread_and_run_id():
    ctx = _build_runtime_context("thread-1", "run-1", None)
    assert ctx == {"thread_id": "thread-1", "run_id": "run-1"}


def test_build_runtime_context_merges_caller_context():
    """Regression for issue #2677: keys from ``config['context']`` (e.g. ``agent_name``)
    must be merged into the Runtime's context so that ``ToolRuntime.context`` — which
    is what ``setup_agent`` reads — can see them."""
    caller_context = {"agent_name": "my-agent", "is_bootstrap": True, "model_name": "gpt-4"}

    ctx = _build_runtime_context("thread-1", "run-1", caller_context)

    assert ctx["thread_id"] == "thread-1"
    assert ctx["run_id"] == "run-1"
    assert ctx["agent_name"] == "my-agent"
    assert ctx["is_bootstrap"] is True
    assert ctx["model_name"] == "gpt-4"


def test_build_runtime_context_caller_cannot_override_thread_id_or_run_id():
    """A malicious or buggy caller must not be able to overwrite the worker-assigned
    ``thread_id`` / ``run_id`` by stuffing them into ``config['context']``."""
    caller_context = {"thread_id": "spoofed", "run_id": "spoofed", "agent_name": "ok"}

    ctx = _build_runtime_context("real-thread", "real-run", caller_context)

    assert ctx["thread_id"] == "real-thread"
    assert ctx["run_id"] == "real-run"
    assert ctx["agent_name"] == "ok"


def test_build_runtime_context_ignores_non_dict_caller_context():
    ctx = _build_runtime_context("thread-1", "run-1", "not-a-dict")
    assert ctx == {"thread_id": "thread-1", "run_id": "run-1"}


def test_agent_factory_supports_app_config_returns_false_when_signature_lookup_fails(monkeypatch):
    class BrokenCallable:
        def __call__(self, **kwargs):
            return kwargs

    monkeypatch.setattr("deerflow.runtime.runs.worker.inspect.signature", lambda _obj: (_ for _ in ()).throw(ValueError("boom")))

    assert _agent_factory_supports_app_config(BrokenCallable()) is False


# ---------------------------------------------------------------------------
# _extract_llm_error_fallback_message coverage
# ---------------------------------------------------------------------------


def test_try_extract_from_message_finds_fallback_on_message_object():
    msg = AIMessage(
        content="fallback",
        additional_kwargs={
            "deerflow_error_fallback": True,
            "error_detail": "Connection error.",
            "error_reason": "transient",
        },
    )
    assert _try_extract_from_message(msg) == "Connection error."


def test_try_extract_from_message_finds_fallback_on_dict():
    msg = {
        "content": "fallback",
        "additional_kwargs": {
            "deerflow_error_fallback": True,
            "error_detail": "Quota exceeded.",
        },
    }
    assert _try_extract_from_message(msg) == "Quota exceeded."


def test_try_extract_from_message_returns_none_for_normal_message():
    msg = AIMessage(content="hello")
    assert _try_extract_from_message(msg) is None


def test_extract_llm_error_fallback_message_large_state_chunk_no_fallback():
    """Normal-size state dict without fallback markers must not raise and should return None."""
    large_state = {
        "messages": [
            AIMessage(content="Hello!"),
            {"role": "user", "content": "Hi there"},
        ],
        "foo": "x" * 10_000,
        "bar": {"nested": {"deep": {"data": list(range(1000))}}},
        "baz": [{"id": i, "payload": "y" * 1000} for i in range(500)],
    }
    assert _extract_llm_error_fallback_message(large_state) is None


def test_extract_llm_error_fallback_message_finds_fallback_in_messages_list():
    state = {
        "messages": [
            AIMessage(content="Hello!"),
            AIMessage(
                content="Unavailable.",
                additional_kwargs={
                    "deerflow_error_fallback": True,
                    "error_detail": "Connection error.",
                },
            ),
        ],
        "other_state": "large_value" * 1000,
    }
    assert _extract_llm_error_fallback_message(state) == "Connection error."


def test_extract_llm_error_fallback_message_finds_fallback_in_raw_message():
    msg = AIMessage(
        content="Unavailable.",
        additional_kwargs={
            "deerflow_error_fallback": True,
            "error_reason": "quota",
        },
    )
    assert _extract_llm_error_fallback_message(msg) == "quota"


def test_extract_llm_error_fallback_message_finds_fallback_in_tuple():
    item = (
        "messages",
        AIMessage(
            content="Unavailable.",
            additional_kwargs={
                "deerflow_error_fallback": True,
                "error_detail": "Circuit open.",
            },
        ),
    )
    assert _extract_llm_error_fallback_message(item) == "Circuit open."


def test_extract_llm_error_fallback_message_returns_none_for_empty_values():
    assert _extract_llm_error_fallback_message({}) is None
    assert _extract_llm_error_fallback_message([]) is None
    assert _extract_llm_error_fallback_message(None) is None
    assert _extract_llm_error_fallback_message("string") is None


def test_extract_llm_error_fallback_message_finds_fallback_in_updates_mode():
    """stream_mode='updates' yields dicts keyed by node name (e.g. {'call_model': {...}}).
    Fallback marker is nested inside the node's state update, not at the top level."""
    update_chunk = {
        "call_model": {
            "messages": [
                AIMessage(
                    content="Unavailable.",
                    additional_kwargs={
                        "deerflow_error_fallback": True,
                        "error_detail": "Connection error.",
                    },
                )
            ]
        }
    }
    assert _extract_llm_error_fallback_message(update_chunk) == "Connection error."


def test_extract_llm_error_fallback_message_updates_mode_no_fallback():
    """Normal updates chunk without any fallback should return None safely."""
    update_chunk = {
        "__interrupt__": [
            {
                "value": "ask_human",
                "resumable": True,
                "ns": ["agent"],
                "when": "during",
            }
        ]
    }
    assert _extract_llm_error_fallback_message(update_chunk) is None
