"""Focused R0-E1/E2 checks for fail-closed FENCED_STAGING scaffolding."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from deerflow.command_room.fenced_staging import (
    ControlledTestExecutorCapability,
    FencedExecutorUnavailable,
    FencedStagingAdapter,
    ProtectedPublisher,
    legacy_preflight,
)
from deerflow.persistence.artifact_reservation import (
    ArtifactReservationRepository,
    CanonicalArtifactMapper,
    FencedArtifactPathError,
    FencedArtifactRoute,
    SnapshotMetadata,
)


class _SecureDirfdAvailable:
    def require_secure_dirfd(self) -> None:
        return None


class _SecureDirfdUnavailable:
    def require_secure_dirfd(self) -> None:
        raise FencedExecutorUnavailable("fenced_executor_unavailable")


def _key():
    return CanonicalArtifactMapper().map(
        user_id="owner-1",
        thread_id="thread-1",
        route=FencedArtifactRoute(
            work_package_id="package-a",
            container="technical-design",
            artifact_kind="technical-plan",
        ),
    )


@pytest.fixture
async def repository(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'fenced-staging.db'}", sqlite_dir=str(tmp_path))
    try:
        session_factory = get_session_factory()
        assert session_factory is not None
        yield ArtifactReservationRepository(session_factory)
    finally:
        await close_engine()


@pytest.fixture
async def competing_repositories(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    url = f"sqlite+aiosqlite:///{tmp_path / 'fenced-staging-race.db'}"
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    try:
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield (
            ArtifactReservationRepository(async_sessionmaker(first_engine, expire_on_commit=False)),
            ArtifactReservationRepository(async_sessionmaker(second_engine, expire_on_commit=False)),
        )
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


def test_typed_mapper_has_one_root_relative_posix_key_and_rejects_unknown_routes() -> None:
    mapper = CanonicalArtifactMapper()
    key = mapper.map(
        user_id="owner-1",
        thread_id="thread-1",
        route=FencedArtifactRoute(
            work_package_id="package-a",
            container="execution",
            artifact_kind="execution",
            delivery_cycle_index=3,
            task_id="task-1",
        ),
    )

    assert key.canonical_artifact_path == "packages/package-a/03-delivery/cycle-03/execution/task-7afaa346b4bf92bf.md"
    assert "command-room-loop" not in key.canonical_artifact_path
    with pytest.raises(FencedArtifactPathError):
        mapper.map(
            user_id="owner-1",
            thread_id="thread-1",
            route=FencedArtifactRoute(work_package_id="package-a", container="execution", artifact_kind="unexpected"),
        )


def test_legacy_bad_line_and_terminal_are_unverified() -> None:
    key = _key()
    assert legacy_preflight(key, b"{not-json}\n") == "legacy_unverified"
    assert (
        legacy_preflight(
            key,
            ('{"canonical_artifact_path":"' + key.canonical_artifact_path + '","status":"completed","writer_mode":"direct_legacy"}\n').encode(),
        )
        == "legacy_unverified"
    )


@pytest.mark.anyio
async def test_default_admission_is_unavailable_and_quarantined_without_staging(repository) -> None:
    adapter = FencedStagingAdapter(repository, secure_dirfd=_SecureDirfdUnavailable())

    result = await adapter.admit(_key(), run_id="run-1", task_id="task-1")

    assert result.status == "fenced_executor_unavailable"
    assert result.handle is None
    assert result.staging_locator is None
    snapshot = await repository.snapshot(_key())
    assert snapshot is not None
    assert snapshot.state == "quarantined"
    assert snapshot.quarantine_reason == "fenced_executor_unavailable"
    assert [row["event"] for row in await repository.history(_key())] == ["reserved", "quarantined"]


@pytest.mark.anyio
async def test_controlled_publisher_is_cas_idempotent_and_stale_generation_cannot_publish(repository) -> None:
    capability = ControlledTestExecutorCapability(available=True)
    adapter = FencedStagingAdapter(
        repository,
        enabled=True,
        secure_dirfd=_SecureDirfdAvailable(),
        executor=capability,
    )
    publisher = ProtectedPublisher(
        repository,
        enabled=True,
        secure_dirfd=_SecureDirfdAvailable(),
        executor=capability,
    )
    admission = await adapter.admit(_key(), run_id="run-1", task_id="task-1")
    assert admission.status == "active"
    assert admission.handle is not None

    publish_id = str(uuid4())
    snapshot = SnapshotMetadata(sha256="a" * 64, size_bytes=12, device=1, inode=2)
    assert (await publisher.publish(admission.handle, publish_id=publish_id, snapshot=snapshot)).status == "published"
    assert (await publisher.publish(admission.handle, publish_id=publish_id, snapshot=snapshot)).status == "published"

    successor = await repository.reserve(_key(), run_id="run-2", task_id="task-2")
    assert successor.status == "reserved"
    assert successor.handle is not None
    assert successor.handle.generation == admission.handle.generation + 1
    assert (await repository.begin_publish(admission.handle, publish_id=str(uuid4()))).status == "stale_generation"
    assert (await repository.snapshot(_key())).generation == successor.handle.generation  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_publish_race_and_crash_recovery_do_not_release_or_take_over(repository) -> None:
    capability = ControlledTestExecutorCapability(available=True)
    adapter = FencedStagingAdapter(
        repository,
        enabled=True,
        secure_dirfd=_SecureDirfdAvailable(),
        executor=capability,
    )
    admission = await adapter.admit(_key(), run_id="run-1", task_id="task-1")
    assert admission.handle is not None

    first_id, second_id = str(uuid4()), str(uuid4())
    first, second = await asyncio.gather(
        repository.begin_publish(admission.handle, publish_id=first_id),
        repository.begin_publish(admission.handle, publish_id=second_id),
    )
    assert {first.status, second.status} == {"publishing", "stale_generation"}
    assert await adapter.recover_unknown_execution(admission.handle) == "quarantined"
    current = await repository.snapshot(_key())
    assert current is not None
    assert current.state == "quarantined"
    assert current.generation == admission.handle.generation
    assert (await repository.reserve(_key(), run_id="run-2", task_id="task-2")).status == "authority_held"


@pytest.mark.anyio
async def test_two_sqlite_factories_have_one_cas_winner_for_reserve_publish_and_fence(competing_repositories) -> None:
    first_repository, second_repository = competing_repositories

    first_reservation, second_reservation = await asyncio.gather(
        first_repository.reserve(_key(), run_id="run-1", task_id="task-1"),
        second_repository.reserve(_key(), run_id="run-2", task_id="task-2"),
    )
    assert {first_reservation.status, second_reservation.status} == {"reserved", "authority_held"}
    handle = first_reservation.handle or second_reservation.handle
    assert handle is not None
    assert await first_repository.activate(handle, staging_locator="staging/artifact.md") == "active"

    first_intent, second_intent = await asyncio.gather(
        first_repository.begin_publish(handle, publish_id=str(uuid4())),
        second_repository.begin_publish(handle, publish_id=str(uuid4())),
    )
    assert {first_intent.status, second_intent.status} == {"publishing", "stale_generation"}

    first_fence, second_fence = await asyncio.gather(
        first_repository.quarantine(handle, reason_code="test_fence"),
        second_repository.quarantine(handle, reason_code="test_fence"),
    )
    assert {first_fence, second_fence} == {"quarantined", "stale_generation"}
    assert [row["event"] for row in await first_repository.history(_key())] == [
        "reserved",
        "activated",
        "publish_intent",
        "quarantined",
    ]


@pytest.mark.anyio
async def test_old_generation_or_token_cas_miss_leaves_current_authority_unchanged(repository) -> None:
    initial = await repository.reserve(_key(), run_id="run-1", task_id="task-1")
    assert initial.handle is not None
    assert await repository.activate(initial.handle, staging_locator="staging/first.md") == "active"
    publish_id = str(uuid4())
    snapshot = SnapshotMetadata(sha256="b" * 64, size_bytes=12, device=1, inode=2)
    assert (await repository.begin_publish(initial.handle, publish_id=publish_id)).status == "publishing"
    assert await repository.complete_publish(initial.handle, publish_id=publish_id, snapshot=snapshot) == "published"

    successor = await repository.reserve(_key(), run_id="run-2", task_id="task-2")
    assert successor.handle is not None
    assert await repository.activate(successor.handle, staging_locator="staging/second.md") == "active"
    forged = replace(successor.handle, owner_token="not-the-current-token")
    before = await repository.snapshot(_key())
    history_before = await repository.history(_key())

    assert await repository.activate(initial.handle, staging_locator="staging/stale.md") == "stale_generation"
    assert (await repository.begin_publish(initial.handle, publish_id=str(uuid4()))).status == "stale_generation"
    assert await repository.complete_publish(initial.handle, publish_id=str(uuid4()), snapshot=snapshot) == "stale_generation"
    assert await repository.quarantine(initial.handle, reason_code="stale_generation") == "stale_generation"
    assert (await repository.begin_publish(forged, publish_id=str(uuid4()))).status == "stale_generation"
    assert await repository.quarantine(forged, reason_code="bad_token") == "stale_generation"

    assert await repository.snapshot(_key()) == before
    assert await repository.history(_key()) == history_before


@pytest.mark.anyio
async def test_sqlite_busy_requires_quarantine_and_blocks_publisher(competing_repositories, monkeypatch) -> None:
    from sqlalchemy import text

    first_repository, second_repository = competing_repositories
    reservation = await first_repository.reserve(_key(), run_id="run-1", task_id="task-1")
    assert reservation.handle is not None
    assert await first_repository.activate(reservation.handle, staging_locator="staging/artifact.md") == "active"

    publisher = ProtectedPublisher(
        second_repository,
        enabled=True,
        secure_dirfd=_SecureDirfdAvailable(),
        executor=ControlledTestExecutorCapability(available=True),
    )
    complete_publish = AsyncMock()
    monkeypatch.setattr(second_repository, "complete_publish", complete_publish)
    async with first_repository._sf() as lock_session:  # noqa: SLF001 - independent SQLite connection for busy injection
        await lock_session.execute(text("BEGIN IMMEDIATE"))
        result = await publisher.publish(
            reservation.handle,
            publish_id=str(uuid4()),
            snapshot=SnapshotMetadata(sha256="c" * 64, size_bytes=12, device=1, inode=2),
        )
        assert result.status == "quarantine_required"
        await lock_session.rollback()

    complete_publish.assert_not_awaited()

    current = await first_repository.snapshot(_key())
    assert current is not None
    assert current.state == "active"
    assert [row["event"] for row in await first_repository.history(_key())] == ["reserved", "activated"]


@pytest.mark.anyio
async def test_database_error_requires_quarantine_without_claiming_persistence(repository, monkeypatch) -> None:
    reservation = await repository.reserve(_key(), run_id="run-1", task_id="task-1")
    assert reservation.handle is not None
    assert await repository.activate(reservation.handle, staging_locator="staging/artifact.md") == "active"

    original_write_session = repository._write_session  # noqa: SLF001 - inject only the authority transaction failure

    @asynccontextmanager
    async def unavailable_write_session():
        raise OperationalError("BEGIN IMMEDIATE", None, RuntimeError("database unavailable"))
        yield

    monkeypatch.setattr(repository, "_write_session", unavailable_write_session)
    assert await repository.quarantine(reservation.handle, reason_code="database_unavailable") == "quarantine_required"
    monkeypatch.setattr(repository, "_write_session", original_write_session)

    current = await repository.snapshot(_key())
    assert current is not None
    assert current.state == "active"
    assert [row["event"] for row in await repository.history(_key())] == ["reserved", "activated"]


@pytest.mark.anyio
async def test_begin_publish_database_error_blocks_protected_publisher(repository, monkeypatch) -> None:
    reservation = await repository.reserve(_key(), run_id="run-1", task_id="task-1")
    assert reservation.handle is not None
    assert await repository.activate(reservation.handle, staging_locator="staging/artifact.md") == "active"

    @asynccontextmanager
    async def unavailable_write_session():
        raise OperationalError("BEGIN IMMEDIATE", None, RuntimeError("database unavailable"))
        yield

    publisher = ProtectedPublisher(
        repository,
        enabled=True,
        secure_dirfd=_SecureDirfdAvailable(),
        executor=ControlledTestExecutorCapability(available=True),
    )
    complete_publish = AsyncMock()
    original_write_session = repository._write_session  # noqa: SLF001 - restore the authority transaction after fault injection
    monkeypatch.setattr(repository, "_write_session", unavailable_write_session)
    monkeypatch.setattr(repository, "complete_publish", complete_publish)

    result = await publisher.publish(
        reservation.handle,
        publish_id=str(uuid4()),
        snapshot=SnapshotMetadata(sha256="d" * 64, size_bytes=12, device=1, inode=2),
    )

    assert result.status == "quarantine_required"
    complete_publish.assert_not_awaited()
    monkeypatch.setattr(repository, "_write_session", original_write_session)
    current = await repository.snapshot(_key())
    assert current is not None
    assert current.state == "active"
    assert [row["event"] for row in await repository.history(_key())] == ["reserved", "activated"]
