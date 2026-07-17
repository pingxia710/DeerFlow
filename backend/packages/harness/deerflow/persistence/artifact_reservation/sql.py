"""CAS-only durable authority for FENCED_STAGING.

This repository never opens a filesystem path or exposes an owner token after
reservation.  A protected publisher is responsible for the data plane.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.artifact_reservation.mapper import CanonicalArtifactMapper
from deerflow.persistence.artifact_reservation.model import (
    ArtifactExecutionRow,
    ArtifactQuarantineRow,
    ArtifactReservationHistoryRow,
    ArtifactReservationRow,
    WriterFenceProofRow,
)
from deerflow.persistence.artifact_reservation.types import (
    WRITER_MODE_FENCED_STAGING,
    FencedArtifactKey,
    PublishIntent,
    ReservationHandle,
    ReservationResult,
    ReservationSnapshot,
    SnapshotMetadata,
)

_REASON_RE = re.compile(r"[a-z0-9_]{1,64}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class _CasMiss(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _uuid(value: str, field: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    if str(parsed) != value:
        raise ValueError(f"{field} must be a canonical UUID")
    return value


def _opaque_locator(locator: str) -> str:
    if not isinstance(locator, str) or not locator or locator.startswith("/") or "\\" in locator or "\0" in locator:
        raise ValueError("staging locator must be a relative logical identifier")
    if any(part in {"", ".", ".."} for part in locator.split("/")):
        raise ValueError("staging locator must be normalized")
    return locator


class ArtifactReservationRepository:
    """One logical-key authority row with generation/token compare-and-swap."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._mapper = CanonicalArtifactMapper()

    def _validate_key(self, key: FencedArtifactKey) -> None:
        if self._mapper.map(user_id=key.user_id, thread_id=key.thread_id, route=key.route) != key:
            raise ValueError("canonical artifact path must come from the typed mapper")

    @staticmethod
    def _validate_binding(run_id: str, task_id: str) -> tuple[str, str]:
        if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
            raise ValueError("run_id is required")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
            raise ValueError("task_id is required")
        return run_id, task_id

    @asynccontextmanager
    async def _write_session(self) -> AsyncIterator[AsyncSession]:
        async with self._sf() as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
                try:
                    yield session
                except BaseException:
                    await session.rollback()
                    raise
                else:
                    await session.commit()
            else:
                async with session.begin():
                    yield session

    @staticmethod
    def _binding_conditions(handle: ReservationHandle) -> tuple[object, ...]:
        return (
            ArtifactReservationRow.reservation_id == handle.reservation_id,
            ArtifactReservationRow.user_id == handle.key.user_id,
            ArtifactReservationRow.thread_id == handle.key.thread_id,
            ArtifactReservationRow.canonical_artifact_path == handle.key.canonical_artifact_path,
            ArtifactReservationRow.generation == handle.generation,
            ArtifactReservationRow.execution_id == handle.execution_id,
            ArtifactReservationRow.run_id == handle.run_id,
            ArtifactReservationRow.task_id == handle.task_id,
            ArtifactReservationRow.writer_mode == WRITER_MODE_FENCED_STAGING,
            ArtifactReservationRow.owner_token_digest == _digest(handle.owner_token),
        )

    @staticmethod
    def _execution_conditions(handle: ReservationHandle) -> tuple[object, ...]:
        return (
            ArtifactExecutionRow.execution_id == handle.execution_id,
            ArtifactExecutionRow.reservation_id == handle.reservation_id,
            ArtifactExecutionRow.generation == handle.generation,
            ArtifactExecutionRow.run_id == handle.run_id,
            ArtifactExecutionRow.task_id == handle.task_id,
        )

    async def _row_for_handle(self, session: AsyncSession, handle: ReservationHandle) -> ArtifactReservationRow | None:
        return await session.scalar(select(ArtifactReservationRow).where(*self._binding_conditions(handle)).with_for_update())

    @staticmethod
    def _binding_digest(row: ArtifactReservationRow) -> str:
        return _digest("\x1f".join((row.reservation_id, str(row.generation), row.execution_id, row.owner_token_digest, row.run_id, row.task_id)))

    @staticmethod
    def _append_history(
        session: AsyncSession,
        row: ArtifactReservationRow,
        *,
        event: str,
        proof_id: str | None = None,
        publish_id: str | None = None,
        snapshot: SnapshotMetadata | None = None,
    ) -> None:
        row.history_sequence += 1
        session.add(
            ArtifactReservationHistoryRow(
                history_id=str(uuid4()),
                reservation_id=row.reservation_id,
                sequence=row.history_sequence,
                event=event,
                generation=row.generation,
                execution_id=row.execution_id,
                proof_id=proof_id,
                publish_id=publish_id,
                sha256=snapshot.sha256 if snapshot is not None else None,
                size_bytes=snapshot.size_bytes if snapshot is not None else None,
            )
        )

    @staticmethod
    def _snapshot_valid(snapshot: SnapshotMetadata) -> None:
        if not _SHA256_RE.fullmatch(snapshot.sha256) or snapshot.size_bytes < 0 or snapshot.device < 0 or snapshot.inode <= 0:
            raise ValueError("invalid protected snapshot metadata")

    async def reserve(self, key: FencedArtifactKey, *, run_id: str, task_id: str) -> ReservationResult:
        """Create a generation after an explicit request; never from terminal recovery."""

        self._validate_key(key)
        run_id, task_id = self._validate_binding(run_id, task_id)
        owner_token = secrets.token_urlsafe(32)
        execution_id = str(uuid4())
        try:
            async with self._write_session() as session:
                updated = await session.execute(
                    update(ArtifactReservationRow)
                    .where(
                        ArtifactReservationRow.user_id == key.user_id,
                        ArtifactReservationRow.thread_id == key.thread_id,
                        ArtifactReservationRow.canonical_artifact_path == key.canonical_artifact_path,
                        ArtifactReservationRow.writer_mode == WRITER_MODE_FENCED_STAGING,
                        ArtifactReservationRow.state == "published",
                    )
                    .values(
                        generation=ArtifactReservationRow.generation + 1,
                        state="reserved",
                        run_id=run_id,
                        task_id=task_id,
                        execution_id=execution_id,
                        owner_token_digest=_digest(owner_token),
                        work_package_id=key.route.work_package_id,
                        container=key.route.container,
                        artifact_kind=key.route.artifact_kind,
                        delivery_cycle_index=key.route.delivery_cycle_index,
                        publish_id=None,
                        updated_at=_now(),
                    )
                )
                if updated.rowcount == 1:
                    row = await session.scalar(select(ArtifactReservationRow).where(ArtifactReservationRow.execution_id == execution_id).with_for_update())
                    if row is None:
                        raise RuntimeError("reserved authority row was not found")
                else:
                    row = await session.scalar(
                        select(ArtifactReservationRow)
                        .where(
                            ArtifactReservationRow.user_id == key.user_id,
                            ArtifactReservationRow.thread_id == key.thread_id,
                            ArtifactReservationRow.canonical_artifact_path == key.canonical_artifact_path,
                        )
                        .with_for_update()
                    )
                    if row is not None:
                        return ReservationResult(status="authority_held")
                    row = ArtifactReservationRow(
                        reservation_id=str(uuid4()),
                        user_id=key.user_id,
                        thread_id=key.thread_id,
                        canonical_artifact_path=key.canonical_artifact_path,
                        generation=1,
                        state="reserved",
                        writer_mode=WRITER_MODE_FENCED_STAGING,
                        run_id=run_id,
                        task_id=task_id,
                        execution_id=execution_id,
                        owner_token_digest=_digest(owner_token),
                        work_package_id=key.route.work_package_id,
                        container=key.route.container,
                        artifact_kind=key.route.artifact_kind,
                        delivery_cycle_index=key.route.delivery_cycle_index,
                        history_sequence=0,
                    )
                    try:
                        async with session.begin_nested():
                            session.add(row)
                            await session.flush()
                    except IntegrityError:
                        return ReservationResult(status="authority_held")
                session.add(
                    ArtifactExecutionRow(
                        execution_id=execution_id,
                        reservation_id=row.reservation_id,
                        generation=row.generation,
                        run_id=run_id,
                        task_id=task_id,
                        staging_locator=None,
                        state="reserved",
                    )
                )
                self._append_history(session, row, event="reserved")
                handle = ReservationHandle(
                    reservation_id=row.reservation_id,
                    key=key,
                    generation=row.generation,
                    execution_id=execution_id,
                    owner_token=owner_token,
                    run_id=run_id,
                    task_id=task_id,
                )
            return ReservationResult(status="reserved", handle=handle)
        except IntegrityError as exc:
            detail = str(exc).lower()
            if "unique" in detail or "duplicate" in detail:
                return ReservationResult(status="authority_held")
            raise

    async def activate(self, handle: ReservationHandle, *, staging_locator: str) -> str:
        locator = _opaque_locator(staging_locator)
        try:
            async with self._write_session() as session:
                updated = await session.execute(update(ArtifactReservationRow).where(*self._binding_conditions(handle), ArtifactReservationRow.state == "reserved").values(state="active", updated_at=_now()))
                if updated.rowcount != 1:
                    return "stale_generation"
                execution = await session.execute(update(ArtifactExecutionRow).where(*self._execution_conditions(handle), ArtifactExecutionRow.state == "reserved").values(state="active", staging_locator=locator, launched_at=_now()))
                if execution.rowcount != 1:
                    raise _CasMiss
                row = await self._row_for_handle(session, handle)
                if row is None:
                    raise _CasMiss
                self._append_history(session, row, event="activated")
                return "active"
        except _CasMiss:
            return "stale_generation"

    async def begin_publish(self, handle: ReservationHandle, *, publish_id: str) -> PublishIntent:
        publish_id = _uuid(publish_id, "publish_id")
        async with self._write_session() as session:
            updated = await session.execute(update(ArtifactReservationRow).where(*self._binding_conditions(handle), ArtifactReservationRow.state == "active").values(state="publishing", publish_id=publish_id, updated_at=_now()))
            if updated.rowcount == 1:
                row = await self._row_for_handle(session, handle)
                if row is None:
                    raise _CasMiss
                self._append_history(session, row, event="publish_intent", publish_id=publish_id)
                return PublishIntent(status="publishing", publish_id=publish_id)
            row = await self._row_for_handle(session, handle)
            if row is None:
                return PublishIntent(status="stale_generation", publish_id=publish_id)
            if row.state == "published" and row.publish_id == publish_id:
                return PublishIntent(status="published", publish_id=publish_id)
            if row.state == "publishing" and row.publish_id == publish_id:
                return PublishIntent(status="publish_intent_replayed", publish_id=publish_id)
            return PublishIntent(status="stale_generation", publish_id=publish_id)

    async def complete_publish(self, handle: ReservationHandle, *, publish_id: str, snapshot: SnapshotMetadata) -> str:
        publish_id = _uuid(publish_id, "publish_id")
        self._snapshot_valid(snapshot)
        try:
            async with self._write_session() as session:
                updated = await session.execute(
                    update(ArtifactReservationRow)
                    .where(
                        *self._binding_conditions(handle),
                        ArtifactReservationRow.state == "publishing",
                        ArtifactReservationRow.publish_id == publish_id,
                    )
                    .values(state="published", updated_at=_now())
                )
                if updated.rowcount == 1:
                    execution = await session.execute(update(ArtifactExecutionRow).where(*self._execution_conditions(handle), ArtifactExecutionRow.state == "active").values(state="published", terminal_at=_now()))
                    if execution.rowcount != 1:
                        raise _CasMiss
                    row = await self._row_for_handle(session, handle)
                    if row is None:
                        raise _CasMiss
                    proof_id = str(uuid4())
                    session.add(
                        WriterFenceProofRow(
                            proof_id=proof_id,
                            reservation_id=row.reservation_id,
                            execution_id=row.execution_id,
                            generation=row.generation,
                            kind="published",
                            publisher_identity="fenced_staging_interface",
                            publisher_version="r0-e2",
                            publish_id=publish_id,
                            binding_digest=self._binding_digest(row),
                        )
                    )
                    self._append_history(session, row, event="published", proof_id=proof_id, publish_id=publish_id, snapshot=snapshot)
                    return "published"
                row = await self._row_for_handle(session, handle)
                if row is None or row.publish_id != publish_id:
                    return "stale_generation"
                if row.state != "published":
                    return "stale_generation"
                published = await session.scalar(
                    select(ArtifactReservationHistoryRow).where(
                        ArtifactReservationHistoryRow.reservation_id == row.reservation_id,
                        ArtifactReservationHistoryRow.generation == row.generation,
                        ArtifactReservationHistoryRow.event == "published",
                        ArtifactReservationHistoryRow.publish_id == publish_id,
                    )
                )
                if published is not None and published.sha256 == snapshot.sha256 and published.size_bytes == snapshot.size_bytes:
                    return "published"
                return "snapshot_mismatch"
        except _CasMiss:
            return "stale_generation"

    async def quarantine(self, handle: ReservationHandle, *, reason_code: str, evidence_reference: str | None = None) -> str:
        if not _REASON_RE.fullmatch(reason_code):
            raise ValueError("reason_code must be a short desensitized identifier")
        try:
            async with self._write_session() as session:
                updated = await session.execute(
                    update(ArtifactReservationRow)
                    .where(
                        *self._binding_conditions(handle),
                        ArtifactReservationRow.state.in_(("reserved", "active", "publishing")),
                    )
                    .values(state="quarantined", updated_at=_now())
                )
                if updated.rowcount == 1:
                    execution = await session.execute(
                        update(ArtifactExecutionRow)
                        .where(
                            *self._execution_conditions(handle),
                            ArtifactExecutionRow.state.in_(("reserved", "active")),
                        )
                        .values(state="quarantined", terminal_at=_now())
                    )
                    if execution.rowcount != 1:
                        raise _CasMiss
                    row = await self._row_for_handle(session, handle)
                    if row is None:
                        raise _CasMiss
                    proof_id = str(uuid4())
                    session.add(
                        WriterFenceProofRow(
                            proof_id=proof_id,
                            reservation_id=row.reservation_id,
                            execution_id=row.execution_id,
                            generation=row.generation,
                            kind="fence_revoked",
                            publisher_identity="fenced_staging_interface",
                            publisher_version="r0-e2",
                            publish_id="",
                            binding_digest=self._binding_digest(row),
                            reason_code=reason_code,
                        )
                    )
                    session.add(
                        ArtifactQuarantineRow(
                            quarantine_id=str(uuid4()),
                            reservation_id=row.reservation_id,
                            generation=row.generation,
                            reason_code=reason_code,
                            evidence_reference=evidence_reference,
                        )
                    )
                    self._append_history(session, row, event="quarantined", proof_id=proof_id)
                    return "quarantined"
                return "stale_generation"
        except _CasMiss:
            return "stale_generation"
        except SQLAlchemyError:
            # The authority could not record the fence, so callers must keep
            # the artifact blocked rather than claim a durable quarantine.
            return "quarantine_required"

    async def snapshot(self, key: FencedArtifactKey) -> ReservationSnapshot | None:
        self._validate_key(key)
        async with self._sf() as session:
            row = await session.scalar(
                select(ArtifactReservationRow).where(
                    ArtifactReservationRow.user_id == key.user_id,
                    ArtifactReservationRow.thread_id == key.thread_id,
                    ArtifactReservationRow.canonical_artifact_path == key.canonical_artifact_path,
                )
            )
            if row is None:
                return None
            quarantine = await session.scalar(
                select(ArtifactQuarantineRow.reason_code)
                .where(
                    ArtifactQuarantineRow.reservation_id == row.reservation_id,
                    ArtifactQuarantineRow.generation == row.generation,
                )
                .order_by(ArtifactQuarantineRow.created_at.desc())
            )
            return ReservationSnapshot(
                reservation_id=row.reservation_id,
                generation=row.generation,
                state=row.state,
                execution_id=row.execution_id,
                publish_id=row.publish_id,
                quarantine_reason=quarantine,
            )

    async def history(self, key: FencedArtifactKey) -> list[dict[str, object]]:
        self._validate_key(key)
        async with self._sf() as session:
            row = await session.scalar(
                select(ArtifactReservationRow).where(
                    ArtifactReservationRow.user_id == key.user_id,
                    ArtifactReservationRow.thread_id == key.thread_id,
                    ArtifactReservationRow.canonical_artifact_path == key.canonical_artifact_path,
                )
            )
            if row is None:
                return []
            result = await session.execute(select(ArtifactReservationHistoryRow).where(ArtifactReservationHistoryRow.reservation_id == row.reservation_id).order_by(ArtifactReservationHistoryRow.sequence))
            return [item.to_dict() for item in result.scalars()]
