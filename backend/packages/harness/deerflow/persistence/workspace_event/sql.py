"""SQL-backed append-only Goal Workspace facts."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.workspace_event.model import WorkspaceEventRow
from deerflow.runtime.user_context import DEFAULT_USER_ID
from deerflow.utils.time import coerce_iso

GOAL_MANDATE_REVISED = "goal.mandate.revised"
OPERATING_BRIEF_REVISED = "operating_brief.revised"
ORGANIZATION_MAP_REVISED = "organization.map.revised"
RESULT_RECEIVED = "result.received"
RESULTS_ACKNOWLEDGED = "result.inbox.acknowledged"
RESULTS_NOTIFIED = "result.inbox.notified"


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _row_to_dict(row: WorkspaceEventRow) -> dict[str, Any]:
    data = row.to_dict()
    data["metadata"] = data.pop("metadata_json", None) or {}
    if isinstance(data.get("created_at"), datetime):
        data["created_at"] = coerce_iso(data["created_at"])
    data["revision"] = data["id"]
    return data


class WorkspaceEventConflictError(ValueError):
    """One idempotency key was reused for different factual content."""


class WorkspaceEventRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def append(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        event_type: str,
        body: str,
        author_run_id: str | None = None,
        event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_body = str(body)
        normalized_event_id = event_id or str(uuid.uuid4())
        content_hash = _body_hash(normalized_body)
        normalized_metadata = dict(metadata or {})
        async with self._sf() as session:
            existing = await session.scalar(
                select(WorkspaceEventRow).where(
                    WorkspaceEventRow.thread_id == thread_id,
                    WorkspaceEventRow.event_id == normalized_event_id,
                )
            )
            if existing is not None:
                return self._same_event_or_raise(
                    existing,
                    user_id=user_id,
                    event_type=event_type,
                    content_hash=content_hash,
                    author_run_id=author_run_id,
                    metadata=normalized_metadata,
                )

            row = WorkspaceEventRow(
                event_id=normalized_event_id,
                thread_id=thread_id,
                user_id=user_id,
                event_type=event_type,
                body=normalized_body,
                metadata_json=normalized_metadata,
                content_hash=content_hash,
                author_run_id=author_run_id,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(WorkspaceEventRow).where(
                        WorkspaceEventRow.thread_id == thread_id,
                        WorkspaceEventRow.event_id == normalized_event_id,
                    )
                )
                if existing is None:
                    raise
                return self._same_event_or_raise(
                    existing,
                    user_id=user_id,
                    event_type=event_type,
                    content_hash=content_hash,
                    author_run_id=author_run_id,
                    metadata=normalized_metadata,
                )
            await session.refresh(row)
            return _row_to_dict(row)

    @staticmethod
    def _same_event_or_raise(
        row: WorkspaceEventRow,
        *,
        user_id: str | None,
        event_type: str,
        content_hash: str,
        author_run_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if row.user_id != user_id or row.event_type != event_type or row.content_hash != content_hash or row.author_run_id != author_run_id or (row.metadata_json or {}) != metadata:
            raise WorkspaceEventConflictError("Workspace event ID is already in use")
        return _row_to_dict(row)

    async def latest(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        event_type: str,
    ) -> dict[str, Any] | None:
        stmt = select(WorkspaceEventRow).where(
            WorkspaceEventRow.thread_id == thread_id,
            WorkspaceEventRow.event_type == event_type,
        )
        if user_id is None:
            stmt = stmt.where(WorkspaceEventRow.user_id.is_(None))
        else:
            stmt = stmt.where(WorkspaceEventRow.user_id == user_id)
        async with self._sf() as session:
            row = await session.scalar(stmt.order_by(WorkspaceEventRow.id.desc()).limit(1))
            return _row_to_dict(row) if row is not None else None

    async def current_context(
        self,
        *,
        thread_id: str,
        user_id: str | None,
    ) -> dict[str, Any]:
        mandate = await self.latest(
            thread_id=thread_id,
            user_id=user_id,
            event_type=GOAL_MANDATE_REVISED,
        )
        brief = await self.latest(
            thread_id=thread_id,
            user_id=user_id,
            event_type=OPERATING_BRIEF_REVISED,
        )
        organization_map = await self.latest(
            thread_id=thread_id,
            user_id=user_id,
            event_type=ORGANIZATION_MAP_REVISED,
        )
        return {
            "goal_mandate": mandate,
            "operating_brief": brief,
            "organization_map": organization_map,
        }

    async def _latest_delivery_through(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        event_type: str,
    ) -> tuple[int, dict[str, Any] | None]:
        row = await self.latest(
            thread_id=thread_id,
            user_id=user_id,
            event_type=event_type,
        )
        through_seq = (row or {}).get("metadata", {}).get("through_seq")
        return (
            through_seq if isinstance(through_seq, int) and through_seq >= 0 else 0,
            row,
        )

    async def result_inbox(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        after_seq: int | None = None,
    ) -> dict[str, Any]:
        acknowledged_through, acknowledgement = await self._latest_delivery_through(
            thread_id=thread_id,
            user_id=user_id,
            event_type=RESULTS_ACKNOWLEDGED,
        )
        notified_through, notification = await self._latest_delivery_through(
            thread_id=thread_id,
            user_id=user_id,
            event_type=RESULTS_NOTIFIED,
        )
        cursor = acknowledged_through if after_seq is None else max(0, after_seq)
        stmt = select(WorkspaceEventRow).where(
            WorkspaceEventRow.thread_id == thread_id,
            WorkspaceEventRow.event_type == RESULT_RECEIVED,
            WorkspaceEventRow.id > cursor,
        )
        if user_id is None:
            stmt = stmt.where(WorkspaceEventRow.user_id.is_(None))
        else:
            stmt = stmt.where(WorkspaceEventRow.user_id == user_id)
        async with self._sf() as session:
            rows = list(await session.scalars(stmt.order_by(WorkspaceEventRow.id.asc())))
        return {
            "acknowledged_through_seq": acknowledged_through,
            "notified_through_seq": notified_through,
            "acknowledgement": acknowledgement,
            "notification": notification,
            "results": [_row_to_dict(row) for row in rows],
        }

    async def pending_results(
        self,
        *,
        thread_id: str,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        inbox = await self.result_inbox(
            thread_id=thread_id,
            user_id=user_id,
            after_seq=0,
        )
        notified_through = inbox["notified_through_seq"]
        return [row for row in inbox["results"] if row["revision"] > notified_through]

    async def acknowledge_results(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        through_seq: int,
        author_run_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        acknowledged_through, _acknowledgement = await self._latest_delivery_through(
            thread_id=thread_id,
            user_id=user_id,
            event_type=RESULTS_ACKNOWLEDGED,
        )
        async with self._sf() as session:
            stmt = select(func.max(WorkspaceEventRow.id)).where(
                WorkspaceEventRow.thread_id == thread_id,
                WorkspaceEventRow.event_type == RESULT_RECEIVED,
            )
            if user_id is None:
                stmt = stmt.where(WorkspaceEventRow.user_id.is_(None))
            else:
                stmt = stmt.where(WorkspaceEventRow.user_id == user_id)
            latest_result_seq = await session.scalar(stmt)
        if latest_result_seq is None or through_seq < 0 or through_seq < acknowledged_through or through_seq > latest_result_seq:
            raise ValueError("Result acknowledgement is outside the factual inbox")
        return await self.append(
            thread_id=thread_id,
            user_id=user_id,
            event_type=RESULTS_ACKNOWLEDGED,
            body=(f"The Chair explicitly acknowledged result inbox through sequence {through_seq}."),
            author_run_id=author_run_id,
            event_id=event_id,
            metadata={"through_seq": through_seq},
        )

    async def history(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        before_revision: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return one owner-scoped, newest-first page of opaque facts."""
        page_limit = min(max(limit, 1), 100)
        stmt = select(WorkspaceEventRow).where(WorkspaceEventRow.thread_id == thread_id)
        if user_id is None:
            stmt = stmt.where(WorkspaceEventRow.user_id.is_(None))
        else:
            stmt = stmt.where(WorkspaceEventRow.user_id == user_id)
        if before_revision is not None:
            stmt = stmt.where(WorkspaceEventRow.id < before_revision)
        async with self._sf() as session:
            rows = list(await session.scalars(stmt.order_by(WorkspaceEventRow.id.desc()).limit(page_limit + 1)))
        has_more = len(rows) > page_limit
        events = [_row_to_dict(row) for row in rows[:page_limit]]
        return {
            "events": events,
            "next_before_revision": (events[-1]["revision"] if has_more and events else None),
        }

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = select(WorkspaceEventRow).where(WorkspaceEventRow.thread_id == thread_id)
        if user_id is not None:
            stmt = stmt.where(WorkspaceEventRow.user_id == user_id)
        async with self._sf() as session:
            rows = await session.scalars(stmt.order_by(WorkspaceEventRow.id.desc()).limit(limit))
            return [_row_to_dict(row) for row in rows]

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        async with self._sf() as session:
            return set(await session.scalars(select(WorkspaceEventRow.user_id).where(WorkspaceEventRow.thread_id == thread_id).distinct()))

    async def claim_legacy_by_thread(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        async with self._sf() as session:
            result = await session.execute(
                update(WorkspaceEventRow)
                .where(
                    WorkspaceEventRow.thread_id == thread_id,
                    or_(
                        WorkspaceEventRow.user_id.is_(None),
                        WorkspaceEventRow.user_id == DEFAULT_USER_ID,
                    ),
                )
                .values(user_id=owner_user_id)
            )
            await session.commit()
            return result.rowcount

    async def delete_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None,
    ) -> None:
        stmt = delete(WorkspaceEventRow).where(WorkspaceEventRow.thread_id == thread_id)
        if user_id is None:
            stmt = stmt.where(WorkspaceEventRow.user_id.is_(None))
        else:
            stmt = stmt.where(WorkspaceEventRow.user_id == user_id)
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

    async def delete_legacy_by_thread(self, thread_id: str) -> None:
        async with self._sf() as session:
            await session.execute(
                delete(WorkspaceEventRow).where(
                    WorkspaceEventRow.thread_id == thread_id,
                    or_(
                        WorkspaceEventRow.user_id.is_(None),
                        WorkspaceEventRow.user_id == DEFAULT_USER_ID,
                    ),
                )
            )
            await session.commit()
