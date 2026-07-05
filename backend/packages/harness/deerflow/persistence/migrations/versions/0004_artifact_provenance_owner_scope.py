"""Scope artifact provenance uniqueness by owner.

Revision ID: 0004_artifact_provenance_owner_scope
Revises: 0003_artifact_provenance
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_artifact_provenance_owner_scope"
down_revision: str | Sequence[str] | None = "0003_artifact_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "artifact_provenance" not in inspector.get_table_names():
        return
    with op.batch_alter_table("artifact_provenance", schema=None) as batch_op:
        batch_op.drop_constraint("uq_artifact_provenance_run_path", type_="unique")
        batch_op.create_unique_constraint(
            "uq_artifact_provenance_owner_run_path",
            ["user_id", "thread_id", "run_id", "virtual_path"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "artifact_provenance" not in inspector.get_table_names():
        return
    with op.batch_alter_table("artifact_provenance", schema=None) as batch_op:
        batch_op.drop_constraint("uq_artifact_provenance_owner_run_path", type_="unique")
        batch_op.create_unique_constraint(
            "uq_artifact_provenance_run_path",
            ["thread_id", "run_id", "virtual_path"],
        )
