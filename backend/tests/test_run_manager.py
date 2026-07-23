"""Tests for RunManager."""

import asyncio
import contextlib
import logging
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import DatabaseError as SQLAlchemyDatabaseError

from deerflow.runtime import DisconnectMode, RunManager, RunStatus
from deerflow.runtime.runs.manager import ConflictError, PersistenceRetryPolicy, RunRecord
from deerflow.runtime.runs.store.memory import MemoryRunStore

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@pytest.fixture
def manager() -> RunManager:
    return RunManager()


class FlakyStatusRunStore(MemoryRunStore):
    """Memory run store that simulates transient SQLite status-write failures."""

    def __init__(self, *, status_failures: int) -> None:
        super().__init__()
        self.status_failures = status_failures
        self.status_update_attempts = 0

    async def update_status(self, run_id, status, *, error=None, terminal_reason=None):
        self.status_update_attempts += 1
        if self.status_failures > 0:
            self.status_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return await super().update_status(run_id, status, error=error, terminal_reason=terminal_reason)


class FlakyProgressRunStore(MemoryRunStore):
    """Memory run store that simulates transient SQLite progress-write failures."""

    def __init__(self, *, progress_failures: int) -> None:
        super().__init__()
        self.progress_failures = progress_failures
        self.progress_update_attempts = 0

    async def update_run_progress(self, run_id, **kwargs):
        self.progress_update_attempts += 1
        if self.progress_failures > 0:
            self.progress_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return await super().update_run_progress(run_id, **kwargs)


class MissingRowStatusRunStore(MemoryRunStore):
    """Memory run store that reports a missing row for status updates."""

    async def update_status(self, run_id, status, *, error=None, terminal_reason=None):
        await super().update_status(run_id, status, error=error, terminal_reason=terminal_reason)
        return False


class PermanentStatusRunStore(MemoryRunStore):
    """Memory run store that simulates a permanent SQLAlchemy write failure."""

    def __init__(self) -> None:
        super().__init__()
        self.status_update_attempts = 0

    async def update_status(self, run_id, status, *, error=None, terminal_reason=None):
        self.status_update_attempts += 1
        raise SQLAlchemyDatabaseError(
            "UPDATE runs SET status = :status WHERE run_id = :run_id",
            {"status": status, "run_id": run_id},
            sqlite3.DatabaseError("no such table: runs"),
        )


class FailingStatusRunStore(MemoryRunStore):
    """Memory run store that always fails status updates."""

    def __init__(self) -> None:
        super().__init__()
        self.status_update_attempts = 0

    async def update_status(self, run_id, status, *, error=None, terminal_reason=None):
        self.status_update_attempts += 1
        raise sqlite3.OperationalError("database is locked")


class MissingCompletionRunStore(MemoryRunStore):
    """Memory run store that reports one missing row for completion updates."""

    def __init__(self) -> None:
        super().__init__()
        self.completion_update_attempts = 0

    async def update_run_completion(self, run_id, *, status, **kwargs):
        self.completion_update_attempts += 1
        if self.completion_update_attempts == 1:
            return False
        return await super().update_run_completion(run_id, status=status, **kwargs)


class AlwaysMissingCompletionRunStore(MemoryRunStore):
    """Memory run store that keeps reporting missing rows for completion updates."""

    def __init__(self) -> None:
        super().__init__()
        self.completion_update_attempts = 0

    async def update_run_completion(self, run_id, *, status, **kwargs):
        self.completion_update_attempts += 1
        return False


class RejectingCompleteRunStore(MemoryRunStore):
    """Memory run store that rejects lease terminal CAS."""

    async def complete_run(self, run_id: str, **kwargs):
        return False


class RecordingRecoveryRunStore(MemoryRunStore):
    """Memory run store that records manager-level expired lease recovery calls."""

    def __init__(self) -> None:
        super().__init__()
        self.list_expired_calls = 0
        self.recover_calls: list[tuple[str, int]] = []

    async def list_expired_active_leases(self, now: datetime):
        self.list_expired_calls += 1
        return await super().list_expired_active_leases(now)

    async def recover_expired_lease(self, run_id: str, *, generation: int, **kwargs):
        self.recover_calls.append((run_id, generation))
        return await super().recover_expired_lease(run_id, generation=generation, **kwargs)


async def _stored_statuses(store: MemoryRunStore, *run_ids: str) -> dict[str, Any]:
    rows = {}
    for run_id in run_ids:
        row = await store.get(run_id)
        rows[run_id] = row["status"] if row else None
    return rows


@pytest.mark.anyio
async def test_create_and_get(manager: RunManager):
    """Created run should be retrievable with new fields."""
    record = await manager.create(
        "thread-1",
        "lead_agent",
        metadata={"key": "val"},
        kwargs={"input": {}},
        multitask_strategy="reject",
    )
    assert record.status == RunStatus.pending
    assert record.thread_id == "thread-1"
    assert record.assistant_id == "lead_agent"
    assert record.metadata == {"key": "val"}
    assert record.kwargs == {"input": {}}
    assert record.multitask_strategy == "reject"
    assert ISO_RE.match(record.created_at)
    assert ISO_RE.match(record.updated_at)

    fetched = await manager.get(record.run_id)
    assert fetched is record


@pytest.mark.anyio
async def test_hydrates_store_only_recovery_status() -> None:
    """Store-only recovery statuses should stay readable as historical runs."""
    store = MemoryRunStore()
    await store.put("lost-run", thread_id="thread-1", status="worker_lost")
    manager = RunManager(store=store)

    fetched = await manager.get("lost-run")
    rows = await manager.list_by_thread("thread-1")

    assert fetched is not None
    assert fetched.status == "worker_lost"
    assert [row.status for row in rows] == ["worker_lost"]


def test_record_from_store_preserves_top_level_completion_time() -> None:
    record = RunManager._record_from_store(
        {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "status": "success",
            "created_at": "2026-06-20T10:00:00Z",
            "updated_at": "2026-07-20T10:00:00Z",
            "completed_at": "2026-06-20T10:00:05Z",
        }
    )

    assert record.metadata["completed_at"] == "2026-06-20T10:00:05Z"


@pytest.mark.anyio
async def test_status_transitions(manager: RunManager):
    """Status should transition pending -> running -> success."""
    record = await manager.create("thread-1")
    assert record.status == RunStatus.pending

    await manager.set_status(record.run_id, RunStatus.running)
    assert record.status == RunStatus.running
    assert ISO_RE.match(record.updated_at)

    await manager.set_status(record.run_id, RunStatus.success)
    assert record.status == RunStatus.success


@pytest.mark.anyio
async def test_cancel(manager: RunManager):
    """Cancel should set abort_event and transition to interrupted."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    cancelled = await manager.cancel(record.run_id)
    assert cancelled is True
    assert record.abort_event.is_set()
    assert record.status == RunStatus.interrupted


@pytest.mark.anyio
async def test_cancel_persists_interrupted_status_to_store():
    """Cancel should persist interrupted status to the backing store."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    cancelled = await manager.cancel(record.run_id)

    stored = await store.get(record.run_id)
    assert cancelled is True
    assert stored is not None
    assert stored["status"] == "interrupted"


@pytest.mark.anyio
@pytest.mark.parametrize("late_status", [RunStatus.running, RunStatus.success, RunStatus.error])
async def test_late_success_cannot_overwrite_cancelled_terminal_status(late_status: RunStatus):
    """Cancel terminal status should win over late worker status writes."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    assert await manager.cancel(record.run_id) is True
    original_updated_at = record.updated_at
    original_stored = await store.get(record.run_id)
    assert original_stored is not None
    original_stored_updated_at = original_stored["updated_at"]
    await manager.set_status(record.run_id, late_status, error="late boom" if late_status == RunStatus.error else None)

    stored = await store.get(record.run_id)
    assert record.status == RunStatus.interrupted
    assert record.error is None
    assert record.updated_at == original_updated_at
    assert stored is not None
    assert stored["status"] == "interrupted"
    assert stored["error"] is None
    assert stored["updated_at"] == original_stored_updated_at


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_status", "terminal_error", "late_status", "late_error"),
    [
        (RunStatus.success, None, RunStatus.error, "late boom"),
        (RunStatus.success, None, RunStatus.running, None),
        (RunStatus.error, "boom", RunStatus.success, None),
        (RunStatus.error, "boom", RunStatus.running, None),
    ],
)
async def test_terminal_status_blocks_late_different_status(terminal_status: RunStatus, terminal_error: str | None, late_status: RunStatus, late_error: str | None):
    """Once a run is terminal, a later different status must not replace it."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)
    await manager.set_status(record.run_id, terminal_status, error=terminal_error)
    original_updated_at = record.updated_at
    original_stored = await store.get(record.run_id)
    assert original_stored is not None
    original_stored_updated_at = original_stored["updated_at"]

    await manager.set_status(record.run_id, late_status, error=late_error)

    stored = await store.get(record.run_id)
    assert record.status == terminal_status
    assert record.error == terminal_error
    assert record.updated_at == original_updated_at
    assert stored is not None
    assert stored["status"] == terminal_status.value
    assert stored.get("error") == terminal_error
    assert stored["updated_at"] == original_stored_updated_at


@pytest.mark.anyio
@pytest.mark.parametrize(("terminal_status", "terminal_error"), [(RunStatus.success, None), (RunStatus.error, "boom")])
async def test_same_terminal_status_write_is_idempotent(terminal_status: RunStatus, terminal_error: str | None):
    """Repeating the same terminal status is allowed for idempotent retries."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)
    await manager.set_status(record.run_id, terminal_status, error=terminal_error)

    await manager.set_status(record.run_id, terminal_status, error=terminal_error)

    stored = await store.get(record.run_id)
    assert record.status == terminal_status
    assert record.error == terminal_error
    assert stored is not None
    assert stored["status"] == terminal_status.value
    assert stored.get("error") == terminal_error


@pytest.mark.anyio
async def test_rollback_cancel_allows_worker_error_terminal_status():
    """Rollback cancellation keeps the existing interrupted -> error completion path."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    assert await manager.cancel(record.run_id, action="rollback") is True
    stored = await store.get(record.run_id)
    assert record.status == RunStatus.interrupted
    assert stored is not None
    assert stored["status"] == "interrupted"

    await manager.set_status(record.run_id, RunStatus.error, error="Rolled back by user")

    stored = await store.get(record.run_id)
    assert record.status == RunStatus.error
    assert record.error == "Rolled back by user"
    assert stored is not None
    assert stored["status"] == "error"
    assert stored["error"] == "Rolled back by user"


@pytest.mark.anyio
async def test_status_persistence_retries_transient_sqlite_lock():
    """Transient SQLite lock errors should not leave a final status stale."""
    store = FlakyStatusRunStore(status_failures=2)
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    await manager.set_status(record.run_id, RunStatus.success)

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == "success"
    assert store.status_update_attempts >= 4


@pytest.mark.anyio
async def test_status_persistence_does_not_recreate_deleted_store_row():
    """A missing row is an explicit deletion boundary, not a repair invitation."""
    store = MissingRowStatusRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await store.delete(record.run_id)

    committed = await manager.set_status(record.run_id, RunStatus.error, error="boom")

    stored = await store.get(record.run_id)
    assert committed is False
    assert stored is None
    assert record.status == RunStatus.pending


@pytest.mark.anyio
async def test_status_persistence_does_not_retry_permanent_sqlalchemy_errors():
    """Permanent SQLAlchemy failures should not be retried as SQLite pressure."""
    store = PermanentStatusRunStore()
    manager = RunManager(
        store=store,
        persistence_retry_policy=PersistenceRetryPolicy(max_attempts=5, initial_delay=0),
    )
    record = await manager.create("thread-1")

    await manager.set_status(record.run_id, RunStatus.error, error="boom")

    assert store.status_update_attempts == 1


@pytest.mark.anyio
async def test_completion_persistence_does_not_recreate_deleted_store_row():
    """Late completion counters must not resurrect an explicitly deleted run."""
    store = MissingCompletionRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)
    await manager.set_status(record.run_id, RunStatus.success)
    await store.delete(record.run_id)

    await manager.update_run_completion(
        record.run_id,
        status="success",
        total_tokens=42,
        llm_call_count=2,
        last_ai_message="done",
    )

    stored = await store.get(record.run_id)
    assert stored is None
    assert store.completion_update_attempts == 1


@pytest.mark.anyio
async def test_completion_persistence_warns_when_store_row_is_missing(caplog):
    """A zero-row completion update should fail closed and remain observable."""
    store = AlwaysMissingCompletionRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.success)
    caplog.set_level(logging.WARNING, logger="deerflow.runtime.runs.manager")

    await manager.update_run_completion(record.run_id, status="success", total_tokens=42)

    assert store.completion_update_attempts == 1
    assert "affected no rows; refusing to recreate" in caplog.text


@pytest.mark.anyio
async def test_external_subagent_usage_survives_source_run_completion():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    assert await manager.record_external_subagent_usage(
        record.run_id,
        source_run_id="codex-cli:task-1",
        model_name="gpt-5.6-terra",
        input_tokens=12,
        output_tokens=3,
        total_tokens=15,
    )
    assert not await manager.record_external_subagent_usage(
        record.run_id,
        source_run_id="codex-cli:task-1",
        model_name="gpt-5.6-terra",
        input_tokens=12,
        output_tokens=3,
        total_tokens=15,
    )

    await manager.set_status(record.run_id, RunStatus.success)

    await manager.update_run_completion(
        record.run_id,
        status="success",
        total_input_tokens=100,
        total_output_tokens=20,
        total_tokens=120,
        lead_agent_tokens=120,
    )

    stored = await store.get(record.run_id)
    assert stored["total_input_tokens"] == 112
    assert stored["total_output_tokens"] == 23
    assert stored["total_tokens"] == 135
    assert stored["subagent_tokens"] == 15
    assert stored["token_usage_by_model"]["gpt-5.6-terra"] == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}


@pytest.mark.anyio
async def test_progress_persistence_retries_transient_sqlite_lock():
    """Running progress snapshots should survive short SQLite write pressure."""
    store = FlakyProgressRunStore(progress_failures=2)
    manager = RunManager(
        store=store,
        persistence_retry_policy=PersistenceRetryPolicy(max_attempts=4, initial_delay=0),
    )
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    await manager.update_run_progress(record.run_id, total_tokens=42, message_count=2)

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["total_tokens"] == 42
    assert stored["message_count"] == 2
    assert store.progress_update_attempts == 3


@pytest.mark.anyio
async def test_reconcile_orphaned_inflight_runs_marks_stale_rows_error():
    """Startup recovery should turn persisted active rows into explicit errors."""
    store = MemoryRunStore()
    await store.put("pending-run", thread_id="thread-1", status="pending", created_at="2026-01-01T00:00:00+00:00")
    await store.put("running-run", thread_id="thread-1", status="running", created_at="2026-01-01T00:00:01+00:00")
    await store.put("cancelling-run", thread_id="thread-1", status="cancelling", created_at="2026-01-01T00:00:02+00:00")
    await store.put("rolling-back-run", thread_id="thread-1", status="rolling_back", created_at="2026-01-01T00:00:03+00:00")
    await store.put("success-run", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:04+00:00")
    manager = RunManager(store=store)

    recovered = await manager.reconcile_orphaned_inflight_runs(
        error="Gateway restarted before this run reached a durable final state.",
        before="2026-01-01T00:00:04+00:00",
    )

    assert {record.run_id for record in recovered} == {"pending-run", "running-run", "cancelling-run", "rolling-back-run"}
    assert await _stored_statuses(store, "pending-run", "running-run", "cancelling-run", "rolling-back-run", "success-run") == {
        "pending-run": "error",
        "running-run": "error",
        "cancelling-run": "error",
        "rolling-back-run": "error",
        "success-run": "success",
    }
    for run_id in ("pending-run", "running-run", "cancelling-run", "rolling-back-run"):
        stored = await store.get(run_id)
        assert stored is not None
        assert stored["terminal_reason"] == "worker_lost"


@pytest.mark.anyio
async def test_reconcile_orphaned_inflight_runs_skips_live_local_run():
    """Startup recovery should not mark an active row orphaned when this worker owns it."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    recovered = await manager.reconcile_orphaned_inflight_runs(
        error="Gateway restarted before this run reached a durable final state.",
    )

    stored = await store.get(record.run_id)
    assert recovered == []
    assert stored["status"] == "running"


@pytest.mark.anyio
async def test_reconcile_orphaned_inflight_runs_skips_rows_when_error_status_is_not_persisted():
    """Startup recovery must not report a row as recovered if the error update failed."""
    store = FailingStatusRunStore()
    await store.put("running-run", thread_id="thread-1", status="running", created_at="2026-01-01T00:00:00+00:00")
    manager = RunManager(
        store=store,
        persistence_retry_policy=PersistenceRetryPolicy(max_attempts=2, initial_delay=0),
    )

    recovered = await manager.reconcile_orphaned_inflight_runs(
        error="Gateway restarted before this run reached a durable final state.",
        before="2026-01-01T00:00:01+00:00",
    )

    stored = await store.get("running-run")
    assert recovered == []
    assert stored["status"] == "running"
    assert store.status_update_attempts == 2


@pytest.mark.anyio
async def test_list_by_thread_recovers_stale_store_only_inflight_run():
    """Read-side recovery should turn stale persisted active rows into worker_lost."""
    store = MemoryRunStore()
    await store.put("stale-run", thread_id="thread-1", status="running", created_at="2026-01-01T00:00:00+00:00")
    store._runs["stale-run"]["updated_at"] = "2026-01-01T00:00:00+00:00"
    manager = RunManager(store=store)

    rows = await manager.list_by_thread("thread-1")

    stored = await store.get("stale-run")
    assert rows[0].run_id == "stale-run"
    assert rows[0].status == RunStatus.error
    assert rows[0].terminal_reason == "worker_lost"
    assert stored["status"] == "error"
    assert stored["terminal_reason"] == "worker_lost"


@pytest.mark.anyio
async def test_recover_stale_inflight_runs_uses_expired_active_lease_api():
    """Manager recovery must call the lease recovery API, not only stale updated_at fallback."""
    store = RecordingRecoveryRunStore()
    now = datetime.now(UTC)
    await store.create_pending_run("expired-run", thread_id="thread-lease")
    lease = await store.try_acquire_active_slot(
        "thread-lease",
        "expired-run",
        owner_worker_id="worker-a",
        lease_expires_at=now - timedelta(seconds=1),
        now=now,
    )
    assert lease is not None
    manager = RunManager(store=store)

    recovered = await manager.recover_stale_inflight_runs(thread_id="thread-lease")

    stored = await store.get("expired-run")
    assert store.list_expired_calls == 1
    assert store.recover_calls == [("expired-run", lease.generation)]
    assert [record.run_id for record in recovered] == ["expired-run"]
    assert recovered[0].status == RunStatus.error
    assert recovered[0].terminal_reason == "lease_expired_recovered"
    assert stored is not None
    assert stored["status"] == "error"


@pytest.mark.anyio
async def test_stale_recovery_skips_live_local_task_without_expired_lease():
    """Ordinary stale updated_at recovery must not cancel a still-live local task."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)
    store._runs[record.run_id]["updated_at"] = "2026-01-01T00:00:00+00:00"
    task = asyncio.create_task(asyncio.sleep(60))
    record.task = task

    try:
        recovered = await manager.recover_stale_inflight_runs(
            before="2026-01-01T00:30:00+00:00",
        )
        stored = await store.get(record.run_id)
        assert recovered == []
        assert not task.cancelled()
        assert not record.abort_event.is_set()
        assert record.status == RunStatus.running
        assert record.terminal_reason is None
        assert stored["status"] == "running"
        assert stored.get("terminal_reason") is None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_cancel_not_inflight(manager: RunManager):
    """Cancelling a completed run should return False."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.success)

    cancelled = await manager.cancel(record.run_id)
    assert cancelled is False


@pytest.mark.anyio
async def test_list_by_thread(manager: RunManager):
    """Same thread should return multiple runs."""
    r1 = await manager.create("thread-1")
    r2 = await manager.create("thread-1")
    await manager.create("thread-2")

    runs = await manager.list_by_thread("thread-1")
    assert len(runs) == 2
    # Newest first: r2 was created after r1.
    assert runs[0].run_id == r2.run_id
    assert runs[1].run_id == r1.run_id


@pytest.mark.anyio
async def test_list_by_thread_is_stable_when_timestamps_tie(manager: RunManager, monkeypatch: pytest.MonkeyPatch):
    """Equal timestamps use the immutable run ID as a stable cursor tie-breaker."""
    monkeypatch.setattr("deerflow.runtime.runs.manager._now_iso", lambda: "2026-01-01T00:00:00+00:00")

    r1 = await manager.create("thread-1")
    r2 = await manager.create("thread-1")

    runs = await manager.list_by_thread("thread-1")
    assert [run.run_id for run in runs] == sorted(
        [r1.run_id, r2.run_id],
        reverse=True,
    )


@pytest.mark.anyio
async def test_has_inflight(manager: RunManager):
    """has_inflight should be True when a run is pending or running."""
    record = await manager.create("thread-1")
    assert await manager.has_inflight("thread-1") is True

    await manager.set_status(record.run_id, RunStatus.success)
    assert await manager.has_inflight("thread-1") is False


@pytest.mark.anyio
async def test_cleanup(manager: RunManager):
    """After cleanup, the run should be gone."""
    record = await manager.create("thread-1")
    run_id = record.run_id

    await manager.cleanup(run_id, delay=0)
    assert await manager.get(run_id) is None


@pytest.mark.anyio
async def test_set_status_with_error(manager: RunManager):
    """Error message should be stored on the record."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.error, error="Something went wrong")
    assert record.status == RunStatus.error
    assert record.error == "Something went wrong"


@pytest.mark.anyio
async def test_set_status_persists_terminal_reason():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")

    await manager.set_status(record.run_id, RunStatus.error, terminal_reason="failed")
    hydrated = await RunManager(store=store).get(record.run_id)

    assert record.terminal_reason == "failed"
    assert hydrated is not None
    assert hydrated.store_only is True
    assert hydrated.terminal_reason == "failed"


@pytest.mark.anyio
async def test_late_terminal_reason_cannot_overwrite_existing_reason():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")

    await manager.set_status(record.run_id, RunStatus.error, error="first", terminal_reason="failed")
    await manager.set_status(record.run_id, RunStatus.error, error="late", terminal_reason="worker_lost")
    hydrated = await RunManager(store=store).get(record.run_id)

    assert record.error == "first"
    assert record.terminal_reason == "failed"
    assert hydrated is not None
    assert hydrated.error == "first"
    assert hydrated.terminal_reason == "failed"


@pytest.mark.anyio
async def test_rollback_cancel_leaves_terminal_reason_for_worker_completion():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    record.task = asyncio.create_task(asyncio.sleep(60))

    assert await manager.cancel(record.run_id, action="rollback") is True
    await manager.set_status(record.run_id, RunStatus.error, error="Rolled back by user", terminal_reason="rolled_back")
    hydrated = await RunManager(store=store).get(record.run_id)

    assert record.status == RunStatus.error
    assert record.terminal_reason == "rolled_back"
    assert hydrated is not None
    assert hydrated.status == RunStatus.error
    assert hydrated.terminal_reason == "rolled_back"
    with contextlib.suppress(asyncio.CancelledError):
        await record.task


@pytest.mark.anyio
async def test_get_nonexistent(manager: RunManager):
    """Getting a nonexistent run should return None."""
    assert await manager.get("does-not-exist") is None


@pytest.mark.anyio
async def test_get_hydrates_store_only_run():
    """Store-only runs should be readable after process restart."""
    store = MemoryRunStore()
    await store.put(
        "run-store-only",
        thread_id="thread-1",
        assistant_id="lead_agent",
        status="success",
        multitask_strategy="reject",
        metadata={"source": "store"},
        kwargs={"input": "value"},
        created_at="2026-01-01T00:00:00+00:00",
        model_name="model-a",
    )
    manager = RunManager(store=store)

    record = await manager.get("run-store-only")

    assert record is not None
    assert record.run_id == "run-store-only"
    assert record.thread_id == "thread-1"
    assert record.assistant_id == "lead_agent"
    assert record.status == RunStatus.success
    assert record.on_disconnect == DisconnectMode.cancel
    assert record.metadata == {"source": "store"}
    assert record.kwargs == {"input": "value"}
    assert record.model_name == "model-a"
    assert record.task is None
    assert record.store_only is True


@pytest.mark.anyio
async def test_get_hydrates_run_with_null_enum_fields():
    """Rows with NULL status/on_disconnect must hydrate with safe defaults, not raise."""
    store = MemoryRunStore()
    # Simulate a SQL row where the nullable status column is NULL
    await store.put(
        "run-null-status",
        thread_id="thread-1",
        status=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    manager = RunManager(store=store)

    record = await manager.get("run-null-status")

    assert record is not None
    assert record.status == RunStatus.pending
    assert record.on_disconnect == DisconnectMode.cancel
    assert record.store_only is True


@pytest.mark.anyio
async def test_list_by_thread_hydrates_run_with_null_enum_fields():
    """list_by_thread must not skip rows with NULL status; applies safe defaults."""
    store = MemoryRunStore()
    await store.put(
        "run-null-status-list",
        thread_id="thread-null",
        status=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    manager = RunManager(store=store)

    runs = await manager.list_by_thread("thread-null")

    assert len(runs) == 1
    assert runs[0].run_id == "run-null-status-list"
    assert runs[0].status == RunStatus.pending
    assert runs[0].on_disconnect == DisconnectMode.cancel


@pytest.mark.anyio
async def test_create_record_is_not_store_only(manager: RunManager):
    """In-memory records created via create() must have store_only=False."""
    record = await manager.create("thread-1")
    assert record.store_only is False


@pytest.mark.anyio
async def test_create_rolls_back_in_memory_record_on_store_failure():
    """create() must fail and hide the run when the initial store write fails."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    store.put = AsyncMock(side_effect=RuntimeError("db down"))
    manager = RunManager(store=store)

    with pytest.raises(RuntimeError, match="db down"):
        await manager.create("thread-1")

    assert manager._runs == {}
    assert await manager.list_by_thread("thread-1") == []


@pytest.mark.anyio
async def test_create_rolls_back_in_memory_record_on_store_cancellation():
    """create() must also roll back when cancelled during the initial store write."""
    store = MemoryRunStore()

    async def cancelled_put(run_id, **kwargs):
        raise asyncio.CancelledError

    store.put = cancelled_put
    manager = RunManager(store=store)

    with pytest.raises(asyncio.CancelledError):
        await manager.create("thread-1")

    assert manager._runs == {}
    assert await manager.list_by_thread("thread-1") == []


@pytest.mark.anyio
async def test_create_does_not_expose_run_until_store_persist_completes():
    """Concurrent readers must wait until the new run has been persisted."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    original_put = store.put
    put_started = asyncio.Event()
    allow_put = asyncio.Event()

    async def blocking_put(run_id, **kwargs):
        put_started.set()
        await allow_put.wait()
        return await original_put(run_id, **kwargs)

    store.put = blocking_put
    create_task = asyncio.create_task(manager.create("thread-1"))
    try:
        await put_started.wait()
        assert await manager.list_by_thread("thread-1") == []

        allow_put.set()
        record = await create_task
        runs = await manager.list_by_thread("thread-1")

        assert [run.run_id for run in runs] == [record.run_id]
    finally:
        allow_put.set()
        cleanup_tasks = []
        for task in (create_task,):
            if not task.done():
                task.cancel()
            cleanup_tasks.append(task)
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_cancelled_create_rolls_back_persisted_run_and_round_binding():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    second_put_started = asyncio.Event()

    class BlockingSecondPutStore(MemoryRunStore):
        def __init__(self) -> None:
            super().__init__()
            self.put_count = 0

        async def put(self, run_id: str, *, thread_id: str, **kwargs):
            self.put_count += 1
            if self.put_count == 2:
                second_put_started.set()
                await asyncio.Event().wait()
            return await super().put(run_id, thread_id=thread_id, **kwargs)

    store = BlockingSecondPutStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=store, round_store=round_store)
    create_task = asyncio.create_task(manager.create("thread-cancelled-create", user_id="owner-a"))

    await second_put_started.wait()
    create_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await create_task

    assert (
        await store.list_by_thread(
            "thread-cancelled-create",
            user_id="owner-a",
        )
        == []
    )
    assert (
        await round_store.list_by_thread(
            "thread-cancelled-create",
            user_id="owner-a",
        )
        == []
    )
    assert (
        await manager.list_by_thread(
            "thread-cancelled-create",
            user_id="owner-a",
        )
        == []
    )


@pytest.mark.anyio
async def test_get_prefers_in_memory_record_over_store():
    """In-memory records retain task/control state when store has same run."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await store.update_status(record.run_id, "success")

    fetched = await manager.get(record.run_id)

    assert fetched is record
    assert fetched.status == RunStatus.pending


@pytest.mark.anyio
async def test_list_by_thread_merges_store_runs_newest_first():
    """list_by_thread should merge memory and store rows with memory precedence."""
    store = MemoryRunStore()
    await store.put("old-store", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:00+00:00")
    await store.put("other-thread", thread_id="thread-2", status="success", created_at="2026-01-03T00:00:00+00:00")
    manager = RunManager(store=store)
    memory_record = await manager.create("thread-1")

    runs = await manager.list_by_thread("thread-1")

    assert [run.run_id for run in runs] == [memory_record.run_id, "old-store"]
    assert runs[0] is memory_record


@pytest.mark.anyio
async def test_create_defaults(manager: RunManager):
    """Create with no optional args should use defaults."""
    record = await manager.create("thread-1")
    assert record.metadata == {}
    assert record.kwargs == {}
    assert record.multitask_strategy == "reject"
    assert record.assistant_id is None


@pytest.mark.anyio
async def test_model_name_create_or_reject():
    """create_or_reject should accept and persist model_name."""
    from deerflow.runtime.runs.schemas import DisconnectMode

    store = MemoryRunStore()
    mgr = RunManager(store=store)

    record = await mgr.create_or_reject(
        "thread-1",
        assistant_id="lead_agent",
        on_disconnect=DisconnectMode.cancel,
        metadata={"key": "val"},
        kwargs={"input": {}},
        multitask_strategy="reject",
        model_name="anthropic.claude-sonnet-4-20250514-v1:0",
    )
    assert record.model_name == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert record.status == RunStatus.pending

    # Verify model_name was persisted to store
    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["model_name"] == "anthropic.claude-sonnet-4-20250514-v1:0"

    # Verify retrieval returns the model_name via in-memory record
    fetched = await mgr.get(record.run_id)
    assert fetched is not None
    assert fetched.model_name == "anthropic.claude-sonnet-4-20250514-v1:0"


@pytest.mark.anyio
async def test_create_or_reject_reject_uses_active_slot_across_managers():
    store = MemoryRunStore()
    manager_a = RunManager(store=store, worker_id="worker-a")
    manager_b = RunManager(store=store, worker_id="worker-b")

    results = await asyncio.gather(
        manager_a.create_or_reject("thread-lease", multitask_strategy="reject"),
        manager_b.create_or_reject("thread-lease", multitask_strategy="reject"),
        return_exceptions=True,
    )

    records = [result for result in results if isinstance(result, RunRecord)]
    conflicts = [result for result in results if isinstance(result, ConflictError)]
    assert len(records) == 1
    assert len(conflicts) == 1
    assert records[0].lease_token is not None
    assert records[0].lease_generation is not None

    inflight = await store.list_inflight()
    assert [row["run_id"] for row in inflight] == [records[0].run_id]
    assert inflight[0]["status"] == "running"


@pytest.mark.anyio
async def test_create_or_reject_store_write_failure_leaves_no_round():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class FailingPendingRunStore(MemoryRunStore):
        async def create_pending_run(self, run_id, *, thread_id, **kwargs):
            raise ValueError("pending run write failed")

    round_store = MemoryRoundStateStore()
    manager = RunManager(store=FailingPendingRunStore(), round_store=round_store)

    with pytest.raises(ValueError, match="pending run write failed"):
        await manager.create_or_reject("thread-round-write-failure")

    assert await round_store.list_by_thread("thread-round-write-failure") == []


@pytest.mark.anyio
async def test_create_or_reject_slot_conflict_preserves_active_round():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class HiddenInflightRunStore(MemoryRunStore):
        hide_inflight = False

        async def list_inflight(self, *, before=None):
            if self.hide_inflight:
                return []
            return await super().list_inflight(before=before)

    store = HiddenInflightRunStore()
    round_store = MemoryRoundStateStore()
    owner = RunManager(store=store, round_store=round_store, worker_id="owner")
    active = await owner.create_or_reject(
        "thread-round-slot-conflict",
        kwargs={"input": {"messages": [{"role": "user", "content": "original intent"}]}},
    )
    [before] = await round_store.list_by_thread("thread-round-slot-conflict")
    before_events = list(round_store.events[before["round_id"]])
    store.hide_inflight = True

    contender = RunManager(store=store, round_store=round_store, worker_id="contender")
    with pytest.raises(ConflictError, match="already has an active run"):
        await contender.create_or_reject(
            "thread-round-slot-conflict",
            kwargs={"input": {"messages": [{"role": "user", "content": "contender intent"}]}},
        )

    [after] = await round_store.list_by_thread("thread-round-slot-conflict")
    assert after["current_run_id"] == active.run_id
    assert after["current_intent"] == "original intent"
    assert round_store.events[after["round_id"]] == before_events


@pytest.mark.anyio
async def test_create_or_reject_round_bind_cancellation_compensates_slot_run_and_round(monkeypatch):
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=store, round_store=round_store)

    bind_round = manager._bind_round_to_run

    async def cancel_after_round_bind(record):
        await bind_round(record)
        [bound_round] = await round_store.list_by_thread("thread-round-cancel")
        assert bound_round["current_run_id"] == record.run_id
        assert record.metadata["round_id"] == bound_round["round_id"]
        raise asyncio.CancelledError

    monkeypatch.setattr(manager, "_bind_round_to_run", cancel_after_round_bind)

    with pytest.raises(asyncio.CancelledError):
        await manager.create_or_reject("thread-round-cancel")

    assert await round_store.list_by_thread("thread-round-cancel") == []
    assert await store.list_by_thread("thread-round-cancel") == []
    assert store._active_slots == {}
    assert manager._runs == {}


@pytest.mark.anyio
async def test_create_or_reject_cancellation_after_slot_commit_compensates_run_and_slot():
    class CancelAfterSlotCommitStore(MemoryRunStore):
        async def try_acquire_active_slot(self, *args, **kwargs):
            lease = await super().try_acquire_active_slot(*args, **kwargs)
            assert lease is not None
            raise asyncio.CancelledError

    store = CancelAfterSlotCommitStore()
    manager = RunManager(store=store)

    with pytest.raises(asyncio.CancelledError):
        await manager.create_or_reject("thread-slot-commit-cancel")

    assert await store.list_by_thread("thread-slot-commit-cancel") == []
    assert store._active_slots == {}
    assert manager._runs == {}


@pytest.mark.anyio
async def test_round_bind_cannot_revive_run_recovered_after_lease_expiry():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class BlockingRoundStore(MemoryRoundStateStore):
        def __init__(self):
            super().__init__()
            self.bound = asyncio.Event()
            self.allow_return = asyncio.Event()

        async def bind_run(self, **kwargs):
            result = await super().bind_run(**kwargs)
            self.bound.set()
            await self.allow_return.wait()
            return result

    store = MemoryRunStore()
    round_store = BlockingRoundStore()
    manager = RunManager(store=store, round_store=round_store)
    create_task = asyncio.create_task(manager.create_or_reject("thread-round-lease-race"))

    await round_store.bound.wait()
    lease = store._active_slots["thread-round-lease-race"]
    now = datetime.now(UTC)
    store._active_slots["thread-round-lease-race"] = replace(
        lease,
        lease_expires_at=now - timedelta(seconds=1),
    )
    assert await store.recover_expired_lease(
        lease.run_id,
        generation=lease.generation,
        now=now,
        error="worker lost during round bind",
    )
    round_store.allow_return.set()

    with pytest.raises(ConflictError, match="lost its active lease"):
        await create_task

    stored = await store.get(lease.run_id)
    assert stored is not None
    assert stored["status"] == RunStatus.error.value
    assert stored["terminal_reason"] == "lease_expired_recovered"
    assert await round_store.list_by_thread("thread-round-lease-race") == []
    assert manager._runs == {}


@pytest.mark.anyio
async def test_run_status_commit_is_independent_of_run_group_records():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=store, round_store=round_store)
    record = await manager.create_or_reject("thread-factual-run-group")

    assert await manager.set_status(record.run_id, RunStatus.running)
    assert await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id)
    [round_info] = await round_store.list_by_thread(record.thread_id)
    assert persisted is not None
    assert persisted["status"] == RunStatus.success.value
    assert round_info["current_run_id"] == record.run_id
    assert "state" not in round_info


@pytest.mark.anyio
async def test_stale_recovery_does_not_recreate_run_after_thread_delete():
    class BlockingStatusStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.update_started = asyncio.Event()
            self.allow_update = asyncio.Event()

        async def update_status(self, *args, **kwargs):
            self.update_started.set()
            await self.allow_update.wait()
            return await super().update_status(*args, **kwargs)

    store = BlockingStatusStore()
    manager = RunManager(store=store)
    run_id = "legacy-run-delete-recovery-race"
    stale_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await store.put(
        run_id,
        thread_id="thread-delete-recovery-race",
        status=RunStatus.running.value,
        created_at=stale_time,
    )
    store._runs[run_id]["updated_at"] = stale_time

    recovery_task = asyncio.create_task(manager.recover_stale_inflight_runs(thread_id="thread-delete-recovery-race"))
    await store.update_started.wait()
    await manager.begin_thread_delete("thread-delete-recovery-race")
    assert await store.delete_by_thread("thread-delete-recovery-race") == 1
    store.allow_update.set()
    await recovery_task

    assert await store.get(run_id) is None
    assert run_id not in manager._runs


@pytest.mark.anyio
async def test_expired_lease_recovery_commit_is_cancellation_safe():
    class CancelAfterRecoveryCommitStore(MemoryRunStore):
        async def recover_expired_lease(self, *args, **kwargs):
            recovered = await super().recover_expired_lease(*args, **kwargs)
            assert recovered is True
            raise asyncio.CancelledError

    store = CancelAfterRecoveryCommitStore()
    manager = RunManager(store=store)
    record = await manager.create_or_reject("thread-recovery-cancel")
    lease = store._active_slots[record.thread_id]
    now = datetime.now(UTC)
    store._active_slots[record.thread_id] = replace(
        lease,
        lease_expires_at=now - timedelta(seconds=1),
    )

    with pytest.raises(asyncio.CancelledError):
        await manager.recover_stale_inflight_runs(thread_id=record.thread_id)

    visible = await manager.get(record.run_id)
    assert visible is not None
    assert visible.status == RunStatus.error
    assert visible.terminal_reason == "lease_expired_recovered"
    replacement = await manager.create_or_reject(record.thread_id)
    assert replacement.run_id != record.run_id


@pytest.mark.anyio
async def test_expired_rollback_lease_recovery_keeps_run_group_identity():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=store, round_store=round_store)
    record = await manager.create_or_reject("thread-expired-rollback")
    assert await manager.set_status(record.run_id, RunStatus.running)
    await store.request_cancel(record.run_id, "rollback")
    lease = store._active_slots[record.thread_id]
    store._active_slots[record.thread_id] = replace(
        lease,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    recovered = await manager.recover_stale_inflight_runs(thread_id=record.thread_id)
    [round_info] = await round_store.list_by_thread(record.thread_id)

    assert [item.run_id for item in recovered] == [record.run_id]
    assert record.status == RunStatus.error
    assert record.terminal_reason == "rollback_failed_owner_lost"
    assert "state" not in round_info


@pytest.mark.anyio
async def test_create_or_reject_reject_uses_sql_active_slot_across_managers(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
    from deerflow.persistence.run import RunRepository

    url = f"sqlite+aiosqlite:///{tmp_path / 'run-manager-lease.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        store = RunRepository(get_session_factory())
        manager_a = RunManager(store=store, worker_id="worker-a")
        manager_b = RunManager(store=store, worker_id="worker-b")

        results = await asyncio.gather(
            manager_a.create_or_reject("thread-lease", multitask_strategy="reject"),
            manager_b.create_or_reject("thread-lease", multitask_strategy="reject"),
            return_exceptions=True,
        )

        records = [result for result in results if isinstance(result, RunRecord)]
        conflicts = [result for result in results if isinstance(result, ConflictError)]
        assert len(records) == 1
        assert len(conflicts) == 1
        row = await store.get(records[0].run_id)
        assert row is not None
        assert row["status"] == "running"
        assert row["metadata"]["lease_token"] == records[0].lease_token
    finally:
        await close_engine()


@pytest.mark.anyio
async def test_lease_terminal_completion_releases_active_slot():
    store = MemoryRunStore()
    manager = RunManager(store=store, worker_id="worker-a")
    first = await manager.create_or_reject("thread-lease", multitask_strategy="reject")

    await manager.set_status(first.run_id, RunStatus.running)
    await manager.set_status(first.run_id, RunStatus.success, terminal_reason="success")
    second = await manager.create_or_reject("thread-lease", multitask_strategy="reject")

    stored_first = await store.get(first.run_id)
    stored_second = await store.get(second.run_id)
    assert stored_first is not None
    assert stored_first["status"] == "success"
    assert stored_first["terminal_reason"] == "success"
    assert stored_second is not None
    assert stored_second["status"] == "running"


@pytest.mark.anyio
async def test_failed_lease_terminal_cas_does_not_fallback_to_put():
    store = RejectingCompleteRunStore()
    manager = RunManager(store=store, worker_id="worker-a")
    record = await manager.create_or_reject("thread-lease", multitask_strategy="reject")

    await manager.set_status(record.run_id, RunStatus.running)
    committed = await manager.set_status(record.run_id, RunStatus.success, terminal_reason="success")

    stored = await store.get(record.run_id)
    assert committed is False
    assert record.status == RunStatus.running
    assert stored is not None
    assert stored["status"] == "running"


@pytest.mark.anyio
async def test_failed_lease_terminal_cas_leaves_run_group_unchanged():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = RejectingCompleteRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        worker_id="worker-a",
    )
    record = await manager.create_or_reject(
        "thread-terminal-round-cas",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running) is True

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    rounds = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert committed is False
    assert "state" not in rounds[0]

    lease = store._active_slots[record.thread_id]
    store._active_slots[record.thread_id] = replace(
        lease,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await manager.recover_stale_inflight_runs(thread_id=record.thread_id)

    recovered_rounds = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert "state" not in recovered_rounds[0]


@pytest.mark.anyio
async def test_late_terminal_loser_uses_winning_durable_run_status():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        worker_id="worker-a",
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-terminal-winner",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)
    assert await store.complete_run(
        record.run_id,
        from_statuses={RunStatus.running.value},
        terminal_status=RunStatus.error.value,
        lease_token=record.lease_token or "",
        generation=record.lease_generation or 0,
        terminal_reason="lease_expired_recovered",
        error="lease owner lost",
    )

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(record.thread_id, user_id="owner-a")
    assert committed is False
    assert persisted is not None and persisted["status"] == RunStatus.error.value
    assert record.status == RunStatus.error
    assert record.terminal_reason == "lease_expired_recovered"
    assert "state" not in round_info


@pytest.mark.anyio
async def test_failed_lease_terminal_cas_leaves_run_recoverable_by_expired_lease():
    store = RejectingCompleteRunStore()
    manager = RunManager(store=store, worker_id="worker-a")
    record = await manager.create_or_reject("thread-lease", multitask_strategy="reject")

    await manager.set_status(record.run_id, RunStatus.running)
    await manager.set_status(record.run_id, RunStatus.success, terminal_reason="success")
    lease = store._active_slots["thread-lease"]
    store._active_slots["thread-lease"] = replace(lease, lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))

    recovered = await manager.recover_stale_inflight_runs(thread_id="thread-lease")

    stored = await store.get(record.run_id)
    assert [run.run_id for run in recovered] == [record.run_id]
    assert record.status == RunStatus.running
    assert recovered[0].status == RunStatus.error
    assert recovered[0].terminal_reason == "lease_expired_recovered"
    assert stored is not None
    assert stored["status"] == "error"
    assert stored["terminal_reason"] == "lease_expired_recovered"


@pytest.mark.anyio
async def test_cancel_store_only_lease_run_records_cancel_intent():
    store = MemoryRunStore()
    owner = RunManager(store=store, worker_id="owner-worker")
    other = RunManager(store=store, worker_id="other-worker")
    record = await owner.create_or_reject("thread-lease", multitask_strategy="reject")

    assert await other.cancel(record.run_id, action="rollback") is True
    intent = await store.consume_cancel_intent(
        record.run_id,
        lease_token=record.lease_token or "",
        generation=record.lease_generation or 0,
    )

    assert intent is not None
    assert intent.action == "rollback"
    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == "rolling_back"


@pytest.mark.anyio
async def test_cancel_store_failure_still_stops_local_run():
    class FailingCancelStore(MemoryRunStore):
        async def request_cancel(self, *args, **kwargs):
            raise RuntimeError("cancel store unavailable")

    manager = RunManager(store=FailingCancelStore())
    record = await manager.create_or_reject("thread-local-cancel-failure")
    record.task = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)

    assert await manager.cancel(record.run_id) is True
    await asyncio.sleep(0)

    assert record.abort_event.is_set()
    assert record.task.cancelled()


@pytest.mark.anyio
async def test_cancel_store_failure_is_not_reported_as_success_for_store_only_run():
    class ToggleCancelStore(MemoryRunStore):
        fail_cancel = False

        async def request_cancel(self, *args, **kwargs):
            if self.fail_cancel:
                raise RuntimeError("cancel store unavailable")
            return await super().request_cancel(*args, **kwargs)

    store = ToggleCancelStore()
    owner = RunManager(store=store, worker_id="owner")
    other = RunManager(store=store, worker_id="other")
    record = await owner.create_or_reject("thread-store-only-cancel-failure")
    store.fail_cancel = True

    with pytest.raises(RuntimeError, match="cancel store unavailable"):
        await other.cancel(record.run_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_status", "terminal_reason", "expected_result"),
    [
        ("success", "completed", False),
        ("interrupted", "cancelled", True),
    ],
)
async def test_cancel_uses_durable_terminal_result_and_evicts_stale_local_control(
    terminal_status: str,
    terminal_reason: str,
    expected_result: bool,
):
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-cancel-durable-terminal",
        user_id="owner-a",
    )
    record.task = asyncio.create_task(asyncio.Event().wait())
    before_rounds = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )

    assert await store.complete_run(
        record.run_id,
        from_statuses={"running"},
        terminal_status=terminal_status,
        lease_token=record.lease_token or "",
        generation=record.lease_generation or 0,
        terminal_reason=terminal_reason,
    )

    assert await manager.cancel(record.run_id) is expected_result
    await asyncio.gather(record.task, return_exceptions=True)

    durable = await store.get(record.run_id, user_id="owner-a")
    hydrated = await manager.get(record.run_id, user_id="owner-a")
    after_rounds = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert record.abort_event.is_set()
    assert record.task.cancelled()
    assert durable is not None and durable["status"] == terminal_status
    assert hydrated is not None and hydrated.status == RunStatus(terminal_status)
    assert hydrated is not record
    assert after_rounds == before_rounds


@pytest.mark.anyio
async def test_cancel_intent_returning_after_terminal_commit_cannot_regress_local_status():
    class BlockingCancelIntentStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.intent_consumed = asyncio.Event()
            self.allow_intent_return = asyncio.Event()

        async def consume_cancel_intent(self, *args, **kwargs):
            intent = await super().consume_cancel_intent(*args, **kwargs)
            self.intent_consumed.set()
            await self.allow_intent_return.wait()
            return intent

    store = BlockingCancelIntentStore()
    manager = RunManager(store=store)
    record = await manager.create_or_reject("thread-cancel-terminal-race")
    assert await manager.set_status(record.run_id, RunStatus.running)
    await store.request_cancel(record.run_id, "interrupt")

    consume_task = asyncio.create_task(manager.consume_cancel_intent(record))
    await store.intent_consumed.wait()
    assert await manager.set_status(
        record.run_id,
        RunStatus.interrupted,
        terminal_reason="cancelled",
    )
    store.allow_intent_return.set()

    assert await consume_task is None
    assert record.status == RunStatus.interrupted


@pytest.mark.anyio
async def test_terminal_run_commit_ack_loss_confirms_idempotent_commit():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class CommitThenFailRunStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.terminal_attempts = 0

        async def complete_run(self, *args, **kwargs):
            result = await super().complete_run(*args, **kwargs)
            self.terminal_attempts += 1
            if self.terminal_attempts == 1:
                raise RuntimeError("terminal commit acknowledgement lost")
            return result

    store = CommitThenFailRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-run-ambiguous-terminal",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert committed is True
    assert store.terminal_attempts == 2
    assert record.status == RunStatus.success
    assert persisted is not None and persisted["status"] == RunStatus.success.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_terminal_run_commit_repeated_ack_loss_reads_durable_terminal():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class CommitThenAlwaysFailRunStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.terminal_attempts = 0

        async def complete_run(self, *args, **kwargs):
            await super().complete_run(*args, **kwargs)
            self.terminal_attempts += 1
            raise RuntimeError("terminal commit acknowledgement lost")

    store = CommitThenAlwaysFailRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-run-repeated-ambiguous-terminal",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert committed is True
    assert store.terminal_attempts == 2
    assert record.status == RunStatus.success
    assert persisted is not None and persisted["status"] == RunStatus.success.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_terminal_run_commit_cancellation_confirms_idempotent_commit():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class CancelThenCommitRunStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.terminal_attempts = 0

        async def complete_run(self, *args, **kwargs):
            self.terminal_attempts += 1
            if self.terminal_attempts == 1:
                raise asyncio.CancelledError
            return await super().complete_run(*args, **kwargs)

    store = CancelThenCommitRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-run-cancelled-terminal",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert committed is True
    assert store.terminal_attempts == 2
    assert record.status == RunStatus.success
    assert persisted is not None and persisted["status"] == RunStatus.success.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_terminal_run_commit_repeated_cancellation_leaves_run_group_unchanged():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class AlwaysCancelRunStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.terminal_attempts = 0

        async def complete_run(self, *args, **kwargs):
            self.terminal_attempts += 1
            raise asyncio.CancelledError

    store = AlwaysCancelRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-run-repeated-cancel-terminal",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)

    with pytest.raises(asyncio.CancelledError):
        await manager.set_status(
            record.run_id,
            RunStatus.success,
            terminal_reason="success",
        )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert store.terminal_attempts == 2
    assert persisted is not None and persisted["status"] == RunStatus.running.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_terminal_run_commit_repeated_ack_cancellation_reads_durable_terminal():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class CommitThenCancelRunStore(MemoryRunStore):
        def __init__(self):
            super().__init__()
            self.terminal_attempts = 0

        async def complete_run(self, *args, **kwargs):
            await super().complete_run(*args, **kwargs)
            self.terminal_attempts += 1
            raise asyncio.CancelledError

    store = CommitThenCancelRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create_or_reject(
        "thread-run-repeated-ack-cancel-terminal",
        user_id="owner-a",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(
        record.thread_id,
        user_id="owner-a",
    )
    assert committed is True
    assert store.terminal_attempts == 2
    assert record.status == RunStatus.success
    assert persisted is not None and persisted["status"] == RunStatus.success.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_unleased_terminal_ack_loss_reads_durable_terminal():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class CommitThenFailStatusStore(MemoryRunStore):
        async def update_status(self, run_id, status, **kwargs):
            result = await super().update_status(run_id, status, **kwargs)
            if status == RunStatus.success.value:
                raise RuntimeError("terminal status acknowledgement lost")
            return result

    store = CommitThenFailStatusStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create("thread-unleased-ack-loss", user_id="owner-a")
    assert await manager.set_status(record.run_id, RunStatus.running)

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(record.thread_id, user_id="owner-a")
    assert committed is True
    assert persisted is not None and persisted["status"] == RunStatus.success.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_unleased_terminal_ack_cancellation_reads_durable_terminal():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    class CommitThenCancelStatusStore(MemoryRunStore):
        async def update_status(self, run_id, status, **kwargs):
            result = await super().update_status(run_id, status, **kwargs)
            if status == RunStatus.success.value:
                raise asyncio.CancelledError
            return result

    store = CommitThenCancelStatusStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    record = await manager.create("thread-unleased-ack-cancel", user_id="owner-a")
    assert await manager.set_status(record.run_id, RunStatus.running)

    committed = await manager.set_status(
        record.run_id,
        RunStatus.success,
        terminal_reason="success",
    )

    persisted = await store.get(record.run_id, user_id="owner-a")
    [round_info] = await round_store.list_by_thread(record.thread_id, user_id="owner-a")
    assert committed is True
    assert persisted is not None and persisted["status"] == RunStatus.success.value
    assert "state" not in round_info


@pytest.mark.anyio
async def test_cancel_keeps_strongest_durable_action_on_local_record():
    store = MemoryRunStore()
    manager = RunManager(store=store, worker_id="worker-a")
    record = await manager.create_or_reject("thread-cancel-action")

    assert await manager.cancel(record.run_id, action="rollback") is True
    assert await manager.cancel(record.run_id, action="interrupt") is True

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["cancel_action"] == "rollback"
    assert record.abort_action == "rollback"


@pytest.mark.anyio
async def test_create_or_reject_interrupt_persists_interrupted_status_to_store():
    """interrupt strategy should persist interrupted status for old runs."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)

    new = await manager.create_or_reject("thread-1", multitask_strategy="interrupt")

    stored_old = await store.get(old.run_id)
    stored_new = await store.get(new.run_id)
    assert new.run_id != old.run_id
    assert new.lease_token is not None
    assert new.lease_generation is not None
    assert old.status == RunStatus.interrupted
    assert stored_old is not None
    assert stored_old["status"] == "interrupted"
    assert stored_new is not None
    assert stored_new["status"] == "running"


@pytest.mark.anyio
async def test_create_or_reject_does_not_interrupt_old_run_when_new_run_store_write_fails():
    """A failed new-run persist must not cancel the existing inflight run."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)
    store.put = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await manager.create_or_reject("thread-1", multitask_strategy="interrupt")

    stored_old = await store.get(old.run_id)
    assert list(manager._runs) == [old.run_id]
    assert old.status == RunStatus.running
    assert old.abort_event.is_set() is False
    assert stored_old is not None
    assert stored_old["status"] == "running"


@pytest.mark.anyio
async def test_create_or_reject_does_not_interrupt_old_run_when_new_run_store_write_is_cancelled():
    """Cancellation during new-run persist must not cancel the existing run."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)

    async def cancelled_put(run_id, **kwargs):
        raise asyncio.CancelledError

    store.put = cancelled_put

    with pytest.raises(asyncio.CancelledError):
        await manager.create_or_reject("thread-1", multitask_strategy="interrupt")

    stored_old = await store.get(old.run_id)
    assert list(manager._runs) == [old.run_id]
    assert old.status == RunStatus.running
    assert old.abort_event.is_set() is False
    assert stored_old is not None
    assert stored_old["status"] == "running"


@pytest.mark.anyio
async def test_create_or_reject_rollback_persists_interrupted_status_to_store():
    """rollback strategy should persist interrupted status for old runs."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)

    new = await manager.create_or_reject("thread-1", multitask_strategy="rollback")

    stored_old = await store.get(old.run_id)
    stored_new = await store.get(new.run_id)
    assert new.run_id != old.run_id
    assert new.lease_token is not None
    assert new.lease_generation is not None
    assert old.status == RunStatus.interrupted
    assert stored_old is not None
    assert stored_old["status"] == "interrupted"
    assert stored_new is not None
    assert stored_new["status"] == "running"


@pytest.mark.anyio
@pytest.mark.parametrize("strategy", ["interrupt", "rollback"])
async def test_create_or_reject_interrupt_and_rollback_conflict_when_active_slot_is_held(strategy: str):
    store = MemoryRunStore()
    manager = RunManager(store=store, worker_id="worker-a")
    old = await manager.create_or_reject("thread-lease", multitask_strategy="reject")

    with pytest.raises(ConflictError):
        await manager.create_or_reject("thread-lease", multitask_strategy=strategy)

    inflight = await store.list_inflight()
    assert old.abort_event.is_set() is False
    assert [row["run_id"] for row in inflight] == [old.run_id]
    assert inflight[0]["lease_token"] == old.lease_token


@pytest.mark.anyio
async def test_model_name_default_is_none():
    """create_or_reject without model_name should default to None."""
    from deerflow.runtime.runs.schemas import DisconnectMode

    store = MemoryRunStore()
    mgr = RunManager(store=store)

    record = await mgr.create_or_reject(
        "thread-1",
        on_disconnect=DisconnectMode.cancel,
        model_name=None,
    )
    assert record.model_name is None

    stored = await store.get(record.run_id)
    assert stored["model_name"] is None


# ---------------------------------------------------------------------------
# Store fallback tests (simulates gateway restart scenario)
# ---------------------------------------------------------------------------


@pytest.fixture
def manager_with_store() -> RunManager:
    """RunManager backed by a MemoryRunStore."""
    return RunManager(store=MemoryRunStore())


@pytest.mark.anyio
async def test_list_by_thread_returns_store_records_after_restart(manager_with_store: RunManager):
    """After in-memory state is cleared (simulating restart), list_by_thread
    should still return runs from the persistent store."""
    mgr = manager_with_store
    r1 = await mgr.create("thread-1", "agent-1")
    await mgr.set_status(r1.run_id, RunStatus.success)
    r2 = await mgr.create("thread-1", "agent-2")
    await mgr.set_status(r2.run_id, RunStatus.error, error="boom")

    # Clear in-memory dict to simulate a restart
    mgr._runs.clear()

    runs = await mgr.list_by_thread("thread-1")
    assert len(runs) == 2
    statuses = {r.run_id: r.status for r in runs}
    assert statuses[r1.run_id] == RunStatus.success
    assert statuses[r2.run_id] == RunStatus.error
    # Verify other fields survive the round-trip
    for r in runs:
        assert r.thread_id == "thread-1"
        assert ISO_RE.match(r.created_at)


@pytest.mark.anyio
async def test_list_by_thread_merges_in_memory_and_store(manager_with_store: RunManager):
    """In-memory runs should be included alongside store-only records."""
    mgr = manager_with_store

    # Create a run and let it complete (will be in both memory and store)
    r1 = await mgr.create("thread-1")
    await mgr.set_status(r1.run_id, RunStatus.success)

    # Simulate restart: clear memory, then create a new in-memory run
    mgr._runs.clear()
    r2 = await mgr.create("thread-1")

    runs = await mgr.list_by_thread("thread-1")
    assert len(runs) == 2
    run_ids = {r.run_id for r in runs}
    assert r1.run_id in run_ids
    assert r2.run_id in run_ids

    # r2 should be the in-memory record (has live state)
    r2_record = next(r for r in runs if r.run_id == r2.run_id)
    assert r2_record is r2  # same object reference


@pytest.mark.anyio
async def test_list_by_thread_does_not_hide_store_only_runs_behind_memory_limit(monkeypatch: pytest.MonkeyPatch):
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    monkeypatch.setattr("deerflow.runtime.runs.manager._now_iso", lambda: "2026-01-01T00:00:00+00:00")
    first = await mgr.create("thread-1")
    second = await mgr.create("thread-1")
    await mgr.set_status(first.run_id, RunStatus.success)
    await mgr.set_status(second.run_id, RunStatus.success)
    await store.put(
        "store-new",
        thread_id="thread-1",
        status="success",
        created_at="2026-01-02T00:00:00+00:00",
    )

    runs = await mgr.list_by_thread("thread-1", limit=2)

    assert runs[0].run_id == "store-new"
    assert len(runs) == 2


@pytest.mark.anyio
async def test_list_by_thread_no_store():
    """Without a store, list_by_thread should only return in-memory runs."""
    mgr = RunManager()
    await mgr.create("thread-1")

    mgr._runs.clear()
    runs = await mgr.list_by_thread("thread-1")
    assert runs == []


@pytest.mark.anyio
async def test_aget_returns_in_memory_record(manager_with_store: RunManager):
    """aget should return the in-memory record when available."""
    mgr = manager_with_store
    r1 = await mgr.create("thread-1", "agent-1")

    result = await mgr.aget(r1.run_id)
    assert result is r1  # same object


@pytest.mark.anyio
async def test_aget_honors_user_filter_for_in_memory_record():
    """aget should apply user_id filtering before returning an in-memory record."""
    mgr = RunManager()
    record = await mgr.create("thread-1", "agent-1", user_id="user-1")

    assert await mgr.aget(record.run_id, user_id="user-1") is record
    assert await mgr.aget(record.run_id, user_id="user-2") is None
    assert await mgr.aget(record.run_id, user_id=None) is record


@pytest.mark.anyio
async def test_get_recheck_after_store_await_honors_user_filter():
    """The post-store-await memory recheck must not bypass owner filtering."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)
    get_started = asyncio.Event()
    allow_get = asyncio.Event()

    async def blocking_get(run_id, *, user_id=None):
        get_started.set()
        await allow_get.wait()
        return None

    store.get = blocking_get
    get_task = asyncio.create_task(mgr.get("run-1", user_id="user-2"))

    try:
        await get_started.wait()
        record = RunRecord(
            run_id="run-1",
            thread_id="thread-1",
            assistant_id="agent-1",
            status=RunStatus.pending,
            on_disconnect=DisconnectMode.cancel,
            user_id="user-1",
        )
        async with mgr._lock:
            mgr._runs[record.run_id] = record
            mgr._index_run_locked(record)

        allow_get.set()

        assert await get_task is None
    finally:
        allow_get.set()
        if not get_task.done():
            get_task.cancel()
            await asyncio.gather(get_task, return_exceptions=True)


@pytest.mark.anyio
async def test_aget_falls_back_to_store(manager_with_store: RunManager):
    """aget should return a record from the store when not in memory."""
    mgr = manager_with_store
    r1 = await mgr.create("thread-1", "agent-1")
    await mgr.set_status(r1.run_id, RunStatus.success)

    mgr._runs.clear()

    result = await mgr.aget(r1.run_id)
    assert result is not None
    assert result.run_id == r1.run_id
    assert result.status == RunStatus.success
    assert result.thread_id == "thread-1"
    assert result.assistant_id == "agent-1"


@pytest.mark.anyio
async def test_aget_falls_back_to_store_with_user_filter():
    """aget should honor user_id when reading store-only records."""
    store = MemoryRunStore()
    await store.put("run-1", thread_id="thread-1", user_id="user-1", status="success")
    mgr = RunManager(store=store)

    allowed = await mgr.aget("run-1", user_id="user-1")
    denied = await mgr.aget("run-1", user_id="user-2")
    assert allowed is not None
    assert denied is None


@pytest.mark.anyio
async def test_aget_returns_none_for_unknown(manager_with_store: RunManager):
    """aget should return None for a run ID that doesn't exist anywhere."""
    result = await manager_with_store.aget("nonexistent-run-id")
    assert result is None


@pytest.mark.anyio
async def test_aget_store_failure_is_graceful():
    """If the store raises, aget should return None instead of propagating."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    store.get = AsyncMock(side_effect=RuntimeError("db down"))
    mgr = RunManager(store=store)

    result = await mgr.aget("some-id")
    assert result is None


@pytest.mark.anyio
async def test_list_by_thread_store_failure_is_graceful():
    """If the store raises, list_by_thread should return only in-memory runs."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    store.list_by_thread = AsyncMock(side_effect=RuntimeError("db down"))
    mgr = RunManager(store=store)

    r1 = await mgr.create("thread-1")
    runs = await mgr.list_by_thread("thread-1")
    assert len(runs) == 1
    assert runs[0].run_id == r1.run_id


@pytest.mark.anyio
async def test_list_by_thread_falls_back_to_store_with_user_filter():
    """list_by_thread should return only the requesting user's store records."""
    store = MemoryRunStore()
    await store.put("run-1", thread_id="thread-1", user_id="user-1", status="success")
    await store.put("run-2", thread_id="thread-1", user_id="user-2", status="success")
    mgr = RunManager(store=store)

    runs = await mgr.list_by_thread("thread-1", user_id="user-1")
    assert [r.run_id for r in runs] == ["run-1"]


@pytest.mark.anyio
async def test_list_by_thread_honors_user_filter_for_in_memory_records():
    """list_by_thread should apply user_id filtering to active in-memory runs too."""
    mgr = RunManager()
    user_1_run = await mgr.create("thread-1", "agent-1", user_id="user-1")
    user_2_run = await mgr.create("thread-1", "agent-1", user_id="user-2")
    shared_run = await mgr.create("thread-1", "agent-1", user_id=None)

    filtered = await mgr.list_by_thread("thread-1", user_id="user-1")
    unfiltered = await mgr.list_by_thread("thread-1", user_id=None)

    assert [r.run_id for r in filtered] == [user_1_run.run_id]
    assert {r.run_id for r in unfiltered} == {user_1_run.run_id, user_2_run.run_id, shared_run.run_id}


# ---------------------------------------------------------------------------
# Per-thread index (thread_id -> run_ids): keeps per-thread queries
# O(runs-in-thread) instead of scanning every in-memory run, and stays
# consistent with ``_runs`` across create / cleanup / rollback.
# ---------------------------------------------------------------------------


class _FailingPutRunStore(MemoryRunStore):
    """Memory run store whose every ``put`` fails (non-retryably)."""

    async def put(self, run_id, **kwargs):
        raise ValueError("simulated persist failure")


@pytest.mark.anyio
async def test_thread_index_scopes_runs_per_thread(manager: RunManager):
    a1 = await manager.create("thread-a")
    a2 = await manager.create("thread-a")
    b1 = await manager.create("thread-b")

    # The index mirrors _runs membership, bucketed by thread.
    assert set(manager._runs_by_thread["thread-a"]) == {a1.run_id, a2.run_id}
    assert set(manager._runs_by_thread["thread-b"]) == {b1.run_id}

    # Per-thread queries return only that thread's runs (no cross-thread leak).
    assert {r.run_id for r in await manager.list_by_thread("thread-a")} == {a1.run_id, a2.run_id}
    assert {r.run_id for r in await manager.list_by_thread("thread-b")} == {b1.run_id}
    assert await manager.list_by_thread("thread-missing") == []


@pytest.mark.anyio
async def test_thread_index_preserves_insertion_order(manager: RunManager):
    # The index is insertion-ordered (dict-as-ordered-set) so list_by_thread
    # keeps the stable tie-breaking the full-scan implementation guaranteed.
    first = await manager.create("thread-a")
    second = await manager.create("thread-a")
    assert list(manager._runs_by_thread["thread-a"]) == [first.run_id, second.run_id]


@pytest.mark.anyio
async def test_thread_index_cleanup_prunes_run_and_empty_bucket(manager: RunManager):
    a1 = await manager.create("thread-a")
    a2 = await manager.create("thread-a")

    await manager.cleanup(a1.run_id, delay=0)
    assert a1.run_id not in manager._runs
    assert set(manager._runs_by_thread["thread-a"]) == {a2.run_id}

    await manager.cleanup(a2.run_id, delay=0)
    # Empty buckets are pruned so the index cannot grow without bound.
    assert "thread-a" not in manager._runs_by_thread
    assert await manager.list_by_thread("thread-a") == []


@pytest.mark.anyio
async def test_has_inflight_reflects_index(manager: RunManager):
    record = await manager.create("thread-a")
    assert await manager.has_inflight("thread-a") is True
    assert await manager.has_inflight("thread-b") is False

    await manager.set_status(record.run_id, RunStatus.success)
    assert await manager.has_inflight("thread-a") is False


@pytest.mark.anyio
async def test_create_or_reject_inflight_is_thread_scoped(manager: RunManager):
    await manager.create_or_reject("thread-a", multitask_strategy="reject")
    # A different thread is unaffected by thread-a's active run.
    await manager.create_or_reject("thread-b", multitask_strategy="reject")
    # A second active run on the same thread is rejected.
    with pytest.raises(ConflictError):
        await manager.create_or_reject("thread-a", multitask_strategy="reject")


@pytest.mark.anyio
async def test_failed_create_unindexes_run():
    manager = RunManager(store=_FailingPutRunStore())
    with pytest.raises(ValueError):
        await manager.create("thread-a")
    # A rolled-back run must leave no trace in either _runs or the index.
    assert manager._runs == {}
    assert "thread-a" not in manager._runs_by_thread


@pytest.mark.anyio
async def test_failed_create_or_reject_unindexes_run():
    # Symmetric to test_failed_create_unindexes_run: create_or_reject has its own
    # insert + rollback-unindex site, so a persist failure there must also leave
    # neither _runs nor the index holding the rolled-back run. This closes the last
    # mutation path not exercised by an index-consistency test.
    manager = RunManager(store=_FailingPutRunStore())
    with pytest.raises(ValueError):
        await manager.create_or_reject("thread-a", multitask_strategy="reject")
    assert manager._runs == {}
    assert "thread-a" not in manager._runs_by_thread


@pytest.mark.asyncio
async def test_terminal_status_schedules_memory_cleanup_quickly():
    manager = RunManager(terminal_cleanup_delay=0)
    record = await manager.create("cleanup-thread")
    await manager.set_status(record.run_id, RunStatus.success)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert record.run_id not in manager._runs
    assert "cleanup-thread" not in manager._runs_by_thread


@pytest.mark.anyio
async def test_create_or_reject_rejects_persisted_inflight_same_thread():
    """A persisted active run should block a second reject run after restart."""
    store = MemoryRunStore()
    await store.put("persisted-running", thread_id="thread-1", status="running")
    manager = RunManager(store=store)

    with pytest.raises(ConflictError, match="already has an active run"):
        await manager.create_or_reject("thread-1", multitask_strategy="reject")

    assert await store.get("persisted-running") is not None
    assert await store.list_by_thread("thread-1")
    assert len(await store.list_inflight()) == 1


@pytest.mark.anyio
async def test_create_or_reject_recovers_expired_same_thread_lease_before_conflict_check():
    store = MemoryRunStore()
    owner = RunManager(store=store, worker_id="worker-a")
    expired = await owner.create_or_reject("thread-expired-create")
    lease = store._active_slots[expired.thread_id]
    store._active_slots[expired.thread_id] = replace(
        lease,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    replacement = await RunManager(store=store, worker_id="worker-b").create_or_reject(
        expired.thread_id,
    )

    stored_expired = await store.get(expired.run_id)
    assert stored_expired is not None
    assert stored_expired["status"] == RunStatus.error.value
    assert stored_expired["terminal_reason"] == "lease_expired_recovered"
    assert replacement.run_id != expired.run_id


@pytest.mark.anyio
async def test_create_or_reject_persists_round_binding_under_active_lease():
    from deerflow.persistence.round_state import MemoryRoundStateStore

    store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=store, round_store=round_store, worker_id="worker-a")

    record = await manager.create_or_reject(
        "thread-durable-round",
        kwargs={"input": {"messages": [{"role": "user", "content": "inspect"}]}},
    )

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["metadata"]["round_id"] == record.round_id
    assert stored["metadata"]["round_context"]["current_run_id"] == record.run_id
    hydrated = await RunManager(store=store).get(record.run_id)
    assert hydrated is not None
    assert hydrated.round_id == record.round_id


@pytest.mark.anyio
async def test_create_or_reject_persisted_terminal_and_other_thread_do_not_block():
    store = MemoryRunStore()
    await store.put("terminal", thread_id="thread-1", status="success")
    await store.put("other-running", thread_id="thread-2", status="running")
    manager = RunManager(store=store)

    record = await manager.create_or_reject("thread-1", multitask_strategy="reject")

    assert record.thread_id == "thread-1"
    assert record.run_id != "terminal"


@pytest.mark.anyio
async def test_create_or_reject_without_store_still_rejects_memory_inflight(manager: RunManager):
    first = await manager.create_or_reject("thread-1", multitask_strategy="reject")

    with pytest.raises(ConflictError, match="already has an active run"):
        await manager.create_or_reject("thread-1", multitask_strategy="reject")

    assert first.run_id in manager._runs


@pytest.mark.asyncio
async def test_thread_delete_gate_blocks_new_runs_until_explicit_recreation(manager: RunManager):
    await manager.begin_thread_delete("thread-delete")

    with pytest.raises(ConflictError, match="being deleted"):
        await manager.create_or_reject("thread-delete", multitask_strategy="reject")

    await manager.end_thread_delete("thread-delete")

    record = await manager.create_or_reject("thread-delete", multitask_strategy="reject")
    assert record.thread_id == "thread-delete"


@pytest.mark.asyncio
async def test_thread_delete_gate_has_single_exclusive_owner(manager: RunManager):
    await manager.begin_thread_delete("thread-exclusive-delete")

    with pytest.raises(ConflictError, match="already being deleted"):
        await manager.begin_thread_delete("thread-exclusive-delete")

    await manager.end_thread_delete("thread-exclusive-delete")
    await manager.begin_thread_delete("thread-exclusive-delete")


@pytest.mark.asyncio
async def test_thread_delete_waits_for_existing_checkpoint_writer(manager: RunManager):
    await manager.begin_thread_write("thread-write")

    delete_task = asyncio.create_task(manager.begin_thread_delete("thread-write"))
    await asyncio.sleep(0)
    assert not delete_task.done()

    await manager.end_thread_write("thread-write")
    await delete_task

    with pytest.raises(ConflictError, match="being deleted"):
        await manager.begin_thread_write("thread-write")


@pytest.mark.asyncio
async def test_cancelled_thread_delete_wait_releases_gate_for_retry(
    manager: RunManager,
):
    thread_id = "thread-cancelled-delete"
    await manager.begin_thread_write(thread_id)
    delete_task = asyncio.create_task(manager.begin_thread_delete(thread_id))
    await asyncio.sleep(0)

    delete_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await delete_task
    await manager.end_thread_write(thread_id)

    await manager.begin_thread_delete(thread_id)


@pytest.mark.asyncio
async def test_existing_run_can_commit_terminal_status_during_thread_delete():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create_or_reject(
        "thread-delete-terminal",
        user_id="owner-a",
    )
    await manager.begin_thread_delete("thread-delete-terminal")

    committed = await manager.set_status(
        record.run_id,
        RunStatus.interrupted,
        terminal_reason="cancelled",
    )

    stored = await store.get(record.run_id, user_id="owner-a")
    assert committed is True
    assert record.status == RunStatus.interrupted
    assert stored is not None
    assert stored["status"] == "interrupted"


@pytest.mark.asyncio
async def test_create_or_reject_waits_for_existing_checkpoint_writer(manager: RunManager):
    await manager.begin_thread_write("thread-initial-checkpoint")

    create_task = asyncio.create_task(manager.create_or_reject("thread-initial-checkpoint"))
    await asyncio.sleep(0)
    assert not create_task.done()

    await manager.end_thread_write("thread-initial-checkpoint")
    record = await create_task
    assert record.thread_id == "thread-initial-checkpoint"


@pytest.mark.asyncio
async def test_thread_status_update_if_latest_serializes_with_new_run_creation(manager: RunManager):
    updater = getattr(manager, "update_thread_status_if_latest", None)
    assert callable(updater)

    class BlockingThreadStore:
        def __init__(self):
            self.status = "running"
            self.update_started = asyncio.Event()
            self.allow_update = asyncio.Event()

        async def update_status(self, thread_id, status, *, user_id=None):
            self.update_started.set()
            await self.allow_update.wait()
            self.status = status

    thread_store = BlockingThreadStore()
    failed = await manager.create("thread-latest-status", user_id="owner-a")
    assert await manager.set_status(failed.run_id, RunStatus.error, terminal_reason="failed")

    update_task = asyncio.create_task(updater(failed, thread_store, "error"))
    await thread_store.update_started.wait()
    create_task = asyncio.create_task(manager.create_or_reject("thread-latest-status", user_id="owner-a"))
    await asyncio.sleep(0)
    assert not create_task.done()

    thread_store.allow_update.set()
    assert await update_task is True
    replacement = await create_task
    await thread_store.update_status("thread-latest-status", "running", user_id="owner-a")
    assert thread_store.status == "running"

    assert await updater(failed, thread_store, "error") is False
    assert replacement.run_id != failed.run_id
    assert thread_store.status == "running"


@pytest.mark.asyncio
async def test_thread_status_update_if_latest_does_not_block_unrelated_thread(manager: RunManager):
    record = await manager.create("thread-status-a", user_id="owner-a")
    assert await manager.set_status(record.run_id, RunStatus.error, terminal_reason="failed")
    update_started = asyncio.Event()
    allow_update = asyncio.Event()

    class BlockingThreadStore:
        async def update_status(self, thread_id, status, *, user_id=None):
            update_started.set()
            await allow_update.wait()

    update_task = asyncio.create_task(
        manager.update_thread_status_if_latest(
            record,
            BlockingThreadStore(),
            "error",
        )
    )
    await update_started.wait()
    create_task = asyncio.create_task(manager.create_or_reject("thread-status-b", user_id="owner-a"))

    try:
        replacement = await asyncio.wait_for(asyncio.shield(create_task), timeout=0.2)
    finally:
        allow_update.set()
        await asyncio.gather(update_task, create_task, return_exceptions=True)

    assert replacement.thread_id == "thread-status-b"


@pytest.mark.asyncio
async def test_slow_leased_run_creation_does_not_block_unrelated_thread():
    create_started = asyncio.Event()
    allow_create = asyncio.Event()

    class BlockingCreateStore(MemoryRunStore):
        async def create_pending_run(self, run_id: str, *, thread_id: str, **kwargs):
            if thread_id == "thread-slow-create":
                create_started.set()
                await allow_create.wait()
            return await super().create_pending_run(
                run_id,
                thread_id=thread_id,
                **kwargs,
            )

    manager = RunManager(store=BlockingCreateStore())
    slow_task = asyncio.create_task(manager.create_or_reject("thread-slow-create", user_id="owner-a"))
    await create_started.wait()
    fast_task = asyncio.create_task(manager.create_or_reject("thread-fast-create", user_id="owner-a"))

    try:
        fast_record = await asyncio.wait_for(asyncio.shield(fast_task), timeout=0.2)
    finally:
        allow_create.set()
        await asyncio.gather(slow_task, fast_task, return_exceptions=True)

    assert fast_record.thread_id == "thread-fast-create"


@pytest.mark.asyncio
async def test_slow_legacy_run_creation_does_not_block_unrelated_thread():
    create_started = asyncio.Event()
    allow_create = asyncio.Event()

    class BlockingPutStore(MemoryRunStore):
        async def put(self, run_id: str, *, thread_id: str, **kwargs):
            if thread_id == "thread-slow-legacy":
                create_started.set()
                await allow_create.wait()
            return await super().put(run_id, thread_id=thread_id, **kwargs)

    manager = RunManager(store=BlockingPutStore())
    slow_task = asyncio.create_task(manager.create("thread-slow-legacy"))
    await create_started.wait()
    fast_task = asyncio.create_task(manager.create("thread-fast-legacy"))

    try:
        fast_record = await asyncio.wait_for(asyncio.shield(fast_task), timeout=0.2)
    finally:
        allow_create.set()
        await asyncio.gather(slow_task, fast_task, return_exceptions=True)

    assert fast_record.thread_id == "thread-fast-legacy"
