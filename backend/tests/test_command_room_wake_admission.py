from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import deerflow.persistence.models  # noqa: F401
from deerflow.persistence.base import Base
from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade
from deerflow.persistence.run import RunRepository
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import (
    CommandRoomWakeAdmission,
    CommandRoomWakeIdentityConflict,
    WakeAdmissionOutcome,
)


def _admission(
    *,
    wake_id: str,
    metadata: dict | None = None,
    kwargs: dict | None = None,
    user_id: str | None = "user-1",
    source_run_id: str = "source-run",
    source_task_id: str = "source-task",
    thread_id: str = "thread-1",
) -> CommandRoomWakeAdmission:
    return CommandRoomWakeAdmission(
        wake_id=wake_id,
        thread_id=thread_id,
        user_id=user_id,
        assistant_id="command-room",
        source_run_id=source_run_id,
        source_task_id=source_task_id,
        metadata=metadata or {"sibling": "first"},
        kwargs=kwargs or {"input": {"messages": [{"content": "first"}]}},
    )


async def _manager_pair(tmp_path: Path) -> tuple[object, object, RunManager, RunManager]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'wake-admission.db').as_posix()}"
    first_engine = create_async_engine(url, connect_args={"timeout": 30})
    second_engine = create_async_engine(url, connect_args={"timeout": 30})
    async with first_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    first = RunManager(RunRepository(async_sessionmaker(first_engine, expire_on_commit=False)))
    second = RunManager(RunRepository(async_sessionmaker(second_engine, expire_on_commit=False)))
    return first_engine, second_engine, first, second


async def _insert_legacy_wake_runs(engine, rows: tuple[tuple[str, str, dict], ...]) -> None:
    async with engine.begin() as connection:
        for run_id, thread_id, metadata in rows:
            await connection.execute(
                sa.text(
                    "INSERT INTO runs ("
                    "run_id, thread_id, status, multitask_strategy, metadata_json, kwargs_json, "
                    "message_count, total_input_tokens, total_output_tokens, total_tokens, "
                    "llm_call_count, lead_agent_tokens, subagent_tokens, middleware_tokens, "
                    "token_usage_by_model, created_at, updated_at"
                    ") VALUES ("
                    ":run_id, :thread_id, 'pending', 'reject', :metadata, '{}', "
                    "0, 0, 0, 0, 0, 0, 0, 0, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                    ")"
                ),
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "metadata": json.dumps(metadata),
                },
            )


@pytest.mark.anyio
async def test_two_sqlite_managers_share_one_wake_reservation_and_lease(tmp_path: Path) -> None:
    first_engine, second_engine, first, second = await _manager_pair(tmp_path)
    wake_id = str(uuid4())
    try:
        one, two = await asyncio.gather(
            first.create_or_reuse_command_room_wake(_admission(wake_id=wake_id, metadata={"sibling": "first"})),
            second.create_or_reuse_command_room_wake(_admission(wake_id=wake_id, metadata={"sibling": "second"})),
        )
        assert {one.record.run_id, two.record.run_id}.__len__() == 1
        assert sum(result.created for result in (one, two)) == 1
        assert sum(result.should_start_worker for result in (one, two)) == 1
        assert {one.outcome, two.outcome} <= {
            WakeAdmissionOutcome.LEASE_WON,
            WakeAdmissionOutcome.ACTIVE,
        }
        assert one.record.metadata["sibling"] in {"first", "second"}
        assert one.record.metadata["sibling"] == two.record.metadata["sibling"]
        assert one.record.kwargs == two.record.kwargs
        await first._store.put(  # type: ignore[union-attr]
            one.record.run_id,
            thread_id="tampered-thread",
            assistant_id="tampered-assistant",
            user_id="tampered-user",
            status="running",
            metadata={"sibling": "tampered"},
            kwargs={"input": {"messages": [{"content": "tampered"}]}},
        )
        persisted = await first._store.get_by_command_room_wake_id(wake_id)  # type: ignore[union-attr]
        assert persisted is not None
        assert persisted["thread_id"] == "thread-1"
        assert persisted["metadata"]["sibling"] == one.record.metadata["sibling"]
        assert persisted["metadata"]["source_run_id"] == "source-run"
        assert persisted["metadata"]["source_task_id"] == "source-task"
        assert persisted["kwargs"] == one.record.kwargs
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.anyio
async def test_wake_identity_conflict_does_not_return_the_canonical_record(tmp_path: Path) -> None:
    first_engine, second_engine, first, second = await _manager_pair(tmp_path)
    wake_id = str(uuid4())
    try:
        created = await first.create_or_reuse_command_room_wake(_admission(wake_id=wake_id))
        with pytest.raises(CommandRoomWakeIdentityConflict):
            await second.create_or_reuse_command_room_wake(_admission(wake_id=wake_id, user_id="other-user"))
        row = await first._store.get_by_command_room_wake_id(wake_id)  # type: ignore[union-attr]
        assert row is not None
        assert row["run_id"] == created.record.run_id
        assert row["user_id"] == "user-1"
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.anyio
async def test_exact_wake_lookup_ignores_history_and_probes_one_expired_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wake-exact-lookup.db').as_posix()}")
    repository = RunRepository(async_sessionmaker(engine, expire_on_commit=False))
    manager = RunManager(repository)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        initial = _admission(wake_id=str(uuid4()))
        admitted = await manager.create_or_reuse_command_room_wake(initial)
        assert admitted.outcome is WakeAdmissionOutcome.LEASE_WON
        for index in range(201):
            await repository.put(
                f"history-{index:03d}",
                thread_id="thread-1",
                user_id="user-1",
                status="success",
            )

        async def forbidden_history_scan(*_args, **_kwargs):
            raise AssertionError("wake lookup must not scan run history")

        monkeypatch.setattr(repository, "list_by_thread", forbidden_history_scan)
        monkeypatch.setattr(repository, "list_inflight", forbidden_history_scan)
        monkeypatch.setattr(repository, "list_expired_active_leases", forbidden_history_scan)

        found = await manager.find_command_room_wake(initial, probe_stale=True)
        assert found is not None
        assert found.run_id == admitted.record.run_id
        assert await manager.find_command_room_wake(_admission(wake_id=str(uuid4())), probe_stale=True) is None
        with pytest.raises(CommandRoomWakeIdentityConflict):
            await manager.find_command_room_wake(
                _admission(wake_id=initial.wake_id, source_task_id="other-source-task"),
                probe_stale=True,
            )

        stale = _admission(wake_id=str(uuid4()), thread_id="thread-2")
        row, created = await repository.reserve_command_room_wake(
            wake_id=stale.wake_id,
            thread_id=stale.thread_id,
            assistant_id=stale.assistant_id,
            user_id=stale.user_id,
            metadata=stale.persisted_metadata(),
            kwargs=stale.kwargs,
            multitask_strategy=stale.multitask_strategy,
            model_name=stale.model_name,
        )
        assert created
        now = datetime.now(UTC)
        lease = await repository.try_acquire_active_slot(
            stale.thread_id,
            row["run_id"],
            owner_worker_id="lost-worker",
            lease_expires_at=now - timedelta(seconds=1),
            now=now,
        )
        assert lease is not None
        stale_calls = 0
        recover_expired_lease = repository.recover_expired_lease

        async def count_stale_probe(*args, **kwargs):
            nonlocal stale_calls
            stale_calls += 1
            return await recover_expired_lease(*args, **kwargs)

        monkeypatch.setattr(repository, "recover_expired_lease", count_stale_probe)
        recovered = await manager.find_command_room_wake(stale, probe_stale=True)
        assert recovered is not None
        assert recovered.status == "error"
        assert recovered.terminal_reason == "worker_lost"
        assert recovered.task is None
        assert stale_calls == 1
        assert (await manager.find_command_room_wake(stale, probe_stale=True)).status == "error"
        assert stale_calls == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_wake_takeover_runs_the_first_canonical_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from unittest.mock import AsyncMock

    from app.gateway import services
    from app.gateway.routers.thread_runs import RunCreateRequest

    first_engine, second_engine, first, second = await _manager_pair(tmp_path)
    wake_id = str(uuid4())
    metadata = {"command_room_wakeup": True, "source_run_id": "source-run", "source_task_id": "source-task"}
    first_admission = CommandRoomWakeAdmission(
        wake_id=wake_id,
        thread_id="thread-1",
        user_id="user-1",
        assistant_id="command-room",
        source_run_id="source-run",
        source_task_id="source-task",
        metadata=metadata,
        kwargs={
            "input": {"messages": [{"role": "user", "content": "first payload A"}]},
            "config": {"tags": ["first"]},
            "context": {"model_name": "model-a", "thinking_enabled": True},
            "command": None,
            "checkpoint_id": None,
            "checkpoint": None,
            "interrupt_before": None,
            "interrupt_after": None,
            "stream_mode": ["values"],
            "stream_subgraphs": False,
        },
        model_name="model-a",
    )
    second_body = RunCreateRequest(
        assistant_id="command-room",
        input={"messages": [{"role": "user", "content": "later payload B"}]},
        metadata=metadata,
        config={"tags": ["later"]},
        context={"model_name": "model-b", "thinking_enabled": False},
        interrupt_before=["later-node"],
        stream_mode=["updates"],
        stream_subgraphs=True,
    )
    second_admission = CommandRoomWakeAdmission(
        wake_id=wake_id,
        thread_id="thread-1",
        user_id="user-1",
        assistant_id="command-room",
        source_run_id="source-run",
        source_task_id="source-task",
        metadata=metadata,
        kwargs={
            "input": second_body.input,
            "config": second_body.config,
            "context": second_body.context,
            "command": second_body.command,
            "checkpoint_id": second_body.checkpoint_id,
            "checkpoint": second_body.checkpoint,
            "interrupt_before": second_body.interrupt_before,
            "interrupt_after": second_body.interrupt_after,
            "stream_mode": second_body.stream_mode,
            "stream_subgraphs": second_body.stream_subgraphs,
        },
        model_name="model-b",
    )

    async def no_lease(*_args, **_kwargs):
        return None

    monkeypatch.setattr(first._store, "try_acquire_active_slot", no_lease)  # type: ignore[union-attr]
    captured: dict[str, object] = {}

    async def fake_run_agent(*_args, **kwargs):
        captured.update(kwargs)

    thread_store = SimpleNamespace(
        check_access=AsyncMock(return_value=True),
        get=AsyncMock(return_value={"thread_id": "thread-1", "user_id": "user-1"}),
        update_status=AsyncMock(),
    )
    monkeypatch.setattr(services, "get_stream_bridge", lambda _request: SimpleNamespace())
    monkeypatch.setattr(services, "get_run_manager", lambda _request: second)
    monkeypatch.setattr(services, "get_run_context", lambda _request: SimpleNamespace(thread_store=thread_store))
    monkeypatch.setattr(
        services,
        "get_app_config",
        lambda: SimpleNamespace(get_model_config=lambda model: object() if model == "model-a" else None),
    )
    monkeypatch.setattr(services, "resolve_agent_factory", lambda _assistant_id: object())
    monkeypatch.setattr(services, "run_agent", fake_run_agent)
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="user-1", system_role="user")),
    )

    try:
        reserved = await first.create_or_reuse_command_room_wake(first_admission)
        assert reserved.outcome is WakeAdmissionOutcome.ACTIVE_SLOT_BLOCKED
        assert not reserved.should_start_worker

        record = await services.start_run(
            second_body,
            "thread-1",
            request,
            command_room_wake_admission=second_admission,
        )
        await record.task

        assert record.run_id == reserved.record.run_id
        assert record.lease_token is not None
        assert captured["graph_input"]["messages"][0].content == "first payload A"
        assert captured["config"]["tags"] == ["first"]
        assert captured["config"]["configurable"]["model_name"] == "model-a"
        assert captured["config"]["context"]["model_name"] == "model-a"
        assert captured["stream_modes"] == ["values"]
        assert captured["stream_subgraphs"] is False
        assert captured["interrupt_before"] is None
        assert len(await second.list_by_thread("thread-1", user_id="user-1")) == 1
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.anyio
async def test_0011_legacy_conflict_fails_before_schema_changes(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wake-migration-conflict.db').as_posix()}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, config, "0010_task_lane_wake_claim")
        wake_id = str(uuid4())
        metadata = {
            "command_room_wakeup": True,
            "command_room_wake_id": wake_id,
        }
        await _insert_legacy_wake_runs(
            engine,
            (
                ("legacy-one", "thread-1", metadata),
                ("legacy-two", "thread-2", metadata),
            ),
        )

        with pytest.raises(RuntimeError, match=r"conflicts=1, digest=[0-9a-f]{16}"):
            await asyncio.to_thread(_upgrade, config, "head")

        async with engine.connect() as connection:
            columns = await connection.run_sync(lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("runs")})
            indexes = await connection.run_sync(lambda sync: {index["name"] for index in sa.inspect(sync).get_indexes("runs")})
        assert "command_room_wake_id" not in columns
        assert "uq_runs_command_room_wake_id" not in indexes
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_0011_preserves_legacy_wake_without_identity_as_null(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wake-migration-pre-identity.db').as_posix()}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, config, "0010_task_lane_wake_claim")
        await _insert_legacy_wake_runs(
            engine,
            (
                (
                    "legacy-before-wake-id",
                    "thread-1",
                    {"command_room_wakeup": True},
                ),
            ),
        )

        await asyncio.to_thread(_upgrade, config, "head")

        async with engine.connect() as connection:
            wake_value = await connection.scalar(sa.text("SELECT command_room_wake_id FROM runs WHERE run_id = 'legacy-before-wake-id'"))
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        assert wake_value is None
        assert version == "0014_goal_workspace_events"
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_0011_backfills_one_valid_legacy_wake_and_installs_global_constraints(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wake-migration-valid.db').as_posix()}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, config, "0010_task_lane_wake_claim")
        wake_id = str(uuid4())
        await _insert_legacy_wake_runs(
            engine,
            (
                (
                    "legacy-valid",
                    "thread-1",
                    {
                        "command_room_wakeup": True,
                        "command_room_wake_id": wake_id,
                    },
                ),
            ),
        )
        await asyncio.to_thread(_upgrade, config, "head")

        async with engine.connect() as connection:
            wake_value = await connection.scalar(sa.text("SELECT command_room_wake_id FROM runs WHERE run_id = 'legacy-valid'"))
            checks = await connection.run_sync(lambda sync: {check["name"] for check in sa.inspect(sync).get_check_constraints("runs")})
            indexes = await connection.run_sync(lambda sync: {index["name"]: index for index in sa.inspect(sync).get_indexes("runs")})
        assert wake_value == wake_id
        assert "ck_runs_command_room_wake_id_nonblank" in checks
        assert indexes["uq_runs_command_room_wake_id"]["column_names"] == ["command_room_wake_id"]
        assert indexes["uq_runs_command_room_wake_id"]["unique"]
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("column_ddl", ["TEXT NOT NULL", "CHAR(64)"], ids=["text-not-null", "char-64"])
async def test_0011_rejects_incompatible_existing_dedicated_column_before_schema_changes(
    tmp_path: Path,
    column_ddl: str,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wake-migration-incompatible-column.db').as_posix()}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, config, "0010_task_lane_wake_claim")
        async with engine.begin() as connection:
            await connection.execute(sa.text(f"ALTER TABLE runs ADD COLUMN command_room_wake_id {column_ddl}"))

        with pytest.raises(RuntimeError, match="dedicated column has an incompatible shape"):
            await asyncio.to_thread(_upgrade, config, "head")

        async with engine.connect() as connection:
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            checks = await connection.run_sync(lambda sync: {check["name"] for check in sa.inspect(sync).get_check_constraints("runs")})
            indexes = await connection.run_sync(lambda sync: {index["name"] for index in sa.inspect(sync).get_indexes("runs")})
        assert version == "0010_task_lane_wake_claim"
        assert "ck_runs_command_room_wake_id_nonblank" not in checks
        assert "uq_runs_command_room_wake_id" not in indexes
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_0011_accepts_compatible_existing_dedicated_column(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wake-migration-compatible-column.db').as_posix()}")
    try:
        config = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, config, "0010_task_lane_wake_claim")
        async with engine.begin() as connection:
            await connection.execute(sa.text("ALTER TABLE runs ADD COLUMN command_room_wake_id VARCHAR(64)"))
        wake_id = str(uuid4())
        await _insert_legacy_wake_runs(
            engine,
            (
                (
                    "legacy-compatible-column",
                    "thread-1",
                    {
                        "command_room_wakeup": True,
                        "command_room_wake_id": wake_id,
                    },
                ),
            ),
        )

        await asyncio.to_thread(_upgrade, config, "0011_runs_command_room_wake_id")

        async with engine.connect() as connection:
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            wake_value = await connection.scalar(sa.text("SELECT command_room_wake_id FROM runs WHERE run_id = 'legacy-compatible-column'"))
            column = await connection.run_sync(lambda sync: next(item for item in sa.inspect(sync).get_columns("runs") if item["name"] == "command_room_wake_id"))
        assert version == "0011_runs_command_room_wake_id"
        assert wake_value == wake_id
        assert column["nullable"] is True
        assert column["default"] is None
        assert not column["primary_key"]
        assert column["type"].length == 64
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reserved_key",
    [
        "command_room_wakeup",
        "command_room_wake_id",
        "source_run_id",
        "source_task_id",
    ],
)
async def test_start_run_rejects_external_command_room_wake_metadata(
    monkeypatch: pytest.MonkeyPatch,
    reserved_key: str,
) -> None:
    from app.gateway import services
    from app.gateway.routers.thread_runs import RunCreateRequest

    monkeypatch.setattr(services, "get_stream_bridge", lambda _request: object())
    monkeypatch.setattr(services, "get_run_manager", lambda _request: object())
    monkeypatch.setattr(services, "get_run_context", lambda _request: object())

    with pytest.raises(services.HTTPException, match="reserved for internal use") as exc:
        await services.start_run(
            RunCreateRequest(metadata={reserved_key: "forged"}),
            "thread-1",
            SimpleNamespace(),
        )
    assert exc.value.status_code == 400
