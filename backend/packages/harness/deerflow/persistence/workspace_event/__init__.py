"""Goal Workspace event persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from deerflow.persistence.workspace_event.memory import MemoryWorkspaceEventStore
from deerflow.persistence.workspace_event.model import WorkspaceEventRow
from deerflow.persistence.workspace_event.sql import (
    GOAL_MANDATE_REVISED,
    OPERATING_BRIEF_REVISED,
    ORGANIZATION_MAP_REVISED,
    RESULT_RECEIVED,
    RESULTS_ACKNOWLEDGED,
    RESULTS_NOTIFIED,
    WorkspaceEventConflictError,
    WorkspaceEventRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "GOAL_MANDATE_REVISED",
    "MemoryWorkspaceEventStore",
    "OPERATING_BRIEF_REVISED",
    "ORGANIZATION_MAP_REVISED",
    "RESULT_RECEIVED",
    "RESULTS_ACKNOWLEDGED",
    "RESULTS_NOTIFIED",
    "WorkspaceEventConflictError",
    "WorkspaceEventRepository",
    "WorkspaceEventRow",
    "make_workspace_event_store",
]


def make_workspace_event_store(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> WorkspaceEventRepository | MemoryWorkspaceEventStore:
    if session_factory is not None:
        return WorkspaceEventRepository(session_factory)
    return MemoryWorkspaceEventStore()
