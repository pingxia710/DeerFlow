"""Add durable, globally unique Command Room wake admissions.

Revision ID: 0011_runs_command_room_wake_id
Revises: 0010_task_lane_wake_claim
Create Date: 2026-07-17
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0011_runs_command_room_wake_id"
down_revision: str | Sequence[str] | None = "0010_task_lane_wake_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_runs_command_room_wake_id_nonblank"
_CHECK_SQL = "command_room_wake_id IS NULL OR (length(command_room_wake_id) BETWEEN 1 AND 64 AND command_room_wake_id = trim(command_room_wake_id))"
_INDEX_NAME = "uq_runs_command_room_wake_id"
_COLUMN_NAME = "command_room_wake_id"
_COLUMN_LENGTH = 64


def _normalized_sql(value: str) -> str:
    return re.sub(r"[\s\"`]", "", value).lower()


def _metadata(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _canonical_uuid4(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 64:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return value if parsed.version == 4 and str(parsed) == value else None


def _legacy_candidates(bind) -> list[tuple[str, str]]:
    columns = {column["name"] for column in sa.inspect(bind).get_columns("runs")}
    if "metadata_json" not in columns:
        return []
    rows = bind.execute(sa.text("SELECT run_id, metadata_json FROM runs ORDER BY run_id ASC")).mappings()
    candidates: dict[str, list[str]] = {}
    invalid: list[str] = []
    for row in rows:
        metadata = _metadata(row["metadata_json"])
        if metadata.get("command_room_wakeup") is not True:
            continue
        wake_id = _canonical_uuid4(metadata.get("command_room_wake_id"))
        if wake_id is None:
            invalid.append(str(row["run_id"]))
            continue
        candidates.setdefault(wake_id, []).append(str(row["run_id"]))

    conflicts = [f"invalid:{run_id}" for run_id in invalid]
    conflicts.extend(f"duplicate:{wake_id}" for wake_id, run_ids in candidates.items() if len(run_ids) != 1)
    if conflicts:
        digest = hashlib.sha256("\n".join(sorted(conflicts)).encode()).hexdigest()[:16]
        raise RuntimeError(f"command room wake legacy preflight failed (conflicts={len(conflicts)}, digest={digest})")
    return sorted((run_ids[0], wake_id) for wake_id, run_ids in candidates.items())


def _validate_existing_dedicated_column(bind, *, required: bool) -> None:
    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("runs")}
    column = columns.get(_COLUMN_NAME)
    if column is None:
        if required:
            raise RuntimeError("command room wake dedicated column is missing after creation")
        return

    problems: list[str] = []
    column_type = column.get("type")
    if column.get("name") != _COLUMN_NAME:
        problems.append("name")
    if not isinstance(column_type, sa.VARCHAR) or getattr(column_type, "length", None) != _COLUMN_LENGTH:
        problems.append("type")
    if column.get("nullable") is not True:
        problems.append("nullable")
    if "default" not in column or column["default"] is not None:
        problems.append("default")
    if "primary_key" not in column or bool(column["primary_key"]):
        problems.append("primary_key")
    if problems:
        raise RuntimeError(f"command room wake dedicated column has an incompatible shape ({', '.join(problems)})")


def _ensure_dedicated_column() -> None:
    bind = op.get_bind()
    _validate_existing_dedicated_column(bind, required=False)
    safe_add_column("runs", sa.Column(_COLUMN_NAME, sa.String(length=_COLUMN_LENGTH), nullable=True))
    _validate_existing_dedicated_column(bind, required=True)


def _ensure_check_constraint() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    checks = {check.get("name"): check.get("sqltext") for check in inspector.get_check_constraints("runs")}
    existing = checks.get(_CHECK_NAME)
    if existing is not None:
        if _normalized_sql(existing) != _normalized_sql(_CHECK_SQL):
            raise RuntimeError("command room wake check constraint has an incompatible definition")
        return
    with op.batch_alter_table("runs") as batch:
        batch.create_check_constraint(_CHECK_NAME, _CHECK_SQL)


def _ensure_unique_index() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index.get("name"): index for index in inspector.get_indexes("runs")}
    existing = indexes.get(_INDEX_NAME)
    if existing is not None:
        options = existing.get("dialect_options") or {}
        partial = any(value is not None for key, value in options.items() if key.endswith("_where"))
        if existing.get("column_names") != ["command_room_wake_id"] or not existing.get("unique") or partial:
            raise RuntimeError("command room wake unique index has an incompatible definition")
        return
    op.create_index(_INDEX_NAME, "runs", ["command_room_wake_id"], unique=True)


def _validate_dedicated_column(bind) -> None:
    rows = bind.execute(sa.text("SELECT command_room_wake_id FROM runs WHERE command_room_wake_id IS NOT NULL ORDER BY command_room_wake_id ASC")).scalars()
    seen: set[str] = set()
    for value in rows:
        wake_id = _canonical_uuid4(value)
        if wake_id is None or wake_id in seen:
            raise RuntimeError("command room wake dedicated column failed validation")
        seen.add(wake_id)


def upgrade() -> None:
    bind = op.get_bind()
    candidates = _legacy_candidates(bind)
    _ensure_dedicated_column()
    _ensure_check_constraint()

    for run_id, wake_id in candidates:
        current = bind.execute(
            sa.text("SELECT command_room_wake_id FROM runs WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()
        if current is None:
            bind.execute(
                sa.text("UPDATE runs SET command_room_wake_id = :wake_id WHERE run_id = :run_id AND command_room_wake_id IS NULL"),
                {"run_id": run_id, "wake_id": wake_id},
            )
        elif current != wake_id:
            raise RuntimeError("command room wake dedicated column conflicts with legacy metadata")

    _validate_dedicated_column(bind)
    _ensure_unique_index()


def downgrade() -> None:
    bind = op.get_bind()
    active = bind.execute(sa.text("SELECT count(*) FROM runs WHERE command_room_wake_id IS NOT NULL AND status IN ('pending', 'running', 'cancelling', 'rolling_back')")).scalar_one()
    if active:
        raise RuntimeError("cannot downgrade command room wake schema while wake runs are active")

    inspector = sa.inspect(bind)
    indexes = {index.get("name") for index in inspector.get_indexes("runs")}
    if _INDEX_NAME in indexes:
        op.drop_index(_INDEX_NAME, table_name="runs")
    checks = {check.get("name") for check in sa.inspect(bind).get_check_constraints("runs")}
    if _CHECK_NAME in checks:
        with op.batch_alter_table("runs") as batch:
            batch.drop_constraint(_CHECK_NAME, type_="check")
    safe_drop_column("runs", "command_room_wake_id")
