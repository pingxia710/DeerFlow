"""Command Room filesystem adapters must stay off the Gateway event loop."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import threading
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, Request

from app.gateway.routers import thread_runs, threads
from deerflow.agents.middlewares.round_context_middleware import CommandRoomRoundContextMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.runtime import RunRecord, RunStatus

pytestmark = pytest.mark.asyncio
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


def _request() -> Request:
    user = SimpleNamespace(id=UUID("77777777-7777-7777-7777-777777777777"), system_role="user")
    return Request({"type": "http", "app": FastAPI(), "headers": [], "state": {"user": user}})


def _run_record(thread_id: str, run_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="command-room",
        status=RunStatus.success,
        on_disconnect="cancel",
        round_id="round-1",
    )


async def test_quality_signal_filesystem_write_is_offloaded(tmp_path, monkeypatch) -> None:
    async def resolve(thread_id: str, run_id: str, request: Request, *, user_id: str) -> RunRecord:
        return _run_record(thread_id, run_id)

    def record(signal, *, user_id: str | None = None) -> None:
        (tmp_path / f"{user_id}-{signal.thread_id}.json").write_text(str(signal.as_dict()), encoding="utf-8")

    monkeypatch.setattr(thread_runs, "_resolve_thread_run_for_user", resolve)
    monkeypatch.setattr(thread_runs, "record_quality_signal", record)
    handler = inspect.unwrap(thread_runs.create_quality_signal)

    response = await handler(
        "thread-1",
        "run-1",
        _request(),
        thread_runs.QualitySignalCreateRequest(
            author_role="evidence",
            recommendation="needs_more_evidence",
            rationale="Confirm the command output.",
            capability_snapshot_version=1,
        ),
        AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}),
    )

    assert response.thread_id == "thread-1"


async def test_command_room_mutation_finishes_before_cancellation_escapes() -> None:
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def mutation() -> None:
        started.set()
        release.wait(timeout=2)
        completed.set()

    task = asyncio.create_task(thread_runs._run_command_room_mutation(mutation))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()


async def test_latest_command_room_round_read_is_offloaded(tmp_path, monkeypatch) -> None:
    import deerflow.command_room.round_record as round_record

    def read_round(*, thread_id: str, user_id: str | None) -> dict[str, str]:
        path = tmp_path / f"{user_id}-{thread_id}.json"
        path.write_text('{"round_id":"round-1"}', encoding="utf-8")
        assert path.read_text(encoding="utf-8")
        return {"round_id": "round-1", "thread_id": thread_id}

    monkeypatch.setattr(round_record, "latest_command_room_round", read_round)
    handler = inspect.unwrap(threads.get_latest_command_room_round)

    response = await handler("thread-1", _request())

    assert response.round == {"round_id": "round-1", "thread_id": "thread-1"}


async def test_async_model_context_uses_active_memory_without_persisted_read(monkeypatch) -> None:
    middleware = CommandRoomRoundContextMiddleware(agent_name="command-room")
    request = object()
    calls: list[object] = []

    def inject(value: object) -> object:
        calls.append(value)
        return value

    async def handler(value: object) -> object:
        return value

    monkeypatch.setattr(middleware, "_inject", inject)

    assert await middleware.awrap_model_call(request, handler) is request
    assert calls == [request]


async def test_subagent_handoff_audit_write_is_offloaded(tmp_path, monkeypatch) -> None:
    def record(**kwargs) -> None:
        (tmp_path / f"{kwargs['task_id']}.json").write_text(str(kwargs), encoding="utf-8")

    monkeypatch.setattr(task_tool_module, "record_subagent_handoff", record)

    await task_tool_module._record_subagent_handoff_async(task_id="task-1")

    assert await asyncio.to_thread((tmp_path / "task-1.json").exists)
