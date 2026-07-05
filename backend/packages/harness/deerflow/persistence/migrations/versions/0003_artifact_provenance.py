"""Add artifact provenance index.

Revision ID: 0003_artifact_provenance
Revises: 0002_runs_token_usage
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_artifact_provenance"
down_revision: str | Sequence[str] | None = "0002_runs_token_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "artifact_provenance" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "artifact_provenance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("virtual_path", sa.Text(), nullable=False),
        sa.Column("source_tool", sa.String(length=128), nullable=True),
        sa.Column("source_node", sa.String(length=128), nullable=True),
        sa.Column("source_event_type", sa.String(length=64), nullable=True),
        sa.Column("source_event_seq", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(length=64), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("display_policy", sa.String(length=32), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "run_id", "virtual_path", name="uq_artifact_provenance_run_path"),
    )
    with op.batch_alter_table("artifact_provenance", schema=None) as batch_op:
        batch_op.create_index("ix_artifact_provenance_owner_thread_run", ["user_id", "thread_id", "run_id"], unique=False)
        batch_op.create_index("ix_artifact_provenance_thread_run", ["thread_id", "run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_artifact_provenance_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("artifact_provenance", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifact_provenance_user_id"))
        batch_op.drop_index("ix_artifact_provenance_thread_run")
        batch_op.drop_index("ix_artifact_provenance_owner_thread_run")
    op.drop_table("artifact_provenance")
