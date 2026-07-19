"""Add append-only Goal Workspace events.

Revision ID: 0014_goal_workspace_events
Revises: 0013_factual_round_records
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_goal_workspace_events"
down_revision: str | Sequence[str] | None = "0013_factual_round_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "workspace_events" in inspector.get_table_names():
        return
    op.create_table(
        "workspace_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("author_run_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "thread_id",
            "event_id",
            name="uq_workspace_events_thread_event",
        ),
    )
    op.create_index(
        "ix_workspace_events_author_run_id",
        "workspace_events",
        ["author_run_id"],
    )
    op.create_index("ix_workspace_events_thread_id", "workspace_events", ["thread_id"])
    op.create_index(
        "ix_workspace_events_thread_user_type_id",
        "workspace_events",
        ["thread_id", "user_id", "event_type", "id"],
    )
    op.create_index("ix_workspace_events_user_id", "workspace_events", ["user_id"])


def downgrade() -> None:
    if "workspace_events" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("workspace_events")
