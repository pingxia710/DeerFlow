"""Add durable task-lane wake claims.

Revision ID: 0010_task_lane_wake_claim
Revises: 0009_task_lane_display_metadata
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0010_task_lane_wake_claim"
down_revision: str | Sequence[str] | None = "0009_task_lane_display_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("wake_claim_id", sa.String(length=64), nullable=True),
        sa.Column("wake_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    ):
        safe_add_column("task_lanes", column)


def downgrade() -> None:
    for name in ("wake_claim_expires_at", "wake_claim_id"):
        safe_drop_column("task_lanes", name)
