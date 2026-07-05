"""Add native task lane reference lists.

Revision ID: 0008_task_lane_ref_lists
Revises: 0007_round_handoff_envelope
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_task_lane_ref_lists"
down_revision: str | Sequence[str] | None = "0007_round_handoff_envelope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "task_lanes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("task_lanes")}
    with op.batch_alter_table("task_lanes", schema=None) as batch_op:
        for name in ("evidence_refs_json", "artifact_refs_json", "output_refs_json"):
            if name not in columns:
                batch_op.add_column(sa.Column(name, sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "task_lanes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("task_lanes")}
    with op.batch_alter_table("task_lanes", schema=None) as batch_op:
        for name in ("output_refs_json", "artifact_refs_json", "evidence_refs_json"):
            if name in columns:
                batch_op.drop_column(name)
