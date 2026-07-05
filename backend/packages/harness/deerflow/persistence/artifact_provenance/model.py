"""ORM model for persisted artifact provenance."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ArtifactProvenanceRow(Base):
    __tablename__ = "artifact_provenance"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(128))
    virtual_path: Mapped[str] = mapped_column(Text, nullable=False)

    source_tool: Mapped[str | None] = mapped_column(String(128))
    source_node: Mapped[str | None] = mapped_column(String(128))
    source_event_type: Mapped[str | None] = mapped_column(String(64))
    source_event_seq: Mapped[int | None] = mapped_column(Integer)
    source_ref: Mapped[str | None] = mapped_column(String(64))

    available: Mapped[bool] = mapped_column(Boolean, default=False)
    display_policy: Mapped[str | None] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", "run_id", "virtual_path", name="uq_artifact_provenance_owner_run_path"),
        Index("ix_artifact_provenance_thread_run", "thread_id", "run_id"),
        Index("ix_artifact_provenance_owner_thread_run", "user_id", "thread_id", "run_id"),
    )
