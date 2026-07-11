"""SQLAlchemy-backed thread metadata repository."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.json_compat import json_match
from deerflow.persistence.thread_meta.base import LEGACY_CLAIM_COMPLETE_METADATA_KEY, LEGACY_CLAIMING_STATUS, InvalidMetadataFilterError, ThreadMetaConflictError, ThreadMetaCreateResult, ThreadMetaStore, strip_internal_thread_metadata
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.runtime.user_context import AUTO, DEFAULT_USER_ID, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)


class ThreadMetaRepository(ThreadMetaStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._metadata_merge_lock = asyncio.Lock()

    @staticmethod
    def _row_to_dict(row: ThreadMetaRow) -> dict[str, Any]:
        d = row.to_dict()
        d["metadata"] = strip_internal_thread_metadata(d.pop("metadata_json", None))
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                # SQLite drops tzinfo despite ``DateTime(timezone=True)``;
                # ``coerce_iso`` normalizes naive values as UTC so the wire format always carries tz.
                d[key] = coerce_iso(val)
        return d

    async def create(
        self,
        thread_id: str,
        *,
        assistant_id: str | None = None,
        user_id: str | None | _AutoSentinel = AUTO,
        display_name: str | None = None,
        metadata: dict | None = None,
        status: str = "idle",
    ) -> dict:
        # Auto-resolve user_id from contextvar when AUTO; explicit None
        # creates an orphan row (used by migration scripts).
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.create")
        now = datetime.now(UTC)
        row = ThreadMetaRow(
            thread_id=thread_id,
            assistant_id=assistant_id,
            user_id=resolved_user_id,
            display_name=display_name,
            status=status,
            metadata_json=strip_internal_thread_metadata(metadata),
            created_at=now,
            updated_at=now,
        )
        async with self._sf() as session:
            existing = await session.get(ThreadMetaRow, thread_id)
            if existing is not None:
                if existing.status == "deleting":
                    raise RuntimeError("Cannot create a thread while it is being deleted")
                if existing.user_id == resolved_user_id:
                    return ThreadMetaCreateResult(self._row_to_dict(existing), created=False)
                raise ThreadMetaConflictError("Thread ID is already in use")
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                existing = await session.get(ThreadMetaRow, thread_id)
                if existing is not None and existing.status != "deleting" and existing.user_id == resolved_user_id:
                    return ThreadMetaCreateResult(self._row_to_dict(existing), created=False)
                raise ThreadMetaConflictError("Thread ID is already in use") from exc
            await session.refresh(row)
            return ThreadMetaCreateResult(self._row_to_dict(row), created=True)

    async def get(
        self,
        thread_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> dict | None:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.get")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return None
            # Enforce owner filter unless explicitly bypassed (user_id=None).
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        """Check if ``user_id`` has access to ``thread_id``.

        Two modes — one row, two distinct semantics depending on what
        the caller is about to do:

        - ``require_existing=False`` (default, permissive):
          Returns True for: row missing (untracked legacy thread),
          ``row.user_id`` is None (shared / pre-auth data),
          or ``row.user_id == user_id``. Use for **read-style**
          decorators where treating an untracked thread as accessible
          preserves backward-compat.

        - ``require_existing=True`` (strict):
          Returns True **only** when the row exists AND
          ``row.user_id == user_id``.
          Use for **destructive / mutating** decorators (DELETE, PATCH,
          state-update) so a thread that has *already been deleted*
          cannot be re-targeted by any caller — closing the
          delete-idempotence cross-user gap where the row vanishing
          made every other user appear to "own" it.
        """
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return not require_existing
            if row.status in {"deleting", LEGACY_CLAIMING_STATUS}:
                return False
            if row.user_id is None:
                return not require_existing
            return row.user_id == user_id

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        """Search threads with optional metadata and status filters.

        Owner filter is enforced by default: caller must be in a user
        context. Pass ``user_id=None`` to bypass (migration/CLI).
        """
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.search")
        stmt = select(ThreadMetaRow).order_by(ThreadMetaRow.updated_at.desc(), ThreadMetaRow.thread_id.desc())
        if resolved_user_id is not None:
            stmt = stmt.where(ThreadMetaRow.user_id == resolved_user_id)
        if status:
            stmt = stmt.where(ThreadMetaRow.status == status)

        if metadata:
            applied = 0
            for key, value in metadata.items():
                try:
                    stmt = stmt.where(json_match(ThreadMetaRow.metadata_json, key, value))
                    applied += 1
                except (ValueError, TypeError) as exc:
                    logger.warning("Skipping metadata filter key %s: %s", ascii(key), exc)
            if applied == 0:
                # Comma-separated plain string (no list repr / nested
                # quoting) so the 400 detail surfaced by the Gateway is
                # easy for clients to read. Sorted for determinism.
                rejected_keys = ", ".join(sorted(str(k) for k in metadata))
                raise InvalidMetadataFilterError(f"All metadata filter keys were rejected as unsafe: {rejected_keys}")

        stmt = stmt.limit(limit).offset(offset)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def _check_ownership(self, session: AsyncSession, thread_id: str, resolved_user_id: str | None) -> bool:
        """Return True if the row exists and is owned (or filter bypassed)."""
        if resolved_user_id is None:
            return True  # explicit bypass
        row = await session.get(ThreadMetaRow, thread_id)
        return row is not None and row.user_id == resolved_user_id

    async def update_display_name(
        self,
        thread_id: str,
        display_name: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """Update the display_name (title) for a thread."""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_display_name")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(display_name=display_name, updated_at=datetime.now(UTC)))
            await session.commit()

    async def update_display_name_if_empty(
        self,
        thread_id: str,
        display_name: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> bool:
        """Atomically fill an autogenerated title without overwriting a user title."""
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="ThreadMetaRepository.update_display_name_if_empty",
        )
        stmt = update(ThreadMetaRow).where(
            ThreadMetaRow.thread_id == thread_id,
            (ThreadMetaRow.display_name.is_(None)) | (ThreadMetaRow.display_name == ""),
        )
        if resolved_user_id is not None:
            stmt = stmt.where(
                ThreadMetaRow.user_id == resolved_user_id,
                ThreadMetaRow.status != "deleting",
            )
        async with self._sf() as session:
            result = await session.execute(
                stmt.values(
                    display_name=display_name,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return result.rowcount != 0

    async def update_status(
        self,
        thread_id: str,
        status: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_status")
        async with self._sf() as session:
            stmt = update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id)
            if resolved_user_id is not None:
                stmt = stmt.where(ThreadMetaRow.user_id == resolved_user_id)
                if status != "deleting":
                    stmt = stmt.where(ThreadMetaRow.status != "deleting")
            await session.execute(stmt.values(status=status, updated_at=datetime.now(UTC)))
            await session.commit()

    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """Merge ``metadata`` into ``metadata_json``.

        Read-modify-write inside a single session/transaction so concurrent
        callers see consistent state. No-op if the row does not exist or
        the user_id check fails.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_metadata")
        # SQLite cannot lock a row for this JSON read-modify-write. DeerFlow's
        # supported SQL gateway topology is one worker, so serialize merges
        # through commit inside that process.
        async with self._metadata_merge_lock:
            async with self._sf() as session:
                row = await session.get(ThreadMetaRow, thread_id)
                if row is None:
                    return
                if resolved_user_id is not None and row.user_id != resolved_user_id:
                    return
                merged = dict(row.metadata_json or {})
                merged.update(strip_internal_thread_metadata(metadata))
                row.metadata_json = merged
                row.updated_at = datetime.now(UTC)
                await session.commit()

    async def update_owner(
        self,
        thread_id: str,
        owner_user_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """Move a thread metadata row to ``owner_user_id``."""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_owner")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(user_id=owner_user_id, updated_at=datetime.now(UTC)))
            await session.commit()

    async def claim_legacy_owner(self, thread_id: str, owner_user_id: str) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(ThreadMetaRow)
                .where(
                    ThreadMetaRow.thread_id == thread_id,
                    ThreadMetaRow.status != "deleting",
                    or_(
                        ThreadMetaRow.user_id.is_(None),
                        ThreadMetaRow.user_id == DEFAULT_USER_ID,
                        ThreadMetaRow.user_id == owner_user_id,
                    ),
                )
                .values(
                    user_id=owner_user_id,
                    status=LEGACY_CLAIMING_STATUS,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return result.rowcount != 0

    async def is_legacy_claim_complete(self, thread_id: str, owner_user_id: str) -> bool:
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            return bool(row is not None and row.status not in {"deleting", LEGACY_CLAIMING_STATUS} and row.user_id == owner_user_id and (row.metadata_json or {}).get(LEGACY_CLAIM_COMPLETE_METADATA_KEY) == owner_user_id)

    async def mark_legacy_claim_complete(self, thread_id: str, owner_user_id: str) -> bool:
        async with self._metadata_merge_lock:
            async with self._sf() as session:
                row = await session.get(ThreadMetaRow, thread_id)
                if row is None or row.status == "deleting" or row.user_id != owner_user_id:
                    return False
                metadata = dict(row.metadata_json or {})
                metadata[LEGACY_CLAIM_COMPLETE_METADATA_KEY] = owner_user_id
                row.metadata_json = metadata
                row.status = "idle"
                row.updated_at = datetime.now(UTC)
                await session.commit()
                return True

    async def delete(
        self,
        thread_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.delete")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return
            await session.delete(row)
            await session.commit()
