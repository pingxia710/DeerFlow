"""Dedicated durable authority rows for FENCED_STAGING."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ArtifactReservationRow(Base):
    __tablename__ = "artifact_reservations"

    reservation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    writer_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    owner_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    work_package_id: Mapped[str | None] = mapped_column(String(64))
    container: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_cycle_index: Mapped[int | None] = mapped_column(Integer)
    publish_id: Mapped[str | None] = mapped_column(String(36))
    history_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        CheckConstraint("generation > 0", name="ck_artifact_reservations_generation_positive"),
        CheckConstraint("length(trim(user_id)) > 0", name="ck_artifact_reservations_owner_nonblank"),
        CheckConstraint("writer_mode = 'fenced_staging'", name="ck_artifact_reservations_writer_mode"),
        CheckConstraint("state IN ('reserved', 'active', 'publishing', 'published', 'quarantined')", name="ck_artifact_reservations_state"),
        UniqueConstraint("user_id", "thread_id", "canonical_artifact_path", name="uq_artifact_reservations_logical_key"),
    )


class ArtifactExecutionRow(Base):
    __tablename__ = "artifact_executions"

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("artifact_reservations.reservation_id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    staging_locator: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("generation > 0", name="ck_artifact_executions_generation_positive"),
        UniqueConstraint("reservation_id", "generation", name="uq_artifact_executions_reservation_generation"),
    )


class WriterFenceProofRow(Base):
    __tablename__ = "writer_fence_proofs"

    proof_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("artifact_reservations.reservation_id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    publisher_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    publisher_version: Mapped[str] = mapped_column(String(64), nullable=False)
    publish_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    binding_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("reservation_id", "generation", "kind", "publish_id", name="uq_writer_fence_proofs_generation_kind_publish"),)


class ArtifactReservationHistoryRow(Base):
    __tablename__ = "artifact_reservation_history"

    history_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("artifact_reservations.reservation_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_id: Mapped[str | None] = mapped_column(String(36))
    proof_id: Mapped[str | None] = mapped_column(String(36))
    publish_id: Mapped[str | None] = mapped_column(String(36))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("reservation_id", "sequence", name="uq_artifact_reservation_history_sequence"),)


class ArtifactQuarantineRow(Base):
    __tablename__ = "artifact_quarantines"

    quarantine_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("artifact_reservations.reservation_id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("reservation_id", "generation", name="uq_artifact_quarantines_generation"),
        Index("ix_artifact_quarantines_reservation", "reservation_id"),
    )
