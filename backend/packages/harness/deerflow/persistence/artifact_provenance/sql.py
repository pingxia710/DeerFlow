"""SQLAlchemy-backed artifact provenance index."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.artifact_provenance.model import ArtifactProvenanceRow
from deerflow.runtime.user_context import AUTO, DEFAULT_USER_ID, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso


class ArtifactProvenanceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                pass
        return datetime.now(UTC)

    @staticmethod
    def _row_to_dict(row: ArtifactProvenanceRow) -> dict[str, Any]:
        d = row.to_dict()
        d["provenance"] = d.pop("provenance_json", None) or {}
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = coerce_iso(val)
        return d

    @staticmethod
    def _entry_values(entry: dict[str, Any], *, user_id: str | None) -> dict[str, Any]:
        thread_id = entry.get("thread_id")
        run_id = entry.get("run_id")
        virtual_path = entry.get("virtual_path")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("artifact provenance entry requires thread_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("artifact provenance entry requires run_id")
        if not isinstance(virtual_path, str) or not virtual_path:
            raise ValueError("artifact provenance entry requires virtual_path")

        provenance = entry.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        source_ref = provenance.get("ref_source")
        if not isinstance(source_ref, str):
            source_ref = entry.get("source_ref") if isinstance(entry.get("source_ref"), str) else None

        entry_user_id = entry.get("user_id")
        owner = user_id if user_id is not None else str(entry_user_id) if entry_user_id is not None else DEFAULT_USER_ID
        return {
            "user_id": owner,
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": entry.get("task_id") if isinstance(entry.get("task_id"), str) else None,
            "virtual_path": virtual_path,
            "source_tool": entry.get("source_tool") if isinstance(entry.get("source_tool"), str) else None,
            "source_node": entry.get("source_node") if isinstance(entry.get("source_node"), str) else None,
            "source_event_type": entry.get("source_event_type") if isinstance(entry.get("source_event_type"), str) else None,
            "source_event_seq": entry.get("source_event_seq") if isinstance(entry.get("source_event_seq"), int) else None,
            "source_ref": source_ref,
            "available": bool(entry.get("available", False)),
            "display_policy": entry.get("display_policy") if isinstance(entry.get("display_policy"), str) else None,
            "sha256": entry.get("sha256") if isinstance(entry.get("sha256"), str) else None,
            "size_bytes": entry.get("size_bytes") if isinstance(entry.get("size_bytes"), int) else None,
            "mime_type": entry.get("mime_type") if isinstance(entry.get("mime_type"), str) else None,
            "provenance_json": provenance,
            "created_at": ArtifactProvenanceRepository._coerce_datetime(entry.get("created_at")),
            "updated_at": datetime.now(UTC),
        }

    async def upsert_many(
        self,
        entries: Iterable[dict[str, Any]],
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> int:
        resolved_user_id = resolve_user_id(user_id, method_name="ArtifactProvenanceRepository.upsert_many")
        count = 0
        async with self._sf() as session:
            for entry in entries:
                values = self._entry_values(entry, user_id=resolved_user_id)
                stmt = select(ArtifactProvenanceRow).where(
                    ArtifactProvenanceRow.user_id == values["user_id"],
                    ArtifactProvenanceRow.thread_id == values["thread_id"],
                    ArtifactProvenanceRow.run_id == values["run_id"],
                    ArtifactProvenanceRow.virtual_path == values["virtual_path"],
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is None:
                    session.add(ArtifactProvenanceRow(**values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                count += 1
            await session.commit()
        return count

    async def list_by_run(
        self,
        thread_id: str,
        run_id: str,
        *,
        limit: int = 500,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        resolved_user_id = resolve_user_id(user_id, method_name="ArtifactProvenanceRepository.list_by_run")
        stmt = select(ArtifactProvenanceRow).where(
            ArtifactProvenanceRow.thread_id == thread_id,
            ArtifactProvenanceRow.run_id == run_id,
        )
        if resolved_user_id is not None:
            stmt = stmt.where(ArtifactProvenanceRow.user_id == resolved_user_id)
        stmt = stmt.order_by(ArtifactProvenanceRow.source_event_seq.asc(), ArtifactProvenanceRow.id.asc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(row) for row in result.scalars()]

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 500,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        """List provenance rows for thread-level ownership/migration probes."""
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="ArtifactProvenanceRepository.list_by_thread",
        )
        stmt = select(ArtifactProvenanceRow).where(ArtifactProvenanceRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(ArtifactProvenanceRow.user_id == resolved_user_id)
        stmt = stmt.order_by(
            ArtifactProvenanceRow.source_event_seq.asc(),
            ArtifactProvenanceRow.id.asc(),
        ).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(row) for row in result.scalars()]

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        """Return every owner present on this thread without pagination."""
        stmt = select(ArtifactProvenanceRow.user_id).where(ArtifactProvenanceRow.thread_id == thread_id).distinct()
        async with self._sf() as session:
            return set((await session.execute(stmt)).scalars())

    async def delete_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> int:
        resolved_user_id = resolve_user_id(user_id, method_name="ArtifactProvenanceRepository.delete_by_thread")
        stmt = delete(ArtifactProvenanceRow).where(ArtifactProvenanceRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(ArtifactProvenanceRow.user_id == resolved_user_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def delete_by_run(
        self,
        thread_id: str,
        run_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> int:
        resolved_user_id = resolve_user_id(user_id, method_name="ArtifactProvenanceRepository.delete_by_run")
        stmt = delete(ArtifactProvenanceRow).where(
            ArtifactProvenanceRow.thread_id == thread_id,
            ArtifactProvenanceRow.run_id == run_id,
        )
        if resolved_user_id is not None:
            stmt = stmt.where(ArtifactProvenanceRow.user_id == resolved_user_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)


# Backwards-compatible method attached outside the class body for older imports.
async def _claim_legacy_by_thread(self: ArtifactProvenanceRepository, thread_id: str, owner_user_id: str) -> int:
    async with self._sf() as session:
        legacy_owner_filter = ArtifactProvenanceRow.user_id.is_(None)
        if owner_user_id != DEFAULT_USER_ID:
            legacy_owner_filter = legacy_owner_filter | (ArtifactProvenanceRow.user_id == DEFAULT_USER_ID)
        legacy_rows = list(
            (
                await session.execute(
                    select(ArtifactProvenanceRow)
                    .where(
                        ArtifactProvenanceRow.thread_id == thread_id,
                        legacy_owner_filter,
                    )
                    .order_by(ArtifactProvenanceRow.updated_at.desc(), ArtifactProvenanceRow.id.desc())
                )
            ).scalars()
        )
        if not legacy_rows:
            return 0

        owner_rows = list(
            (
                await session.execute(
                    select(ArtifactProvenanceRow).where(
                        ArtifactProvenanceRow.thread_id == thread_id,
                        ArtifactProvenanceRow.user_id == owner_user_id,
                    )
                )
            ).scalars()
        )
        owner_keys = {(row.run_id, row.virtual_path) for row in owner_rows}
        now = datetime.now(UTC)
        for row in legacy_rows:
            key = (row.run_id, row.virtual_path)
            if key in owner_keys:
                await session.delete(row)
            else:
                row.user_id = owner_user_id
                row.updated_at = now
                owner_keys.add(key)
        await session.commit()
        return len(legacy_rows)


ArtifactProvenanceRepository.claim_legacy_by_thread = _claim_legacy_by_thread
