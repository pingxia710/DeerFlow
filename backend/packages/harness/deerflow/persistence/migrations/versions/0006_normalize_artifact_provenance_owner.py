"""Normalize artifact provenance owners.

Revision ID: 0006_normalize_artifact_provenance_owner
Revises: 0005_native_round_state
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_normalize_artifact_provenance_owner"
down_revision: str | Sequence[str] | None = "0005_native_round_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_USER_ID = "default"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "artifact_provenance" not in tables:
        return

    run_owner = "NULL"
    thread_owner = "NULL"
    if "runs" in tables:
        run_owner = """
            (
                SELECT runs.user_id
                FROM runs
                WHERE runs.thread_id = artifact_provenance.thread_id
                  AND runs.run_id = artifact_provenance.run_id
                  AND runs.user_id IS NOT NULL
                  AND runs.user_id != 'default'
                LIMIT 1
            )
        """
    if "threads_meta" in tables:
        thread_owner = """
            (
                SELECT threads_meta.user_id
                FROM threads_meta
                WHERE threads_meta.thread_id = artifact_provenance.thread_id
                  AND threads_meta.user_id IS NOT NULL
                  AND threads_meta.user_id != 'default'
                LIMIT 1
            )
        """

    bind.execute(
        sa.text(
            f"""
            UPDATE artifact_provenance
            SET user_id = COALESCE({run_owner}, {thread_owner}, :default_user_id)
            WHERE user_id IS NULL OR user_id = :default_user_id
            """
        ),
        {"default_user_id": DEFAULT_USER_ID},
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM artifact_provenance
            WHERE id IN (
                SELECT id FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY user_id, thread_id, run_id, virtual_path
                            ORDER BY updated_at DESC, id DESC
                        ) AS rn
                    FROM artifact_provenance
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )

    with op.batch_alter_table("artifact_provenance", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "artifact_provenance" not in inspector.get_table_names():
        return
    with op.batch_alter_table("artifact_provenance", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(length=64), nullable=True)
