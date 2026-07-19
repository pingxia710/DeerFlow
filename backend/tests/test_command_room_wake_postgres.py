from __future__ import annotations

import asyncio
import ipaddress
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import deerflow.persistence.models  # noqa: F401
from app.gateway import command_room_background as background_module
from app.gateway import services
from deerflow.persistence.base import Base
from deerflow.persistence.round_state import RoundStateRepository
from deerflow.persistence.run import RunRepository
from deerflow.runtime.background_tasks import CommandRoomBackgroundJob, CommandRoomBackgroundOutcome
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus, WakeAdmissionOutcome, WakeAdmissionResult

_LOOPBACK_POSTGRES_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_LEASE_TTL = timedelta(seconds=30)


def _postgres_url() -> str:
    url = os.getenv("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL")
    if not url:
        pytest.fail("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL is required for the Postgres wake contract check")
    try:
        parsed = make_url(url)
    except Exception:
        pytest.fail("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL must be a valid postgresql+asyncpg URL")
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("Postgres wake contract check requires the postgresql+asyncpg driver")
    if parsed.host not in _LOOPBACK_POSTGRES_HOSTS or "host" in parsed.query:
        pytest.fail("Postgres wake contract check requires an explicit loopback TCP host")
    return url


def _require_postgres_url() -> str:
    """Return the optional local integration URL or skip integration checks."""
    if not os.getenv("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL"):
        pytest.skip("set DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL to run the Postgres wake contract checks")
    return _postgres_url()


@pytest.mark.parametrize(
    "url",
    (
        "postgresql+asyncpg://role@127.0.0.1/wake",
        "postgresql+asyncpg://role@[::1]/wake",
        "postgresql+asyncpg://role@localhost/wake",
    ),
)
def test_postgres_url_accepts_explicit_loopback_tcp_hosts(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL", url)
    assert _postgres_url() == url


@pytest.mark.parametrize(
    "url",
    (
        None,
        "sqlite+aiosqlite:///wake",
        "postgresql+asyncpg:///wake",
        "postgresql+asyncpg://role@198.51.100.1/wake",
        "postgresql+asyncpg://role@localhost/wake?host=/tmp",
    ),
)
def test_postgres_url_rejects_unsafe_target(monkeypatch: pytest.MonkeyPatch, url: str | None) -> None:
    if url is None:
        monkeypatch.delenv("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL", raising=False)
    else:
        monkeypatch.setenv("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL", url)
    with pytest.raises(pytest.fail.Exception):
        _postgres_url()


async def _gateway(url: str, worker_id: str):
    engine = create_async_engine(url)
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "asyncpg"
    repository = RunRepository(async_sessionmaker(engine, expire_on_commit=False))
    round_store = RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False))
    manager = RunManager(repository, worker_id=worker_id)
    service = background_module.CommandRoomBackgroundService()
    app = SimpleNamespace(state=SimpleNamespace(gateway_id=worker_id, run_manager=manager, round_state_store=round_store))
    snapshot = background_module._RequestSnapshot(app=app, headers=[], state={})
    return engine, repository, round_store, manager, service, app, snapshot


async def _assert_utc_session(connection) -> None:
    timezone = await connection.scalar(text("SHOW TimeZone"))
    assert timezone == "UTC", f"Postgres wake contract requires UTC session TimeZone, got {timezone!r}"


async def _assert_loopback_server_and_utc(engine) -> None:
    async with engine.connect() as connection:
        server_address = await connection.scalar(text("SELECT inet_server_addr()"))
        await _assert_utc_session(connection)
    assert server_address is not None
    assert ipaddress.ip_address(str(server_address)).is_loopback


async def _assert_single_canonical_run(engine, wake_id: str, *, expected_run_id: str | None = None) -> str:
    async with engine.connect() as connection:
        count = await connection.scalar(
            text("SELECT COUNT(*) FROM runs WHERE command_room_wake_id = :wake_id"),
            {"wake_id": wake_id},
        )
        assert count == 1
        run_id = await connection.scalar(
            text("SELECT run_id FROM runs WHERE command_room_wake_id = :wake_id"),
            {"wake_id": wake_id},
        )
    assert isinstance(run_id, str)
    if expected_run_id is not None:
        assert run_id == expected_run_id
    return run_id


@pytest.mark.anyio
async def test_postgres_wake_contract_rejects_non_utc_session() -> None:
    engine, *_ = await _gateway(_require_postgres_url(), "wake-gateway-non-utc")
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TIME ZONE 'America/New_York'"))
            with pytest.raises(AssertionError, match="requires UTC session TimeZone"):
                await _assert_utc_session(connection)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_postgres_gateway_wake_lease_fences_competition_expiry_and_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _require_postgres_url()
    first_engine, first_repository, first_round_store, _first_manager, first_service, first_app, first_snapshot = await _gateway(url, "wake-gateway-one")
    second_engine, second_repository, second_round_store, _second_manager, second_service, second_app, second_snapshot = await _gateway(url, "wake-gateway-two")
    restarted_engine = None
    restarted_service = None
    second_wake_task = None
    run_id = None
    release_agent = asyncio.Event()
    agent_started = asyncio.Event()
    agent_finished = asyncio.Event()
    start_run_entries: list[tuple[str, str]] = []
    start_run_outcomes: list[tuple[str, WakeAdmissionOutcome]] = []
    start_run_returned = {
        "wake-gateway-one": asyncio.Event(),
        "wake-gateway-two": asyncio.Event(),
        "wake-gateway-one-restarted": asyncio.Event(),
    }
    agent_starts: list[str] = []
    completion_results: list[bool] = []

    async def get_thread(*_args, **_kwargs):
        return {"thread_id": "wake-thread"}

    async def update_thread_status(*_args, **_kwargs):
        return None

    async def no_checkpoint(*_args, **_kwargs):
        return None

    async def run_agent(_bridge, manager, record, **_kwargs):
        agent_starts.append(record.metadata["command_room_wake_id"])
        agent_started.set()
        await release_agent.wait()
        completion_results.append(await manager.set_status(record.run_id, RunStatus.success, terminal_reason="completed"))
        agent_finished.set()

    original_start_run = services.start_run

    async def count_start_run(*args, **kwargs):
        request = args[2]
        gateway_id = request.app.state.gateway_id
        admission = kwargs["command_room_wake_admission"]
        start_run_entries.append((gateway_id, admission.wake_id))
        result = await original_start_run(*args, **kwargs)
        assert isinstance(result, WakeAdmissionResult)
        start_run_outcomes.append((gateway_id, result.outcome))
        start_run_returned[gateway_id].set()
        return result

    thread_store = SimpleNamespace(get=get_thread, update_status=update_thread_status)
    monkeypatch.setattr(services, "get_stream_bridge", lambda _request: SimpleNamespace())
    monkeypatch.setattr(services, "get_run_manager", lambda request: request.app.state.run_manager)
    monkeypatch.setattr(services, "get_run_context", lambda _request: SimpleNamespace(thread_store=thread_store, event_store=None))
    monkeypatch.setattr(services, "build_run_config", lambda *_args, **_kwargs: {"configurable": {}})
    monkeypatch.setattr(services, "owner_checkpoint_config", lambda *_args, **_kwargs: {"configurable": {}})
    monkeypatch.setattr(services, "apply_checkpoint_to_run_config", no_checkpoint)
    monkeypatch.setattr(services, "merge_run_context_overrides", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(services, "inject_authenticated_user_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(services, "normalize_input", lambda value: value)
    monkeypatch.setattr(services, "normalize_stream_modes", lambda _value: [])
    monkeypatch.setattr(services, "resolve_agent_factory", lambda _assistant_id: object())
    monkeypatch.setattr(services, "run_agent", run_agent)
    monkeypatch.setattr(services, "start_run", count_start_run)

    wake_id = str(uuid4())
    thread_id = f"wake-thread-{uuid4().hex}"
    source_run_id = f"wake-source-{uuid4().hex}"
    task_id = f"wake-task-{uuid4().hex}"
    job = CommandRoomBackgroundJob(
        thread_id=thread_id,
        source_run_id=source_run_id,
        task_id=task_id,
        description="Postgres wake contract",
        subagent_type="executor",
        execute=lambda: None,
    )
    outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
    try:
        monkeypatch.setattr(background_module, "_WAKE_MAX_ATTEMPTS", 2)
        await _assert_loopback_server_and_utc(first_engine)
        await _assert_loopback_server_and_utc(second_engine)
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        await first_round_store.bind_run(thread_id=thread_id, run_id=source_run_id)
        await first_service._persist_state(
            job,
            first_snapshot,
            outcome=outcome,
            wake={"state": "pending", "attempts": 0, "wake_id": wake_id},
        )
        await first_service.recover(first_app)
        await asyncio.wait_for(agent_started.wait(), timeout=2)
        await asyncio.wait_for(start_run_returned["wake-gateway-one"].wait(), timeout=2)
        assert start_run_entries == [("wake-gateway-one", wake_id)]
        assert start_run_outcomes == [("wake-gateway-one", WakeAdmissionOutcome.LEASE_WON)]
        assert agent_starts == [wake_id]
        assert len(agent_starts) == 1

        await second_service.recover(second_app)
        assert not second_service._tasks

        run_id = await _assert_single_canonical_run(first_engine, wake_id)
        stored = await first_repository.get_by_command_room_wake_id(wake_id)
        assert stored is not None
        assert stored["run_id"] == run_id

        await first_service.shutdown()
        second_claim_id = await second_service._claim_wake(job, second_snapshot)
        assert second_claim_id is not None
        second_wake_task = asyncio.create_task(
            second_service._wake_with_claim(job, second_snapshot, outcome, second_claim_id),
        )
        await asyncio.wait_for(start_run_returned["wake-gateway-two"].wait(), timeout=2)
        assert start_run_entries == [("wake-gateway-one", wake_id), ("wake-gateway-two", wake_id)]
        assert start_run_outcomes == [
            ("wake-gateway-one", WakeAdmissionOutcome.LEASE_WON),
            ("wake-gateway-two", WakeAdmissionOutcome.ACTIVE),
        ]
        assert agent_starts == [wake_id]
        assert len(agent_starts) == 1
        assert await _assert_single_canonical_run(second_engine, wake_id, expected_run_id=run_id) == run_id

        lease_expires_at = datetime.fromisoformat(stored["metadata"]["lease_expires_at"])
        lease_heartbeat_at = datetime.fromisoformat(stored["metadata"]["lease_heartbeat_at"])
        assert lease_heartbeat_at.tzinfo is UTC
        assert lease_expires_at.tzinfo is UTC
        assert lease_expires_at - lease_heartbeat_at == _LEASE_TTL
        assert not await second_repository.recover_expired_lease(
            run_id,
            generation=stored["metadata"]["generation"],
            terminal_reason="worker_lost",
            now=lease_heartbeat_at + _LEASE_TTL - timedelta(microseconds=1),
        )
        assert not await second_repository.recover_expired_lease(
            run_id,
            generation=stored["metadata"]["generation"] + 1,
            terminal_reason="worker_lost",
            now=lease_heartbeat_at + _LEASE_TTL + timedelta(microseconds=1),
        )
        assert await second_repository.recover_expired_lease(
            run_id,
            generation=stored["metadata"]["generation"],
            terminal_reason="worker_lost",
            now=lease_heartbeat_at + _LEASE_TTL + timedelta(microseconds=1),
        )
        recovered = await second_repository.get_by_command_room_wake_id(wake_id)
        assert recovered is not None
        assert recovered["status"] == "error"
        assert recovered["metadata"]["terminal_reason"] == "worker_lost"
        assert await _assert_single_canonical_run(second_engine, wake_id, expected_run_id=run_id) == run_id
        second_wake_task.cancel()
        await asyncio.gather(second_wake_task, return_exceptions=True)
        second_wake_task = None
        release_agent.set()
        await asyncio.wait_for(agent_finished.wait(), timeout=2)
        assert completion_results == [False]
        await first_engine.dispose()
        restarted_engine, _restarted_repository, _restarted_round_store, _restarted_manager, restarted_service, _restarted_app, restarted_snapshot = await _gateway(url, "wake-gateway-one-restarted")
        await _assert_loopback_server_and_utc(restarted_engine)
        restarted_claim_id = await restarted_service._claim_wake(job, restarted_snapshot)
        assert restarted_claim_id is not None
        await restarted_service._wake_with_claim(job, restarted_snapshot, outcome, restarted_claim_id)
        assert start_run_entries == [
            ("wake-gateway-one", wake_id),
            ("wake-gateway-two", wake_id),
            ("wake-gateway-one-restarted", wake_id),
        ]
        assert start_run_outcomes == [
            ("wake-gateway-one", WakeAdmissionOutcome.LEASE_WON),
            ("wake-gateway-two", WakeAdmissionOutcome.ACTIVE),
            ("wake-gateway-one-restarted", WakeAdmissionOutcome.TERMINAL_FAILURE),
        ]
        assert agent_starts == [wake_id]
        assert len(agent_starts) == 1
        assert await _assert_single_canonical_run(
            restarted_engine,
            wake_id,
            expected_run_id=run_id,
        )
    finally:
        release_agent.set()
        if second_wake_task is not None:
            second_wake_task.cancel()
            await asyncio.gather(second_wake_task, return_exceptions=True)
        await first_service.shutdown()
        await second_service.shutdown()
        if restarted_service is not None:
            await restarted_service.shutdown()
        if run_id is not None:
            await second_repository.delete(run_id, user_id=None)
        await first_round_store.delete_by_thread(thread_id, user_id=None)
        await first_engine.dispose()
        await second_engine.dispose()
        if restarted_engine is not None:
            await restarted_engine.dispose()
