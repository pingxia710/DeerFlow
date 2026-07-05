"""SQLAlchemy-backed RunStore implementation.

Each method acquires and releases its own short-lived session.
Run status updates happen from background workers that may live
minutes -- we don't hold connections across long execution.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.run.model import RunRow
from deerflow.runtime.runs.schemas import ACTIVE_RUN_STATUS_VALUES, INFLIGHT_RUN_STATUS_VALUES, is_active_status, is_terminal_status
from deerflow.runtime.runs.store.base import CancelIntent, CancelRequestResult, RunLease, RunStore
from deerflow.runtime.user_context import AUTO, DEFAULT_USER_ID, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso

_DEFAULT_LEASE_TTL = timedelta(seconds=30)


class RunRepository(RunStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str | None:
        """Normalize model_name for storage: strip whitespace, truncate to 128 chars."""
        if model_name is None:
            return None
        if not isinstance(model_name, str):
            model_name = str(model_name)
        normalized = model_name.strip()
        if len(normalized) > 128:
            normalized = normalized[:128]
        return normalized

    @staticmethod
    def _safe_json(obj: Any) -> Any:
        """Ensure obj is JSON-serializable. Falls back to model_dump() or str()."""
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {k: RunRepository._safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [RunRepository._safe_json(v) for v in obj]
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return obj.dict()
            except Exception:
                pass
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

    @staticmethod
    def _now(now: datetime | None = None) -> datetime:
        if now is None:
            return datetime.now(UTC)
        if now.tzinfo is None:
            return now.replace(tzinfo=UTC)
        return now

    @staticmethod
    def _thread_lock_keys(thread_id: str) -> tuple[int, int]:
        digest = hashlib.blake2b(thread_id.encode("utf-8"), digest_size=8, person=b"df_run").digest()
        return (
            int.from_bytes(digest[:4], "big", signed=True),
            int.from_bytes(digest[4:], "big", signed=True),
        )

    @staticmethod
    def _lease_expires_at(now: datetime, lease_expires_at: datetime | None) -> datetime:
        if lease_expires_at is None:
            return now + _DEFAULT_LEASE_TTL
        if lease_expires_at.tzinfo is None:
            return lease_expires_at.replace(tzinfo=UTC)
        return lease_expires_at

    @staticmethod
    def _metadata(row: RunRow) -> dict[str, Any]:
        return dict(row.metadata_json or {})

    @staticmethod
    def _lease_from_metadata(row: RunRow) -> RunLease | None:
        metadata = RunRepository._metadata(row)
        try:
            expires_at = datetime.fromisoformat(metadata["lease_expires_at"])
            heartbeat_at = datetime.fromisoformat(metadata["lease_heartbeat_at"])
            generation = int(metadata["generation"])
            owner_worker_id = str(metadata["owner_worker_id"])
            lease_token = str(metadata["lease_token"])
        except (KeyError, TypeError, ValueError):
            return None
        return RunLease(
            thread_id=row.thread_id,
            run_id=row.run_id,
            owner_worker_id=owner_worker_id,
            lease_token=lease_token,
            generation=generation,
            lease_expires_at=expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC),
            lease_heartbeat_at=heartbeat_at if heartbeat_at.tzinfo is not None else heartbeat_at.replace(tzinfo=UTC),
        )

    @staticmethod
    def _terminal_reason(row: RunRow) -> str | None:
        return RunRepository._metadata(row).get("terminal_reason")

    @staticmethod
    def _with_metadata(row: RunRow, **values: Any) -> dict[str, Any]:
        metadata = RunRepository._metadata(row)
        metadata.update({key: value for key, value in values.items() if value is not None})
        return metadata

    @staticmethod
    def _row_to_dict(row: RunRow) -> dict[str, Any]:
        d = row.to_dict()
        # Remap JSON columns to match RunStore interface
        d["metadata"] = d.pop("metadata_json", {})
        d["kwargs"] = d.pop("kwargs_json", {})
        # Convert datetime to ISO string for consistency with MemoryRunStore.
        # SQLite drops tzinfo on read despite ``DateTime(timezone=True)`` —
        # ``coerce_iso`` normalizes naive datetimes as UTC.
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = coerce_iso(val)
        return d

    async def put(
        self,
        run_id,
        *,
        thread_id,
        assistant_id=None,
        user_id: str | None | _AutoSentinel = AUTO,
        model_name: str | None = None,
        status="pending",
        multitask_strategy="reject",
        metadata=None,
        kwargs=None,
        error=None,
        created_at=None,
        follow_up_to_run_id=None,
    ):
        """Insert or update a run row.

        ``RunManager`` retries ``put`` after transient SQLite failures.  Making
        this operation idempotent prevents a successful-but-unacknowledged first
        commit from turning the retry into a primary-key failure.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.put")
        now = datetime.now(UTC)
        created = datetime.fromisoformat(created_at) if created_at else now
        values = {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "model_name": self._normalize_model_name(model_name),
            "status": status,
            "multitask_strategy": multitask_strategy,
            "metadata_json": self._safe_json(metadata) or {},
            "kwargs_json": self._safe_json(kwargs) or {},
            "error": error,
            "follow_up_to_run_id": follow_up_to_run_id,
            "updated_at": now,
        }
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                session.add(RunRow(run_id=run_id, created_at=created, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await session.commit()

    async def get(
        self,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.get")
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return None
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def list_by_thread(
        self,
        thread_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
        limit=100,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.list_by_thread")
        stmt = select(RunRow).where(RunRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(RunRow.user_id == resolved_user_id)
        stmt = stmt.order_by(RunRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def update_status(self, run_id, status, *, error=None, terminal_reason=None) -> bool:
        values: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if error is not None:
            values["error"] = error
        if terminal_reason is not None:
            async with self._sf() as session:
                row = await session.get(RunRow, run_id)
                if row is None:
                    return False
                row.status = status
                row.metadata_json = self._with_metadata(row, terminal_reason=terminal_reason)
                if error is not None:
                    row.error = error
                row.updated_at = values["updated_at"]
                await session.commit()
                return True
        async with self._sf() as session:
            result = await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(**values))
            await session.commit()
            return result.rowcount != 0

    async def update_model_name(self, run_id, model_name):
        async with self._sf() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(model_name=self._normalize_model_name(model_name), updated_at=datetime.now(UTC)))
            await session.commit()

    async def delete(
        self,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.delete")
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return
            await session.delete(row)
            await session.commit()

    async def delete_by_thread(
        self,
        thread_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.delete_by_thread")
        stmt = delete(RunRow).where(RunRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(RunRow.user_id == resolved_user_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0

    async def claim_legacy_by_thread(self, thread_id: str, owner_user_id: str) -> int:
        """Claim ownerless/default-owned runs for a legacy thread."""
        async with self._sf() as session:
            result = await session.execute(update(RunRow).where(RunRow.thread_id == thread_id, RunRow.user_id.is_(None) | (RunRow.user_id == DEFAULT_USER_ID)).values(user_id=owner_user_id, updated_at=datetime.now(UTC)))
            await session.commit()
            return result.rowcount or 0

    async def list_pending(self, *, before=None):
        if before is None:
            before_dt = datetime.now(UTC)
        elif isinstance(before, datetime):
            before_dt = before
        else:
            before_dt = datetime.fromisoformat(before)
        stmt = select(RunRow).where(RunRow.status == "pending", RunRow.created_at <= before_dt).order_by(RunRow.created_at.asc())
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def list_inflight(self, *, before=None):
        """Return persisted active runs for startup recovery."""
        if before is None:
            before_dt = datetime.now(UTC)
        elif isinstance(before, datetime):
            before_dt = before
        else:
            before_dt = datetime.fromisoformat(before)
        stmt = (
            select(RunRow)
            .where(
                RunRow.status.in_(INFLIGHT_RUN_STATUS_VALUES),
                RunRow.created_at <= before_dt,
            )
            .order_by(RunRow.created_at.asc())
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def update_run_completion(
        self,
        run_id: str,
        *,
        status: str,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_tokens: int = 0,
        llm_call_count: int = 0,
        lead_agent_tokens: int = 0,
        subagent_tokens: int = 0,
        middleware_tokens: int = 0,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        message_count: int = 0,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update status + token usage + convenience fields on run completion.

        Returns ``False`` when no run row matched the requested ``run_id``.
        """
        values: dict[str, Any] = {
            "status": status,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": llm_call_count,
            "lead_agent_tokens": lead_agent_tokens,
            "subagent_tokens": subagent_tokens,
            "middleware_tokens": middleware_tokens,
            "token_usage_by_model": self._safe_json(token_usage_by_model) or {},
            "message_count": message_count,
            "updated_at": datetime.now(UTC),
        }
        if last_ai_message is not None:
            values["last_ai_message"] = last_ai_message[:2000]
        if first_human_message is not None:
            values["first_human_message"] = first_human_message[:2000]
        if error is not None:
            values["error"] = error
        async with self._sf() as session:
            result = await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(**values))
            await session.commit()
            return result.rowcount != 0

    async def update_run_progress(
        self,
        run_id: str,
        *,
        total_input_tokens: int | None = None,
        total_output_tokens: int | None = None,
        total_tokens: int | None = None,
        llm_call_count: int | None = None,
        lead_agent_tokens: int | None = None,
        subagent_tokens: int | None = None,
        middleware_tokens: int | None = None,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        message_count: int | None = None,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
    ) -> None:
        """Update token usage + convenience fields while a run is still active."""
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        optional_counters = {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": llm_call_count,
            "lead_agent_tokens": lead_agent_tokens,
            "subagent_tokens": subagent_tokens,
            "middleware_tokens": middleware_tokens,
            "message_count": message_count,
        }
        for key, value in optional_counters.items():
            if value is not None:
                values[key] = value
        if token_usage_by_model is not None:
            values["token_usage_by_model"] = self._safe_json(token_usage_by_model) or {}
        if last_ai_message is not None:
            values["last_ai_message"] = last_ai_message[:2000]
        if first_human_message is not None:
            values["first_human_message"] = first_human_message[:2000]
        async with self._sf() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == run_id, RunRow.status == "running").values(**values))
            await session.commit()

    async def create_pending_run(self, run_id: str, *, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        await self.put(run_id, thread_id=thread_id, status="pending", **kwargs)
        row = await self.get(run_id)
        if row is None:
            raise RuntimeError(f"pending run {run_id!r} was not persisted")
        return row

    async def _next_generation(self, session: AsyncSession, thread_id: str) -> int:
        rows = (await session.execute(select(RunRow.metadata_json).where(RunRow.thread_id == thread_id))).scalars()
        generations: list[int] = []
        for metadata in rows:
            try:
                generations.append(int((metadata or {}).get("generation") or 0))
            except (TypeError, ValueError):
                continue
        return max(generations, default=0) + 1

    async def try_acquire_active_slot(
        self,
        thread_id: str,
        run_id: str,
        *,
        owner_worker_id: str,
        lease_token: str | None = None,
        lease_expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> RunLease | None:
        now_dt = self._now(now)
        expires_at = self._lease_expires_at(now_dt, lease_expires_at)
        token = lease_token or uuid4().hex
        async with self._sf() as session:
            async with session.begin():
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    key1, key2 = self._thread_lock_keys(thread_id)
                    # ponytail: per-thread Postgres xact lock; replace with a DB constraint if active slots become first-class rows.
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key1, :key2)"), {"key1": key1, "key2": key2})
                row = await session.get(RunRow, run_id)
                if row is None or row.thread_id != thread_id or row.status != "pending":
                    return None
                generation = await self._next_generation(session, thread_id)
                active_exists = exists(
                    select(RunRow.run_id).where(
                        RunRow.thread_id == thread_id,
                        RunRow.status.in_(tuple(ACTIVE_RUN_STATUS_VALUES)),
                    )
                )
                metadata = self._with_metadata(
                    row,
                    owner_worker_id=owner_worker_id,
                    lease_token=token,
                    generation=generation,
                    lease_expires_at=expires_at.isoformat(),
                    lease_heartbeat_at=now_dt.isoformat(),
                )
                result = await session.execute(
                    update(RunRow)
                    .where(
                        RunRow.run_id == run_id,
                        RunRow.thread_id == thread_id,
                        RunRow.status == "pending",
                        ~active_exists,
                    )
                    .values(status="running", metadata_json=metadata, updated_at=now_dt)
                )
                if result.rowcount == 0:
                    return None
                return RunLease(
                    thread_id=thread_id,
                    run_id=run_id,
                    owner_worker_id=owner_worker_id,
                    lease_token=token,
                    generation=generation,
                    lease_expires_at=expires_at,
                    lease_heartbeat_at=now_dt,
                )

    async def heartbeat_lease(
        self,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
        lease_expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> bool:
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    return False
                lease = self._lease_from_metadata(row)
                if lease is None or lease.lease_token != lease_token or lease.generation != generation or lease.lease_expires_at < now_dt:
                    return False
                expires_at = self._lease_expires_at(now_dt, lease_expires_at)
                row.metadata_json = self._with_metadata(row, lease_expires_at=expires_at.isoformat(), lease_heartbeat_at=now_dt.isoformat())
                row.updated_at = now_dt
                return True

    async def request_cancel(
        self,
        run_id: str,
        action: str,
        *,
        requested_by: str | None = None,
        now: datetime | None = None,
    ) -> CancelRequestResult | None:
        if action not in {"interrupt", "rollback"}:
            raise ValueError(f"unsupported cancel action: {action}")
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    return None
                metadata = self._metadata(row)
                if is_terminal_status(row.status):
                    return CancelRequestResult(
                        run_id=run_id,
                        status=row.status,
                        action=metadata.get("cancel_action"),
                        accepted=False,
                        terminal=True,
                        terminal_reason=metadata.get("terminal_reason"),
                    )
                current = metadata.get("cancel_action")
                next_action = "rollback" if action == "rollback" or current == "rollback" else "interrupt"
                metadata["cancellation_requested_at"] = metadata.get("cancellation_requested_at") or now_dt.isoformat()
                metadata["cancel_action"] = next_action
                if requested_by is not None:
                    metadata["cancel_requested_by"] = requested_by
                if next_action == "rollback":
                    metadata["rollback_requested_at"] = metadata.get("rollback_requested_at") or now_dt.isoformat()
                row.metadata_json = metadata
                row.updated_at = now_dt
                return CancelRequestResult(run_id=run_id, status=row.status, action=next_action, accepted=True)

    async def consume_cancel_intent(
        self,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
        now: datetime | None = None,
    ) -> CancelIntent | None:
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    return None
                lease = self._lease_from_metadata(row)
                if lease is None or lease.lease_token != lease_token or lease.generation != generation:
                    return None
                metadata = self._metadata(row)
                action = metadata.get("cancel_action")
                requested_at = metadata.get("cancellation_requested_at")
                if action is None or requested_at is None:
                    return None
                row.status = "rolling_back" if action == "rollback" else "cancelling"
                row.updated_at = now_dt
                return CancelIntent(run_id=run_id, action=action, requested_at=requested_at, requested_by=metadata.get("cancel_requested_by"))

    async def cas_status(
        self,
        run_id: str,
        *,
        from_statuses: set[str] | tuple[str, ...] | list[str],
        to_status: str,
        lease_token: str,
        generation: int,
        terminal_reason: str | None = None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None or row.status not in set(from_statuses):
                    return False
                lease = self._lease_from_metadata(row)
                if lease is None or lease.lease_token != lease_token or lease.generation != generation:
                    return False
                row.status = to_status
                if terminal_reason is not None:
                    row.metadata_json = self._with_metadata(row, terminal_reason=terminal_reason)
                if error is not None:
                    row.error = error
                row.updated_at = now_dt
                return True

    async def complete_run(
        self,
        run_id: str,
        *,
        from_statuses: set[str] | tuple[str, ...] | list[str],
        terminal_status: str,
        lease_token: str,
        generation: int,
        terminal_reason: str | None = None,
        error: str | None = None,
        now: datetime | None = None,
        completion_fields: dict[str, Any] | None = None,
    ) -> bool:
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    return False
                metadata = self._metadata(row)
                lease = self._lease_from_metadata(row)
                if is_terminal_status(row.status):
                    return row.status == terminal_status and metadata.get("lease_token") == lease_token and metadata.get("generation") == generation and metadata.get("terminal_reason") == terminal_reason
                if row.status not in set(from_statuses):
                    return False
                if lease is None or lease.lease_token != lease_token or lease.generation != generation:
                    return False
                row.status = terminal_status
                metadata["terminal_reason"] = terminal_reason
                metadata["completed_at"] = now_dt.isoformat()
                row.metadata_json = metadata
                if error is not None:
                    row.error = error
                for key, value in (completion_fields or {}).items():
                    if value is not None and hasattr(row, key):
                        setattr(row, key, value)
                row.updated_at = now_dt
                return True

    async def backfill_completion_metadata(
        self,
        run_id: str,
        *,
        terminal_status: str,
        lease_token: str,
        generation: int,
        terminal_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    return False
                existing = self._metadata(row)
                if row.status != terminal_status or existing.get("lease_token") != lease_token or existing.get("generation") != generation or existing.get("terminal_reason") != terminal_reason:
                    return False
                for key, value in (metadata or {}).items():
                    if key in {"status", "terminal_reason"} or value is None:
                        continue
                    if hasattr(row, key):
                        setattr(row, key, value)
                    else:
                        existing[key] = value
                row.metadata_json = existing
                row.updated_at = now_dt
                return True

    async def release_active_slot(
        self,
        thread_id: str,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
    ) -> bool:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None or row.thread_id != thread_id:
                    return False
                lease = self._lease_from_metadata(row)
                if lease is None or lease.lease_token != lease_token or lease.generation != generation:
                    return False
                metadata = self._with_metadata(row, active_slot_released_at=datetime.now(UTC).isoformat())
                row.metadata_json = metadata
                row.updated_at = datetime.now(UTC)
                return True

    async def list_expired_active_leases(self, now: datetime) -> list[RunLease]:
        now_dt = self._now(now)
        stmt = select(RunRow).where(RunRow.status.in_(tuple(ACTIVE_RUN_STATUS_VALUES)))
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars()
            leases = [lease for row in rows if (lease := self._lease_from_metadata(row)) is not None]
            return [lease for lease in leases if lease.lease_expires_at < now_dt]

    async def recover_expired_lease(
        self,
        run_id: str,
        *,
        generation: int,
        terminal_status: str = "error",
        terminal_reason: str | None = None,
        recovery_worker_id: str = "recovery",
        recovery_lease_token: str | None = None,
        now: datetime | None = None,
        error: str | None = None,
    ) -> bool:
        now_dt = self._now(now)
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None or not is_active_status(row.status):
                    return False
                lease = self._lease_from_metadata(row)
                if lease is None or lease.generation != generation or lease.lease_expires_at >= now_dt:
                    return False
                metadata = self._metadata(row)
                metadata["owner_worker_id"] = recovery_worker_id
                metadata["lease_token"] = recovery_lease_token or uuid4().hex
                metadata["lease_heartbeat_at"] = now_dt.isoformat()
                reason = terminal_reason
                if reason is None:
                    reason = "rollback_failed_owner_lost" if metadata.get("cancel_action") == "rollback" else "lease_expired_recovered"
                metadata["terminal_reason"] = reason
                metadata["completed_at"] = now_dt.isoformat()
                row.metadata_json = metadata
                row.status = terminal_status
                if error is not None:
                    row.error = error
                row.updated_at = now_dt
                return True

    async def aggregate_tokens_by_thread(
        self,
        thread_id: str,
        *,
        include_active: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate token usage for a thread.

        ``by_model`` is reduced in Python from each row's ``token_usage_by_model``
        JSON column so subagent / middleware tokens land on the model that
        actually produced them (issue #3645). Rows written before that column
        existed fall back to ``RunRow.model_name`` + ``RunRow.total_tokens``,
        preserving the legacy lead-only behavior instead of dropping the data.

        Headline totals (``total_tokens``, ``total_input_tokens``,
        ``total_output_tokens``) and the ``by_caller`` bucket are summed from
        their own columns and are therefore unaffected by the JSON column being
        empty.
        """
        statuses = ("success", "error", "running") if include_active else ("success", "error")
        _completed = RunRow.status.in_(statuses)
        _thread = RunRow.thread_id == thread_id

        stmt = select(
            RunRow.model_name,
            RunRow.total_tokens,
            RunRow.total_input_tokens,
            RunRow.total_output_tokens,
            RunRow.lead_agent_tokens,
            RunRow.subagent_tokens,
            RunRow.middleware_tokens,
            RunRow.token_usage_by_model,
        ).where(_thread, _completed)
        if user_id is not None:
            stmt = stmt.where(RunRow.user_id == user_id)

        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()

        total_tokens = total_input = total_output = total_runs = 0
        lead_agent = subagent = middleware = 0
        by_model: dict[str, dict] = {}
        for r in rows:
            total_runs += 1
            total_tokens += r.total_tokens
            total_input += r.total_input_tokens
            total_output += r.total_output_tokens
            lead_agent += r.lead_agent_tokens
            subagent += r.subagent_tokens
            middleware += r.middleware_tokens

            # ``or {}`` covers rows written before ``token_usage_by_model``
            # existed (the column is NULL on a manual ALTER ADD COLUMN without
            # backfill); fresh rows always carry the journal-produced dict.
            usage_by_model = r.token_usage_by_model or {}
            if usage_by_model:
                for model, usage in usage_by_model.items():
                    entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                    entry["tokens"] += usage.get("total_tokens", 0)
                    entry["runs"] += 1
            else:
                model = r.model_name or "unknown"
                entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                entry["tokens"] += r.total_tokens
                entry["runs"] += 1

        return {
            "total_tokens": total_tokens,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_runs": total_runs,
            "by_model": by_model,
            "by_caller": {
                "lead_agent": lead_agent,
                "subagent": subagent,
                "middleware": middleware,
            },
        }
