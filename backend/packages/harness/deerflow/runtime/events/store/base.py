"""Abstract interface for run event storage.

RunEventStore is the unified storage interface for run event streams.
Messages (frontend display), execution traces (debugging/audit), and complete
model-input context snapshots go through the same interface, distinguished by
the ``category`` field.

Implementations:
- MemoryRunEventStore: in-memory dict (development, tests)
- Future: DB-backed store (SQLAlchemy ORM), JSONL file store
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadTimelinePage:
    """One immutable, owner-scoped window of persisted timeline facts."""

    records: list[dict]
    watermark_seq: int
    has_more: bool = False
    truncated: bool = False


class RunEventStore(abc.ABC):
    """Run event stream storage interface.

    All implementations must guarantee:
    1. put() events are retrievable in subsequent queries
    2. seq is strictly increasing within the same thread
    3. list_messages() only returns category="message" events
    4. list_events() returns all events for the specified run
    5. Returned dicts match the RunEvent field structure
    """

    @abc.abstractmethod
    async def put(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_type: str,
        category: str,
        content: str | dict = "",
        metadata: dict | None = None,
        created_at: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """Write an event, auto-assign seq, return the complete record."""

    @abc.abstractmethod
    async def put_batch(self, events: list[dict]) -> list[dict]:
        """Batch-write events. Used by RunJournal flush buffer.

        Each dict's keys match put()'s keyword arguments.
        Returns complete records with seq assigned.
        """

    @abc.abstractmethod
    async def list_messages(
        self,
        thread_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Return displayable messages (category=message) for a thread, ordered by seq ascending.

        Supports bidirectional cursor pagination:
        - before_seq: return the last ``limit`` records with seq < before_seq (ascending)
        - after_seq: return the first ``limit`` records with seq > after_seq (ascending)
        - neither: return the latest ``limit`` records (ascending)
        """

    @abc.abstractmethod
    async def list_events(
        self,
        thread_id: str,
        run_id: str,
        *,
        event_types: list[str] | None = None,
        limit: int = 500,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Return the full event stream for a run, ordered by seq ascending.

        Optionally filter by event_types.
        after_seq returns events with seq > after_seq, for replay cursors.
        """

    @abc.abstractmethod
    async def list_messages_by_run(
        self,
        thread_id: str,
        run_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Return displayable messages (category=message) for a specific run, ordered by seq ascending.

        Supports bidirectional cursor pagination:
        - after_seq: return the first ``limit`` records with seq > after_seq (ascending)
        - before_seq: return the last ``limit`` records with seq < before_seq (ascending)
        - neither: return the latest ``limit`` records (ascending)
        """

    @abc.abstractmethod
    async def read_thread_timeline(
        self,
        thread_id: str,
        *,
        categories: set[str],
        limit: int = 100,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> ThreadTimelinePage:
        """Return one owner-scoped timeline window with its stable watermark.

        ``seq`` is thread-scoped and immutable. The returned records never
        exceed ``watermark_seq``; later writes receive higher sequence values.
        With ``after_seq`` the page advances forward, otherwise it returns the
        latest bounded window and sets ``truncated`` when older facts exist.
        """

    async def claim_legacy_by_thread(self, thread_id: str, owner_user_id: str) -> int:
        """Claim ownerless/default-owned events for a legacy thread when supported."""
        return 0

    async def delete_legacy_by_thread(self, thread_id: str) -> int:
        """Delete ownerless events for a globally unique deleted thread."""
        return 0

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        """Return every persisted owner marker for *thread_id*."""
        raise NotImplementedError

    async def has_events(self, thread_id: str, *, user_id: str | None = None) -> bool:
        """Return whether any event category exists for a thread and owner.

        The default preserves compatibility for older custom stores that only
        implement the messages projection. Built-in stores override this with
        a true all-category, short-circuiting existence probe.
        """
        return bool(await self.count_messages(thread_id, user_id=user_id))

    @abc.abstractmethod
    async def count_messages(self, thread_id: str, *, user_id: str | None = None) -> int:
        """Count displayable messages (category=message) in a thread."""

    @abc.abstractmethod
    async def delete_by_thread(self, thread_id: str, *, user_id: str | None = None) -> int:
        """Delete all events for a thread. Return the number of deleted events."""

    @abc.abstractmethod
    async def delete_by_run(self, thread_id: str, run_id: str, *, user_id: str | None = None) -> int:
        """Delete all events for a specific run. Return the number of deleted events."""
