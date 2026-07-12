from __future__ import annotations

import asyncio
import importlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.runtime import Runtime

from deerflow.community.aio_sandbox.sandbox_info import SandboxInfo
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.worker import RunContext, run_agent
from deerflow.sandbox.middleware import SandboxMiddleware
from deerflow.sandbox.sandbox_provider import (
    mark_runtime_sandbox_lease,
    reset_sandbox_provider,
    set_sandbox_provider,
)


@pytest.mark.asyncio
async def test_middleware_does_not_release_recreated_id_when_retain_failed() -> None:
    provider = MagicMock()
    provider.retain.return_value = False
    middleware = SandboxMiddleware(lazy_init=True)
    state = {"sandbox": {"sandbox_id": "same-deterministic-id"}}
    runtime = Runtime(context={"thread_id": "thread-1", "user_id": "user-1"})

    set_sandbox_provider(provider)
    try:
        await middleware.abefore_agent(state, runtime)
        # Another caller can recreate the deterministic ID after retain(False).
        # This middleware invocation never owned that new generation's lease.
        await middleware.aafter_agent(state, runtime)
    finally:
        reset_sandbox_provider()

    provider.retain.assert_called_once_with("same-deterministic-id")
    provider.release.assert_not_called()


class _ControlledStream:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.closed = asyncio.Event()
        self._blocker = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.fail:
            raise RuntimeError("stream failed")
        await self._blocker.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed.set()


class _LeaseOwningAgent:
    metadata: dict = {}
    checkpointer = None
    store = None
    interrupt_before_nodes: list[str] = []
    interrupt_after_nodes: list[str] = []

    def __init__(self, *, fail: bool) -> None:
        self.stream = _ControlledStream(fail=fail)
        self.started = asyncio.Event()

    def astream(self, _graph_input, *, config, stream_mode, **_kwargs):
        del stream_mode
        runtime = config["configurable"]["__pregel_runtime"]
        mark_runtime_sandbox_lease(runtime.context, "sandbox-stream")
        self.started.set()
        return self.stream


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["cancel", "error"])
async def test_worker_astream_exit_closes_stream_and_releases_owned_lease(failure_mode: str) -> None:
    provider = MagicMock()
    agent = _LeaseOwningAgent(fail=failure_mode == "error")
    run_manager = RunManager()
    record = await run_manager.create(f"thread-{failure_mode}")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    set_sandbox_provider(provider)
    try:
        task = asyncio.create_task(
            run_agent(
                bridge,
                run_manager,
                record,
                ctx=RunContext(checkpointer=None),
                agent_factory=lambda *, config: agent,
                graph_input={"messages": []},
                config={},
            )
        )
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        if failure_mode == "cancel":
            task.cancel()
        await asyncio.wait_for(task, timeout=2)
        await asyncio.sleep(0)
    finally:
        reset_sandbox_provider()

    assert agent.stream.closed.is_set()
    provider.release.assert_called_once_with("sandbox-stream")


def _active_provider(*, lease_count: int):
    aio_module = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_module.AioSandboxProvider.__new__(aio_module.AioSandboxProvider)
    sandbox_id = "sandbox-idle"
    sandbox = MagicMock()
    info = SandboxInfo(
        sandbox_id=sandbox_id,
        sandbox_url="http://sandbox",
        sandbox_api_key="scoped-key",
        status="Running",
        ready=True,
    )
    provider._lock = threading.Lock()
    provider._sandboxes = {sandbox_id: sandbox}
    provider._sandbox_infos = {sandbox_id: info}
    provider._thread_sandboxes = {("user-1", "thread-1"): sandbox_id}
    provider._last_activity = {sandbox_id: 0.0}
    provider._lease_counts = {sandbox_id: lease_count} if lease_count else {}
    provider._warm_pool = {}
    provider._backend = MagicMock()
    return aio_module, provider, sandbox_id, sandbox


def test_idle_sweeper_skips_sandbox_with_active_lease() -> None:
    aio_module, provider, sandbox_id, sandbox = _active_provider(lease_count=1)

    with patch.object(aio_module.time, "time", return_value=100.0):
        provider._cleanup_idle_sandboxes(idle_timeout=1.0)

    assert provider._sandboxes[sandbox_id] is sandbox
    sandbox.close.assert_not_called()
    provider._backend.destroy.assert_not_called()


def test_idle_sweeper_rechecks_lease_before_final_destroy() -> None:
    aio_module, provider, sandbox_id, sandbox = _active_provider(lease_count=0)

    class LeaseArrivesDuringRecheck(dict):
        def get(self, key, default=None):
            provider._lease_counts[sandbox_id] = 1
            return super().get(key, default)

    provider._last_activity = LeaseArrivesDuringRecheck(provider._last_activity)
    with patch.object(aio_module.time, "time", return_value=100.0):
        provider._cleanup_idle_sandboxes(idle_timeout=1.0)

    assert provider._sandboxes[sandbox_id] is sandbox
    sandbox.close.assert_not_called()
    provider._backend.destroy.assert_not_called()
