"""Add native round state tables.

Revision ID: 0005_native_round_state
Revises: 0004_artifact_provenance_owner_scope
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_native_round_state"
down_revision: str | Sequence[str] | None = "0004_artifact_provenance_owner_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "rounds" not in tables:
        op.create_table(
            "rounds",
            sa.Column("round_id", sa.String(length=64), nullable=False),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column("parent_round_id", sa.String(length=64), nullable=True),
            sa.Column("current_run_id", sa.String(length=64), nullable=True),
            sa.Column("source_goal_run_id", sa.String(length=64), nullable=True),
            sa.Column("current_intent", sa.Text(), nullable=True),
            sa.Column("state", sa.String(length=24), nullable=False),
            sa.Column("next_action", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("round_id"),
        )
        op.create_index("ix_rounds_current_run_id", "rounds", ["current_run_id"])
        op.create_index("ix_rounds_thread_id", "rounds", ["thread_id"])
        op.create_index("ix_rounds_thread_state_updated", "rounds", ["thread_id", "state", "updated_at"])
        op.create_index("ix_rounds_thread_user_updated", "rounds", ["thread_id", "user_id", "updated_at"])
        op.create_index("ix_rounds_user_id", "rounds", ["user_id"])

    if "round_events" not in tables:
        op.create_table(
            "round_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("round_id", sa.String(length=64), nullable=False),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column("event_type", sa.String(length=48), nullable=False),
            sa.Column("content_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("round_id", "seq", name="uq_round_events_round_seq"),
        )
        op.create_index("ix_round_events_round_id", "round_events", ["round_id"])
        op.create_index("ix_round_events_thread_id", "round_events", ["thread_id"])
        op.create_index("ix_round_events_run_id", "round_events", ["run_id"])
        op.create_index("ix_round_events_thread_run", "round_events", ["thread_id", "run_id", "seq"])
        op.create_index("ix_round_events_user_id", "round_events", ["user_id"])

    if "task_lanes" not in tables:
        op.create_table(
            "task_lanes",
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.String(length=128), nullable=False),
            sa.Column("round_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column("role", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("result_ref", sa.Text(), nullable=True),
            sa.Column("evidence_ref", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("thread_id", "run_id", "task_id"),
        )
        op.create_index("ix_task_lanes_round_id", "task_lanes", ["round_id"])
        op.create_index("ix_task_lanes_round_status", "task_lanes", ["round_id", "status"])
        op.create_index("ix_task_lanes_user_id", "task_lanes", ["user_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "task_lanes" in tables:
        op.drop_table("task_lanes")
    if "round_events" in tables:
        op.drop_table("round_events")
    if "rounds" in tables:
        op.drop_table("rounds")
