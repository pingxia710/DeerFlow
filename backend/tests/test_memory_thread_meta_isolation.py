"""Owner isolation tests for MemoryThreadMetaStore.

Mirrors the SQL-backed tests in test_owner_isolation.py but exercises
the in-memory LangGraph Store backend used when database.backend=memory.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langgraph.store.memory import InMemoryStore

from deerflow.persistence.thread_meta.base import LEGACY_CLAIM_COMPLETE_METADATA_KEY
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.user_context import reset_current_user, set_current_user

USER_A = SimpleNamespace(id="user-a", email="a@test.local")
USER_B = SimpleNamespace(id="user-b", email="b@test.local")


def _as_user(user):
    class _Ctx:
        def __enter__(self):
            self._token = set_current_user(user)
            return user

        def __exit__(self, *exc):
            reset_current_user(self._token)

    return _Ctx()


@pytest.fixture
def store():
    return MemoryThreadMetaStore(InMemoryStore())


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_search_isolation(store):
    """search() returns only threads owned by the current user."""
    with _as_user(USER_A):
        await store.create("t-alpha", display_name="A's thread")
    with _as_user(USER_B):
        await store.create("t-beta", display_name="B's thread")

    with _as_user(USER_A):
        results = await store.search()
        assert [r["thread_id"] for r in results] == ["t-alpha"]

    with _as_user(USER_B):
        results = await store.search()
        assert [r["thread_id"] for r in results] == ["t-beta"]


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_get_isolation(store):
    """get() returns None for threads owned by another user."""
    with _as_user(USER_A):
        await store.create("t-alpha", display_name="A's thread")

    with _as_user(USER_B):
        assert await store.get("t-alpha") is None

    with _as_user(USER_A):
        result = await store.get("t-alpha")
        assert result is not None
        assert result["display_name"] == "A's thread"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_update_display_name_denied(store):
    """User B cannot rename User A's thread."""
    with _as_user(USER_A):
        await store.create("t-alpha", display_name="original")

    with _as_user(USER_B):
        await store.update_display_name("t-alpha", "hacked")

    with _as_user(USER_A):
        row = await store.get("t-alpha")
        assert row is not None
        assert row["display_name"] == "original"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_update_status_denied(store):
    """User B cannot change status of User A's thread."""
    with _as_user(USER_A):
        await store.create("t-alpha")

    with _as_user(USER_B):
        await store.update_status("t-alpha", "error")

    with _as_user(USER_A):
        row = await store.get("t-alpha")
        assert row is not None
        assert row["status"] == "idle"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_update_metadata_denied(store):
    """User B cannot modify metadata of User A's thread."""
    with _as_user(USER_A):
        await store.create("t-alpha", metadata={"key": "original"})

    with _as_user(USER_B):
        await store.update_metadata("t-alpha", {"key": "hacked"})

    with _as_user(USER_A):
        row = await store.get("t-alpha")
        assert row is not None
        assert row["metadata"]["key"] == "original"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_delete_denied(store):
    """User B cannot delete User A's thread."""
    with _as_user(USER_A):
        await store.create("t-alpha")

    with _as_user(USER_B):
        await store.delete("t-alpha")

    with _as_user(USER_A):
        row = await store.get("t-alpha")
        assert row is not None


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_no_context_raises(store):
    """Calling methods without user context raises RuntimeError."""
    with pytest.raises(RuntimeError, match="no user context is set"):
        await store.search()


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_explicit_none_bypasses_filter(store):
    """user_id=None bypasses isolation (migration/CLI escape hatch)."""
    with _as_user(USER_A):
        await store.create("t-alpha")
    with _as_user(USER_B):
        await store.create("t-beta")

    all_rows = await store.search(user_id=None)
    assert {r["thread_id"] for r in all_rows} == {"t-alpha", "t-beta"}

    row = await store.get("t-alpha", user_id=None)
    assert row is not None


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_strict_access_denies_null_owner(store):
    await store.create("legacy", user_id=None)

    assert await store.check_access("legacy", "user-a") is True
    assert await store.check_access("legacy", "user-a", require_existing=True) is False


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_claim_legacy_owner_is_atomic_and_same_owner_idempotent(store):
    await store.create("legacy-claim", user_id="default")

    assert await store.claim_legacy_owner("legacy-claim", "owner-1") is True
    assert await store.claim_legacy_owner("legacy-claim", "owner-1") is True
    assert await store.claim_legacy_owner("legacy-claim", "owner-2") is False
    assert await store.is_legacy_claim_complete("legacy-claim", "owner-1") is False
    assert await store.mark_legacy_claim_complete("legacy-claim", "owner-1") is True
    assert await store.is_legacy_claim_complete("legacy-claim", "owner-1") is True

    row = await store.get("legacy-claim", user_id=None)
    assert row is not None
    assert row["user_id"] == "owner-1"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_client_metadata_cannot_persist_internal_legacy_claim_marker(store):
    created = await store.create(
        "marker",
        user_id="owner-1",
        metadata={LEGACY_CLAIM_COMPLETE_METADATA_KEY: "forged", "safe": 1},
    )
    assert created["metadata"] == {"safe": 1}

    await store.update_metadata(
        "marker",
        {LEGACY_CLAIM_COMPLETE_METADATA_KEY: "forged-again", "next": 2},
        user_id="owner-1",
    )
    row = await store.get("marker", user_id="owner-1")
    assert row is not None
    assert row["metadata"] == {"safe": 1, "next": 2}


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_deleting_thread_cannot_be_marked_as_claim_complete(store):
    await store.create("deleting-claim", user_id="owner-1")
    await store.update_status("deleting-claim", "deleting", user_id="owner-1")

    assert await store.mark_legacy_claim_complete("deleting-claim", "owner-1") is False
    assert await store.is_legacy_claim_complete("deleting-claim", "owner-1") is False


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_deleting_status_is_an_owner_barrier(store):
    await store.create("t-alpha", user_id="user-a")
    await store.update_status("t-alpha", "deleting", user_id="user-a")

    assert await store.check_access("t-alpha", "user-a") is False
    assert await store.check_access("t-alpha", "user-a", require_existing=True) is False

    await store.update_status("t-alpha", "running", user_id="user-a")
    assert (await store.get("t-alpha", user_id="user-a"))["status"] == "deleting"

    await store.update_status("t-alpha", "error", user_id=None)
    assert (await store.get("t-alpha", user_id="user-a"))["status"] == "error"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_deleting_status_cannot_be_overwritten_by_stale_worker_update():
    read_started = asyncio.Event()
    resume_worker = asyncio.Event()

    class PausingStore(InMemoryStore):
        pause_next_read = False

        async def aget(self, namespace, key):
            item = await super().aget(namespace, key)
            if self.pause_next_read:
                self.pause_next_read = False
                read_started.set()
                await resume_worker.wait()
            return item

    base_store = PausingStore()
    thread_store = MemoryThreadMetaStore(base_store)
    await thread_store.create("t-alpha", user_id="user-a")

    base_store.pause_next_read = True
    worker_update = asyncio.create_task(thread_store.update_status("t-alpha", "running", user_id="user-a"))
    await read_started.wait()
    delete_barrier = asyncio.create_task(thread_store.update_status("t-alpha", "deleting", user_id="user-a"))
    await asyncio.sleep(0)
    resume_worker.set()
    await asyncio.gather(worker_update, delete_barrier)

    assert (await thread_store.get("t-alpha", user_id="user-a"))["status"] == "deleting"


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_stale_metadata_update_cannot_overwrite_deleting_status():
    read_started = asyncio.Event()
    resume_metadata_update = asyncio.Event()

    class PausingStore(InMemoryStore):
        pause_next_read = False

        async def aget(self, namespace, key):
            item = await super().aget(namespace, key)
            if self.pause_next_read:
                self.pause_next_read = False
                read_started.set()
                await resume_metadata_update.wait()
            return item

    base_store = PausingStore()
    thread_store = MemoryThreadMetaStore(base_store)
    await thread_store.create("t-alpha", user_id="user-a", metadata={"before": True})

    base_store.pause_next_read = True
    metadata_update = asyncio.create_task(thread_store.update_metadata("t-alpha", {"after": True}, user_id="user-a"))
    await read_started.wait()
    delete_barrier = asyncio.create_task(thread_store.update_status("t-alpha", "deleting", user_id="user-a"))
    await asyncio.sleep(0)
    resume_metadata_update.set()
    await asyncio.gather(metadata_update, delete_barrier)

    row = await thread_store.get("t-alpha", user_id="user-a")
    assert row["status"] == "deleting"
    assert row["metadata"] == {"before": True, "after": True}


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_create_cannot_overwrite_deleting_tombstone(store):
    await store.create("t-alpha", user_id="user-a", metadata={"before": True})
    await store.update_status("t-alpha", "deleting", user_id="user-a")

    with pytest.raises(RuntimeError, match="while it is being deleted"):
        await store.create("t-alpha", user_id="user-b", metadata={"after": True})

    row = await store.get("t-alpha", user_id=None)
    assert row["user_id"] == "user-a"
    assert row["status"] == "deleting"
    assert row["metadata"] == {"before": True}
