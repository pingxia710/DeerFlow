"""Append-only factual records for one NextOS Goal Workspace."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class WorkspaceEventRow(Base):
    __tablename__ = "workspace_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    author_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("thread_id", "event_id", name="uq_workspace_events_thread_event"),
        Index(
            "ix_workspace_events_thread_user_type_id",
            "thread_id",
            "user_id",
            "event_type",
            "id",
        ),
    )
