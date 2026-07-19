"""In-memory append-only Goal Workspace facts."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from deerflow.persistence.workspace_event.sql import (
    GOAL_MANDATE_REVISED,
    OPERATING_BRIEF_REVISED,
    ORGANIZATION_MAP_REVISED,
    RESULT_RECEIVED,
    RESULTS_ACKNOWLEDGED,
    RESULTS_NOTIFIED,
    WorkspaceEventConflictError,
    _body_hash,
)
from deerflow.runtime.user_context import DEFAULT_USER_ID
from deerflow.utils.time import now_iso


class MemoryWorkspaceEventStore:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

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
        async with self._lock:
            existing = next(
                (row for row in self._events if row["thread_id"] == thread_id and row["event_id"] == normalized_event_id),
                None,
            )
            if existing is not None:
                if existing["user_id"] != user_id or existing["event_type"] != event_type or existing["content_hash"] != content_hash or existing["author_run_id"] != author_run_id or existing["metadata"] != normalized_metadata:
                    raise WorkspaceEventConflictError("Workspace event ID is already in use")
                return dict(existing)
            revision = len(self._events) + 1
            row = {
                "id": revision,
                "revision": revision,
                "event_id": normalized_event_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "event_type": event_type,
                "body": normalized_body,
                "metadata": normalized_metadata,
                "content_hash": content_hash,
                "author_run_id": author_run_id,
                "created_at": now_iso(),
            }
            self._events.append(row)
            return dict(row)

    async def latest(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        event_type: str,
    ) -> dict[str, Any] | None:
        for row in reversed(self._events):
            if row["thread_id"] == thread_id and row["user_id"] == user_id and row["event_type"] == event_type:
                return dict(row)
        return None

    async def current_context(
        self,
        *,
        thread_id: str,
        user_id: str | None,
    ) -> dict[str, Any]:
        return {
            "goal_mandate": await self.latest(
                thread_id=thread_id,
                user_id=user_id,
                event_type=GOAL_MANDATE_REVISED,
            ),
            "operating_brief": await self.latest(
                thread_id=thread_id,
                user_id=user_id,
                event_type=OPERATING_BRIEF_REVISED,
            ),
            "organization_map": await self.latest(
                thread_id=thread_id,
                user_id=user_id,
                event_type=ORGANIZATION_MAP_REVISED,
            ),
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
        results = [dict(row) for row in self._events if row["thread_id"] == thread_id and row["user_id"] == user_id and row["event_type"] == RESULT_RECEIVED and row["revision"] > cursor]
        return {
            "acknowledged_through_seq": acknowledged_through,
            "notified_through_seq": notified_through,
            "acknowledgement": acknowledgement,
            "notification": notification,
            "results": results,
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
        return [row for row in inbox["results"] if row["revision"] > inbox["notified_through_seq"]]

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
        latest_result_seq = max(
            (row["revision"] for row in self._events if row["thread_id"] == thread_id and row["user_id"] == user_id and row["event_type"] == RESULT_RECEIVED),
            default=None,
        )
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
        async with self._lock:
            rows = [dict(row) for row in reversed(self._events) if row["thread_id"] == thread_id and row["user_id"] == user_id and (before_revision is None or row["revision"] < before_revision)][: page_limit + 1]
        has_more = len(rows) > page_limit
        events = rows[:page_limit]
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
        rows = [row for row in reversed(self._events) if row["thread_id"] == thread_id and (user_id is None or row["user_id"] == user_id)]
        return [dict(row) for row in rows[:limit]]

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        return {row["user_id"] for row in self._events if row["thread_id"] == thread_id}

    async def claim_legacy_by_thread(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        changed = 0
        async with self._lock:
            for row in self._events:
                if row["thread_id"] == thread_id and row["user_id"] in {None, DEFAULT_USER_ID}:
                    row["user_id"] = owner_user_id
                    changed += 1
        return changed

    async def delete_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None,
    ) -> None:
        async with self._lock:
            self._events = [row for row in self._events if not (row["thread_id"] == thread_id and row["user_id"] == user_id)]

    async def delete_legacy_by_thread(self, thread_id: str) -> None:
        async with self._lock:
            self._events = [row for row in self._events if not (row["thread_id"] == thread_id and row["user_id"] in {None, DEFAULT_USER_ID})]
