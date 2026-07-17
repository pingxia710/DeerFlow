"""Add fail-closed FENCED_STAGING durable authority tables.

Revision ID: 0012_fenced_artifact_reservations
Revises: 0011_runs_command_room_wake_id
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.artifact_reservation.model import (
    ArtifactExecutionRow,
    ArtifactQuarantineRow,
    ArtifactReservationHistoryRow,
    ArtifactReservationRow,
    WriterFenceProofRow,
)

revision: str = "0012_fenced_artifact_reservations"
down_revision: str | Sequence[str] | None = "0011_runs_command_room_wake_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_if_missing(table: sa.Table) -> None:
    bind = op.get_bind()
    if table.name not in sa.inspect(bind).get_table_names():
        table.create(bind)


def upgrade() -> None:
    # Schema-only migration: it deliberately does not inspect legacy JSONL,
    # import direct reservations, release a key, or modify filesystem state.
    for table in (
        ArtifactReservationRow.__table__,
        ArtifactExecutionRow.__table__,
        WriterFenceProofRow.__table__,
        ArtifactReservationHistoryRow.__table__,
        ArtifactQuarantineRow.__table__,
    ):
        _create_if_missing(table)


def downgrade() -> None:
    # Evidence must survive a feature rollback; a destructive downgrade could
    # erase the only record that an unsafe legacy writer remains unverified.
    raise RuntimeError("FENCED_STAGING authority evidence is retained; downgrade is refused")
