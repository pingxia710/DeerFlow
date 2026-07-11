"""RunStore lease/CAS contract tests.

These tests exercise the PR1 store-level contract only. They do not wire the
lease path into RunManager or Gateway routes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from deerflow.persistence.run import RunRepository
from deerflow.runtime.runs.schemas import is_active_status, is_inflight_status, is_terminal_status
from deerflow.runtime.runs.store.memory import MemoryRunStore

STORE_KINDS = ("memory", "sql")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _make_store(kind: str, tmp_path) -> MemoryRunStore | RunRepository:
    if kind == "memory":
        return MemoryRunStore()

    from deerflow.persistence.engine import get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'lease-contract.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    return RunRepository(get_session_factory())


async def _cleanup_store(kind: str) -> None:
    if kind == "sql":
        from deerflow.persistence.engine import close_engine

        await close_engine()


def _terminal_reason(row: dict[str, Any]) -> str | None:
    return row.get("terminal_reason") or (row.get("metadata") or {}).get("terminal_reason")


@pytest.mark.anyio
def test_status_predicates_cover_legacy_and_future_values() -> None:
    assert is_active_status("running")
    assert is_active_status("cancelling")
    assert is_active_status("rolling_back")
    assert not is_active_status("pending")
    assert is_inflight_status("pending")
    assert is_inflight_status("running")
    assert is_inflight_status("cancelling")
    assert is_inflight_status("rolling_back")
    assert not is_inflight_status("success")
    assert is_terminal_status("success")
    assert is_terminal_status("interrupted")
    assert is_terminal_status("boundary_stopped")
    assert is_terminal_status("worker_lost")
    assert is_terminal_status("rollback_failed")


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_concurrent_acquire_allows_one_active_run_per_thread(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        await store.create_pending_run("run-2", thread_id="thread-1")

        first, second = await asyncio.gather(
            store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a"),
            store.try_acquire_active_slot("thread-1", "run-2", owner_worker_id="worker-b"),
        )

        leases = [lease for lease in (first, second) if lease is not None]
        assert len(leases) == 1
        winner = await store.get(leases[0].run_id)
        loser_id = "run-2" if leases[0].run_id == "run-1" else "run-1"
        loser = await store.get(loser_id)
        assert winner["status"] == "running"
        assert loser["status"] == "pending"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_generation_increases_after_terminal_release(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease_1 = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease_1 is not None

        completed = await store.complete_run(
            "run-1",
            from_statuses={"running"},
            terminal_status="success",
            lease_token=lease_1.lease_token,
            generation=lease_1.generation,
        )
        assert completed is True

        await store.create_pending_run("run-2", thread_id="thread-1")
        lease_2 = await store.try_acquire_active_slot("thread-1", "run-2", owner_worker_id="worker-b")

        assert lease_2 is not None
        assert lease_2.generation > lease_1.generation
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_heartbeat_rejects_stale_token_or_generation(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        now = datetime(2026, 7, 4, tzinfo=UTC)
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot(
            "thread-1",
            "run-1",
            owner_worker_id="worker-a",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        )
        assert lease is not None

        assert await store.heartbeat_lease("run-1", lease_token="stale", generation=lease.generation, now=now) is False
        assert await store.heartbeat_lease("run-1", lease_token=lease.lease_token, generation=lease.generation + 1, now=now) is False
        assert await store.heartbeat_lease("run-1", lease_token=lease.lease_token, generation=lease.generation, now=now) is True
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_heartbeat_metadata_update_is_fenced_by_valid_lease(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        now = datetime(2026, 7, 4, tzinfo=UTC)
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot(
            "thread-1",
            "run-1",
            owner_worker_id="worker-a",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        )
        assert lease is not None

        assert (
            await store.heartbeat_lease(
                "run-1",
                lease_token="stale",
                generation=lease.generation,
                metadata_updates={"round_id": "stale-round"},
                now=now,
            )
            is False
        )
        assert "round_id" not in (await store.get("run-1"))["metadata"]

        assert (
            await store.heartbeat_lease(
                "run-1",
                lease_token=lease.lease_token,
                generation=lease.generation,
                metadata_updates={
                    "round_id": "round-1",
                    "round_context": {"current_run_id": "run-1"},
                },
                now=now,
            )
            is True
        )
        metadata = (await store.get("run-1"))["metadata"]
        assert metadata["round_id"] == "round-1"
        assert metadata["round_context"] == {"current_run_id": "run-1"}
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_delete_legacy_by_thread_only_removes_unowned_rows(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.put("legacy", thread_id="thread-legacy", user_id=None)
        await store.put("default", thread_id="thread-legacy", user_id="default")
        await store.put("foreign", thread_id="thread-legacy", user_id="foreign")

        deleted = await store.delete_legacy_by_thread("thread-legacy")

        assert deleted == 1
        assert await store.get("legacy", user_id=None) is None
        assert await store.get("default", user_id=None) is not None
        assert await store.get("foreign", user_id=None) is not None
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_active_status_cas_rejects_expired_lease(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        now = datetime(2026, 7, 4, tzinfo=UTC)
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot(
            "thread-1",
            "run-1",
            owner_worker_id="worker-a",
            lease_expires_at=now - timedelta(seconds=1),
            now=now,
        )
        assert lease is not None

        assert (
            await store.cas_status(
                "run-1",
                from_statuses={"running"},
                to_status="running",
                lease_token=lease.lease_token,
                generation=lease.generation,
                now=now,
            )
            is False
        )
        assert (await store.get("run-1"))["status"] == "running"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_late_completion_cannot_overwrite_terminal(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease is not None

        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="success", lease_token=lease.lease_token, generation=lease.generation) is True
        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="error", lease_token=lease.lease_token, generation=lease.generation) is False
        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="error", lease_token="stale", generation=lease.generation) is False

        row = await store.get("run-1")
        assert row["status"] == "success"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_late_error_and_success_cannot_overwrite_each_other(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease is not None

        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="error", lease_token=lease.lease_token, generation=lease.generation) is True
        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="success", lease_token=lease.lease_token, generation=lease.generation) is False

        row = await store.get("run-1")
        assert row["status"] == "error"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_failed_terminal_cas_does_not_release_active_slot(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease is not None
        await store.create_pending_run("run-2", thread_id="thread-1")

        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="success", lease_token="stale", generation=lease.generation) is False

        assert await store.try_acquire_active_slot("thread-1", "run-2", owner_worker_id="worker-b") is None
        row = await store.get("run-1")
        assert row["status"] == "running"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_backfill_metadata_requires_same_generation_and_keeps_terminal_status(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease is not None

        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="success", lease_token=lease.lease_token, generation=lease.generation) is True
        assert (
            await store.backfill_completion_metadata(
                "run-1",
                terminal_status="success",
                lease_token=lease.lease_token,
                generation=lease.generation,
                metadata={"total_tokens": 42, "status": "error", "terminal_reason": "changed"},
            )
            is True
        )
        assert (
            await store.backfill_completion_metadata(
                "run-1",
                terminal_status="success",
                lease_token=lease.lease_token,
                generation=lease.generation + 1,
                metadata={"total_tokens": 100},
            )
            is False
        )

        row = await store.get("run-1")
        assert row["status"] == "success"
        assert row["total_tokens"] == 42
        assert _terminal_reason(row) is None
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_cancel_intent_is_idempotent_and_rollback_wins(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease is not None

        first = await store.request_cancel("run-1", "interrupt")
        duplicate = await store.request_cancel("run-1", "interrupt")
        upgraded = await store.request_cancel("run-1", "rollback")
        downgraded = await store.request_cancel("run-1", "interrupt")

        assert first is not None and first.action == "interrupt"
        assert duplicate is not None and duplicate.action == "interrupt"
        assert upgraded is not None and upgraded.action == "rollback"
        assert downgraded is not None and downgraded.action == "rollback"

        stale = await store.consume_cancel_intent("run-1", lease_token=lease.lease_token, generation=lease.generation + 1)
        intent = await store.consume_cancel_intent("run-1", lease_token=lease.lease_token, generation=lease.generation)
        assert stale is None
        assert intent is not None
        assert intent.action == "rollback"
        row = await store.get("run-1")
        assert row["status"] == "rolling_back"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_request_cancel_after_terminal_is_noop_current_terminal(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot("thread-1", "run-1", owner_worker_id="worker-a")
        assert lease is not None
        assert await store.complete_run("run-1", from_statuses={"running"}, terminal_status="success", lease_token=lease.lease_token, generation=lease.generation) is True

        result = await store.request_cancel("run-1", "rollback")
        row = await store.get("run-1")

        assert result is not None
        assert result.accepted is False
        assert result.terminal is True
        assert result.status == "success"
        assert row["status"] == "success"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_expired_recovery_checks_generation_and_expiry(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        now = datetime(2026, 7, 4, tzinfo=UTC)
        await store.create_pending_run("fresh", thread_id="thread-fresh")
        fresh_lease = await store.try_acquire_active_slot(
            "thread-fresh",
            "fresh",
            owner_worker_id="worker-a",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        )
        assert fresh_lease is not None
        assert await store.recover_expired_lease("fresh", generation=fresh_lease.generation, now=now) is False

        await store.create_pending_run("expired", thread_id="thread-expired")
        expired_lease = await store.try_acquire_active_slot(
            "thread-expired",
            "expired",
            owner_worker_id="worker-a",
            lease_expires_at=now - timedelta(seconds=1),
            now=now,
        )
        assert expired_lease is not None
        assert await store.recover_expired_lease("expired", generation=expired_lease.generation + 1, now=now) is False

        expired = await store.list_expired_active_leases(now)
        assert [lease.run_id for lease in expired] == ["expired"]
        assert await store.recover_expired_lease("expired", generation=expired_lease.generation, now=now) is True
        assert await store.recover_expired_lease("expired", generation=expired_lease.generation, now=now) is False

        row = await store.get("expired")
        assert row["status"] == "error"
        assert _terminal_reason(row) == "lease_expired_recovered"
    finally:
        await _cleanup_store(store_kind)


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_recovery_with_rollback_intent_records_rollback_failed_semantics(store_kind: str, tmp_path) -> None:
    store = await _make_store(store_kind, tmp_path)
    try:
        now = datetime(2026, 7, 4, tzinfo=UTC)
        await store.create_pending_run("run-1", thread_id="thread-1")
        lease = await store.try_acquire_active_slot(
            "thread-1",
            "run-1",
            owner_worker_id="worker-a",
            lease_expires_at=now - timedelta(seconds=1),
            now=now,
        )
        assert lease is not None
        assert await store.request_cancel("run-1", "rollback", now=now) is not None

        assert await store.recover_expired_lease("run-1", generation=lease.generation, now=now) is True

        row = await store.get("run-1")
        assert row["status"] == "error"
        assert _terminal_reason(row) == "rollback_failed_owner_lost"
    finally:
        await _cleanup_store(store_kind)
