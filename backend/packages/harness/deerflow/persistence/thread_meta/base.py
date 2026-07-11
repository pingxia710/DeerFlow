"""Abstract interface for thread metadata storage.

Implementations:
- ThreadMetaRepository: SQL-backed (sqlite / postgres via SQLAlchemy)
- MemoryThreadMetaStore: wraps LangGraph BaseStore (memory mode)

All mutating and querying methods accept a ``user_id`` parameter with
three-state semantics (see :mod:`deerflow.runtime.user_context`):

- ``AUTO`` (default): resolve from the request-scoped contextvar.
- Explicit ``str``: use the provided value verbatim.
- Explicit ``None``: bypass owner filtering (migration/CLI only).
"""

from __future__ import annotations

import abc
from typing import Any

from deerflow.runtime.user_context import AUTO, _AutoSentinel

LEGACY_CLAIM_COMPLETE_METADATA_KEY = "_deerflow_legacy_claim_complete_owner"
LEGACY_CLAIMING_STATUS = "claiming"


def strip_internal_thread_metadata(metadata: dict | None) -> dict:
    """Copy client metadata without repository-owned control fields."""
    cleaned = dict(metadata or {})
    cleaned.pop(LEGACY_CLAIM_COMPLETE_METADATA_KEY, None)
    return cleaned


class InvalidMetadataFilterError(ValueError):
    """Raised when all client-supplied metadata filter keys are rejected."""


class ThreadMetaConflictError(ValueError):
    """Raised when a thread ID is already owned by another user."""


class ThreadMetaCreateResult(dict[str, Any]):
    """A thread row plus whether this call inserted it."""

    def __init__(self, row: dict[str, Any], *, created: bool) -> None:
        super().__init__(row)
        self.created = created


class ThreadMetaStore(abc.ABC):
    @abc.abstractmethod
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
        """Atomically create, return the same-owner row, or raise on owner conflict."""
        pass

    @abc.abstractmethod
    async def get(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> dict | None:
        pass

    @abc.abstractmethod
    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def update_display_name(self, thread_id: str, display_name: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        pass

    @abc.abstractmethod
    async def update_display_name_if_empty(
        self,
        thread_id: str,
        display_name: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> bool:
        """Set the generated title only when no title has been committed."""
        pass

    @abc.abstractmethod
    async def update_status(self, thread_id: str, status: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        pass

    @abc.abstractmethod
    async def update_metadata(self, thread_id: str, metadata: dict, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """Merge ``metadata`` into the thread's metadata field.

        Existing keys are overwritten by the new values; keys absent from
        ``metadata`` are preserved. No-op if the thread does not exist
        or the owner check fails.
        """
        pass

    @abc.abstractmethod
    async def update_owner(self, thread_id: str, owner_user_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """Move a thread metadata row to a new owner.

        Intended for trusted internal repair/migration paths. No-op if the
        row does not exist or the caller fails the owner check.
        """
        pass

    @abc.abstractmethod
    async def claim_legacy_owner(self, thread_id: str, owner_user_id: str) -> bool:
        """Atomically reserve a legacy/default thread for one concrete owner.

        Returns ``True`` when the row was legacy/default-owned or was already
        reserved by the same owner. Returns ``False`` for missing, deleting, or
        foreign-owned rows.
        """
        pass

    @abc.abstractmethod
    async def is_legacy_claim_complete(self, thread_id: str, owner_user_id: str) -> bool:
        """Return whether all legacy surfaces were converged for this owner."""
        pass

    @abc.abstractmethod
    async def mark_legacy_claim_complete(self, thread_id: str, owner_user_id: str) -> bool:
        """Persist the completion marker if ``owner_user_id`` still owns the row."""
        pass

    @abc.abstractmethod
    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        """Check if ``user_id`` has access to ``thread_id``."""
        pass

    @abc.abstractmethod
    async def delete(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        pass
