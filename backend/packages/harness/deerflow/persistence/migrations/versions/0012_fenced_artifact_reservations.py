"""Retain the historical revision identity after removing its runtime feature.

Revision ID: 0012_fenced_artifact_reservations
Revises: 0011_runs_command_room_wake_id
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0012_fenced_artifact_reservations"
down_revision: str | Sequence[str] | None = "0011_runs_command_room_wake_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
