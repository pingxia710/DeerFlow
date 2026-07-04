from __future__ import annotations

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import _set_terminal_status


class _RunManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RunStatus, str | None]] = []

    async def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        self.calls.append((run_id, status, error))


@pytest.mark.anyio
async def test_set_terminal_status_persists_replay_event_before_status() -> None:
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, user_id="user-1", flush_threshold=100)
    manager = _RunManager()

    await _set_terminal_status(manager, journal, "run-1", RunStatus.error, terminal_reason="failed", error="boom")

    events = await store.list_events("thread-1", "run-1", event_types=["run.terminal"], user_id="user-1")
    assert [event["content"] for event in events] == [
        {"status": "error", "terminal_reason": "failed"},
    ]
    assert manager.calls == [("run-1", RunStatus.error, "boom")]
