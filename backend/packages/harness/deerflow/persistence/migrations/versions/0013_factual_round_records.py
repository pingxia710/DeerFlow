"""Remove program-owned workflow state from round records.

Revision ID: 0013_factual_round_records
Revises: 0012_fenced_artifact_reservations
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_factual_round_records"
down_revision: str | Sequence[str] | None = "0012_fenced_artifact_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_COLUMNS = ("state", "next_action", "closed_at")
_LEGACY_INDEX = "ix_rounds_thread_state_updated"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rounds" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("rounds")}
    indexes = {index["name"] for index in inspector.get_indexes("rounds")}
    columns_to_drop = [name for name in _LEGACY_COLUMNS if name in columns]
    if not columns_to_drop and _LEGACY_INDEX not in indexes:
        return

    with op.batch_alter_table("rounds") as batch:
        if _LEGACY_INDEX in indexes:
            batch.drop_index(_LEGACY_INDEX)
        for name in columns_to_drop:
            batch.drop_column(name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rounds" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("rounds")}
    with op.batch_alter_table("rounds") as batch:
        if "state" not in columns:
            batch.add_column(sa.Column("state", sa.String(length=24), nullable=True))
        if "next_action" not in columns:
            batch.add_column(sa.Column("next_action", sa.Text(), nullable=True))
        if "closed_at" not in columns:
            batch.add_column(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))

    bind.execute(sa.text("UPDATE rounds SET state = 'open' WHERE state IS NULL"))
    with op.batch_alter_table("rounds") as batch:
        batch.alter_column("state", existing_type=sa.String(length=24), nullable=False)

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("rounds")}
    if _LEGACY_INDEX not in indexes:
        op.create_index(_LEGACY_INDEX, "rounds", ["thread_id", "state", "updated_at"])
