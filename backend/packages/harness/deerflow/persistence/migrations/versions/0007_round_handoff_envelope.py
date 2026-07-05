"""Add handoff envelope to task lanes.

Revision ID: 0007_round_handoff_envelope
Revises: 0006_normalize_artifact_provenance_owner
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_round_handoff_envelope"
down_revision: str | Sequence[str] | None = "0006_normalize_artifact_provenance_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "task_lanes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("task_lanes")}
    if "handoff_json" in columns:
        return
    with op.batch_alter_table("task_lanes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("handoff_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "task_lanes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("task_lanes")}
    if "handoff_json" not in columns:
        return
    with op.batch_alter_table("task_lanes", schema=None) as batch_op:
        batch_op.drop_column("handoff_json")
