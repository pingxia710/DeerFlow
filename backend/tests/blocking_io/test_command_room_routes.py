"""Command Room filesystem adapters must stay off the Gateway event loop."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from deerflow.agents.middlewares.round_context_middleware import CommandRoomRoundContextMiddleware

pytestmark = pytest.mark.asyncio
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


async def test_async_model_context_uses_active_memory_without_persisted_read(monkeypatch) -> None:
    middleware = CommandRoomRoundContextMiddleware(agent_name="command-room")
    request = SimpleNamespace(runtime=SimpleNamespace(context={}), messages=[])
    calls: list[object] = []

    def context_text(value: object) -> None:
        calls.append(value)
        return None

    async def handler(value: object) -> object:
        return value

    monkeypatch.setattr(middleware, "_context_text", context_text)

    assert await middleware.awrap_model_call(request, handler) is request
    assert calls == [request.runtime]


async def test_subagent_handoff_audit_write_is_offloaded(tmp_path, monkeypatch) -> None:
    def record(**kwargs) -> None:
        (tmp_path / f"{kwargs['task_id']}.json").write_text(str(kwargs), encoding="utf-8")

    monkeypatch.setattr(task_tool_module, "record_subagent_handoff", record)

    await task_tool_module._record_subagent_handoff_async(task_id="task-1")

    assert await asyncio.to_thread((tmp_path / "task-1.json").exists)
