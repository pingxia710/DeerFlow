"""Add safe task lane display metadata.

Revision ID: 0009_task_lane_display_metadata
Revises: 0008_task_lane_ref_lists
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0009_task_lane_display_metadata"
down_revision: str | Sequence[str] | None = "0008_task_lane_ref_lists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    ):
        safe_add_column("task_lanes", column)


def downgrade() -> None:
    for name in ("duration_ms", "finished_at", "started_at", "result", "description"):
        safe_drop_column("task_lanes", name)
