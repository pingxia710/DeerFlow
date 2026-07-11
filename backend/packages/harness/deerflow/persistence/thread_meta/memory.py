"""In-memory ThreadMetaStore backed by LangGraph BaseStore.

Used when database.backend=memory. Delegates to the LangGraph Store's
``("threads",)`` namespace — the same namespace used by the Gateway
router for thread records.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.store.base import BaseStore

from deerflow.persistence.thread_meta.base import LEGACY_CLAIMING_STATUS, ThreadMetaConflictError, ThreadMetaCreateResult, ThreadMetaStore, strip_internal_thread_metadata
from deerflow.runtime.user_context import AUTO, DEFAULT_USER_ID, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso, now_iso

THREADS_NS: tuple[str, ...] = ("threads",)


class MemoryThreadMetaStore(ThreadMetaStore):
    def __init__(self, store: BaseStore) -> None:
        self._store = store
        self._row_lock = asyncio.Lock()

    async def _get_owned_record(
        self,
        thread_id: str,
        user_id: str | None | _AutoSentinel,
        method_name: str,
    ) -> dict | None:
        """Fetch a record and verify ownership. Returns a mutable copy, or None."""
        resolved = resolve_user_id(user_id, method_name=method_name)
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return None
        record = dict(item.value)
        if resolved is not None and record.get("user_id") != resolved:
            return None
        return record

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
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryThreadMetaStore.create")
        now = now_iso()
        record: dict[str, Any] = {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "display_name": display_name,
            "status": status,
            "metadata": strip_internal_thread_metadata(metadata),
            "values": {},
            "created_at": now,
            "updated_at": now,
        }
        async with self._row_lock:
            existing = await self._store.aget(THREADS_NS, thread_id)
            if existing is not None:
                if existing.value.get("status") == "deleting":
                    raise RuntimeError("Cannot create a thread while it is being deleted")
                existing_record = dict(existing.value)
                if existing_record.get("user_id") == resolved_user_id:
                    return ThreadMetaCreateResult(existing_record, created=False)
                raise ThreadMetaConflictError("Thread ID is already in use")
            await self._store.aput(THREADS_NS, thread_id, record)
        return ThreadMetaCreateResult(record, created=True)

    async def get(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> dict | None:
        return await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.get")

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryThreadMetaStore.search")
        filter_dict: dict[str, Any] = {}
        if metadata:
            filter_dict.update(metadata)
        if status:
            filter_dict["status"] = status
        if resolved_user_id is not None:
            filter_dict["user_id"] = resolved_user_id

        items = await self._store.asearch(
            THREADS_NS,
            filter=filter_dict or None,
            limit=limit,
            offset=offset,
        )
        return [self._item_to_dict(item) for item in items]

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return not require_existing
        if item.value.get("status") in {"deleting", LEGACY_CLAIMING_STATUS}:
            return False
        record_user_id = item.value.get("user_id")
        if record_user_id is None:
            return not require_existing
        return record_user_id == user_id

    async def update_display_name(self, thread_id: str, display_name: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        async with self._row_lock:
            record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_display_name")
            if record is None:
                return
            record["display_name"] = display_name
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)

    async def update_display_name_if_empty(
        self,
        thread_id: str,
        display_name: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> bool:
        async with self._row_lock:
            record = await self._get_owned_record(
                thread_id,
                user_id,
                "MemoryThreadMetaStore.update_display_name_if_empty",
            )
            if record is None or record.get("display_name"):
                return False
            record["display_name"] = display_name
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)
            return True

    async def update_status(self, thread_id: str, status: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        async with self._row_lock:
            record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_status")
            if record is None:
                return
            if record.get("status") == "deleting" and status != "deleting" and user_id is not None:
                return
            record["status"] = status
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)

    async def update_metadata(self, thread_id: str, metadata: dict, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        async with self._row_lock:
            record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_metadata")
            if record is None:
                return
            merged = dict(record.get("metadata") or {})
            merged.update(strip_internal_thread_metadata(metadata))
            record["metadata"] = merged
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)

    async def update_owner(self, thread_id: str, owner_user_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        async with self._row_lock:
            record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_owner")
            if record is None:
                return
            if record.get("status") == "deleting" and user_id is not None:
                return
            record["user_id"] = owner_user_id
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)

    async def claim_legacy_owner(self, thread_id: str, owner_user_id: str) -> bool:
        async with self._row_lock:
            item = await self._store.aget(THREADS_NS, thread_id)
            if item is None:
                return False
            record = dict(item.value)
            if record.get("status") == "deleting":
                return False
            if record.get("user_id") not in {None, DEFAULT_USER_ID, owner_user_id}:
                return False
            record["user_id"] = owner_user_id
            record["status"] = LEGACY_CLAIMING_STATUS
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)
            return True

    async def is_legacy_claim_complete(self, thread_id: str, owner_user_id: str) -> bool:
        item = await self._store.aget(THREADS_NS, thread_id)
        return bool(item is not None and item.value.get("status") not in {"deleting", LEGACY_CLAIMING_STATUS} and item.value.get("user_id") == owner_user_id and item.value.get("_legacy_claim_complete_owner") == owner_user_id)

    async def mark_legacy_claim_complete(self, thread_id: str, owner_user_id: str) -> bool:
        async with self._row_lock:
            item = await self._store.aget(THREADS_NS, thread_id)
            if item is None or item.value.get("status") == "deleting" or item.value.get("user_id") != owner_user_id:
                return False
            record = dict(item.value)
            record["_legacy_claim_complete_owner"] = owner_user_id
            record["status"] = "idle"
            record["updated_at"] = now_iso()
            await self._store.aput(THREADS_NS, thread_id, record)
            return True

    async def delete(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        async with self._row_lock:
            record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.delete")
            if record is None:
                return
            await self._store.adelete(THREADS_NS, thread_id)

    @staticmethod
    def _item_to_dict(item) -> dict[str, Any]:
        """Convert a Store SearchItem to the dict format expected by callers."""
        val = item.value
        return {
            "thread_id": item.key,
            "assistant_id": val.get("assistant_id"),
            "user_id": val.get("user_id"),
            "display_name": val.get("display_name"),
            "status": val.get("status", "idle"),
            "metadata": val.get("metadata", {}),
            # ``coerce_iso`` heals legacy unix-second values written by
            # earlier Gateway versions that called ``str(time.time())``.
            "created_at": coerce_iso(val.get("created_at", "")),
            "updated_at": coerce_iso(val.get("updated_at", "")),
        }
