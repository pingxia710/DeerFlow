"""In-memory run registry with optional persistent RunStore backing."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from deerflow.utils.time import now_iso as _now_iso

from .schemas import ACTIVE_RUN_STATUS_VALUES, DisconnectMode, RunStatus, is_inflight_status, is_terminal_status, run_status_value

if TYPE_CHECKING:
    from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

_RETRYABLE_SQLITE_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)

_RETRYABLE_SQLITE_ERROR_CODES = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
}

_STALE_INFLIGHT_TIMEOUT = timedelta(minutes=30)
_STALE_INFLIGHT_ERROR = "Worker lost after no run progress before the recovery timeout."
_ACTIVE_SLOT_LEASE_TTL = timedelta(seconds=30)


def _is_retryable_persistence_error(exc: BaseException) -> bool:
    """Return True for transient SQLite persistence failures.

    SQLite lock contention normally surfaces through either sqlite3 exceptions
    or SQLAlchemy wrappers.  The short bounded retry here protects run status
    finalization from transient writer pressure without hiding permanent
    failures forever.
    """

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        message = str(current).lower()
        if any(fragment in message for fragment in _RETRYABLE_SQLITE_MESSAGES):
            return True
        if isinstance(current, (sqlite3.OperationalError, sqlite3.DatabaseError)):
            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code in _RETRYABLE_SQLITE_ERROR_CODES:
                return True
        for chained in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(chained, BaseException):
                pending.append(chained)
    return False


def _record_matches_user_id(record: RunRecord, user_id: str | None) -> bool:
    if user_id is None:
        return True
    return record.user_id == user_id


def _parse_run_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class PersistenceRetryPolicy:
    """Bounded retry policy for short run-store writes."""

    max_attempts: int = 5
    initial_delay: float = 0.05
    max_delay: float = 1.0
    backoff_factor: float = 2.0


@dataclass
class RunRecord:
    """Mutable record for a single run."""

    run_id: str
    thread_id: str
    assistant_id: str | None
    status: RunStatus | str
    on_disconnect: DisconnectMode
    multitask_strategy: str = "reject"
    metadata: dict = field(default_factory=dict)
    kwargs: dict = field(default_factory=dict)
    user_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    abort_action: str = "interrupt"
    error: str | None = None
    terminal_reason: str | None = None
    model_name: str | None = None
    store_only: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    # Per-model token breakdown
    token_usage_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    message_count: int = 0
    last_ai_message: str | None = None
    first_human_message: str | None = None
    round_id: str | None = None
    lease_token: str | None = None
    lease_generation: int | None = None
    lease_owner_worker_id: str | None = None


class RunManager:
    """In-memory run registry with optional persistent RunStore backing.

    All mutations are protected by an asyncio lock. When a ``store`` is
    provided, serializable metadata is also persisted to the store so
    that run history survives process restarts.
    """

    def __init__(
        self,
        store: RunStore | None = None,
        *,
        round_store: Any | None = None,
        persistence_retry_policy: PersistenceRetryPolicy | None = None,
        terminal_cleanup_delay: float = 300,
        worker_id: str | None = None,
    ) -> None:
        self._runs: dict[str, RunRecord] = {}
        # Secondary index: thread_id -> insertion-ordered run_id set (a dict is
        # used as an ordered set), maintained in lockstep with ``_runs`` so
        # per-thread queries avoid O(total in-memory runs) full scans while
        # preserving ``_runs`` iteration order (see ``_thread_records_locked``).
        self._runs_by_thread: dict[str, dict[str, None]] = {}
        self._lock = asyncio.Lock()
        self._store = store
        self._round_store = round_store
        self._persistence_retry_policy = persistence_retry_policy or PersistenceRetryPolicy()
        self._terminal_cleanup_delay = terminal_cleanup_delay
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._worker_id = worker_id or f"run-manager-{uuid.uuid4().hex}"

    def _index_run_locked(self, record: RunRecord) -> None:
        """Register *record* in the thread index. Caller must hold ``self._lock``."""
        self._runs_by_thread.setdefault(record.thread_id, {})[record.run_id] = None

    def _unindex_run_locked(self, run_id: str, thread_id: str) -> None:
        """Drop *run_id* from the thread index. Caller must hold ``self._lock``."""
        bucket = self._runs_by_thread.get(thread_id)
        if bucket is not None:
            bucket.pop(run_id, None)
            if not bucket:
                self._runs_by_thread.pop(thread_id, None)

    def _thread_records_locked(self, thread_id: str) -> list[RunRecord]:
        """Return live in-memory records for *thread_id*. Caller must hold ``self._lock``.

        Uses the ``_runs_by_thread`` index for O(runs-in-thread) lookup instead of
        scanning every in-memory run. Correctness rests on the index and ``_runs``
        being mutated in lockstep under ``self._lock`` (no ``await`` between the two
        writes), so any holder of the lock sees them agree. The ``self._runs.get``
        filter is defense-in-depth, not reconciliation: it drops a stale id still in
        the index but already gone from ``_runs``, yet it cannot recover a run that is
        in ``_runs`` but missing from the index (such a run would be silently
        omitted). It guards only that one direction, should a future refactor ever
        break the lockstep invariant.
        """
        run_ids = self._runs_by_thread.get(thread_id)
        if not run_ids:
            return []
        return [record for run_id in run_ids if (record := self._runs.get(run_id)) is not None]

    @staticmethod
    def _sort_newest_first(records: Iterable[RunRecord], tie_rank: dict[str, int] | None = None) -> list[RunRecord]:
        ranks = tie_rank or {}
        return sorted(
            records,
            key=lambda record: (
                record.created_at or "",
                record.updated_at or "",
                record.lease_generation or 0,
                ranks.get(record.run_id, -1),
                record.run_id,
            ),
            reverse=True,
        )

    def _store_put_payload(self, record: RunRecord, *, error: str | None = None) -> dict[str, Any]:
        metadata = dict(record.metadata or {})
        if record.round_id is not None:
            metadata["round_id"] = record.round_id
        if record.terminal_reason is not None:
            metadata["terminal_reason"] = record.terminal_reason
        if record.lease_token is not None and record.lease_generation is not None:
            metadata.setdefault("owner_worker_id", record.lease_owner_worker_id or self._worker_id)
            metadata.setdefault("lease_token", record.lease_token)
            metadata.setdefault("generation", record.lease_generation)
        payload = {
            "thread_id": record.thread_id,
            "assistant_id": record.assistant_id,
            "status": run_status_value(record.status),
            "multitask_strategy": record.multitask_strategy,
            "metadata": metadata,
            "kwargs": record.kwargs or {},
            "error": error if error is not None else record.error,
            "created_at": record.created_at,
            "model_name": record.model_name,
        }
        if record.user_id is not None:
            payload["user_id"] = record.user_id
        return payload

    async def _call_store_with_retry(
        self,
        operation_name: str,
        run_id: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run a short store operation with bounded retries for SQLite pressure."""
        policy = self._persistence_retry_policy
        attempt = 1
        delay = policy.initial_delay
        while True:
            try:
                return await operation()
            except Exception as exc:
                retryable = _is_retryable_persistence_error(exc)
                if attempt >= policy.max_attempts or not retryable:
                    raise
                logger.warning(
                    "Transient persistence failure during %s for run %s (attempt %d/%d); retrying",
                    operation_name,
                    run_id,
                    attempt,
                    policy.max_attempts,
                    exc_info=True,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                delay = min(policy.max_delay, delay * policy.backoff_factor if delay else policy.initial_delay)
                attempt += 1

    async def _persist_snapshot_to_store(self, run_id: str, payload: dict[str, Any]) -> bool:
        """Best-effort persist a previously captured run snapshot."""
        if self._store is None:
            return True
        try:
            await self._call_store_with_retry(
                "put",
                run_id,
                lambda: self._store.put(run_id, **payload),
            )
            return True
        except Exception:
            logger.warning("Failed to persist run %s to store", run_id, exc_info=True)
            return False

    async def _persist_new_run_to_store(self, record: RunRecord) -> None:
        """Persist a newly created run record to the backing store.

        Initial run creation is part of the run visibility boundary: callers
        should not observe a run in memory unless its backing store row exists.
        Unlike follow-up status/model updates, failures are propagated so the
        caller can treat creation as failed. Rollback is the caller's
        responsibility after inserting the record into ``_runs``.
        """
        if self._store is None:
            return
        await self._call_store_with_retry(
            "put",
            record.run_id,
            lambda: self._store.put(record.run_id, **self._store_put_payload(record)),
        )

    @staticmethod
    def _record_has_lease(record: RunRecord) -> bool:
        return record.lease_token is not None and record.lease_generation is not None

    def _apply_lease_to_record(self, record: RunRecord, lease: Any) -> None:
        record.lease_token = lease.lease_token
        record.lease_generation = lease.generation
        record.lease_owner_worker_id = lease.owner_worker_id
        record.metadata = {
            **(record.metadata or {}),
            "owner_worker_id": lease.owner_worker_id,
            "lease_token": lease.lease_token,
            "generation": lease.generation,
            "lease_expires_at": lease.lease_expires_at.isoformat(),
            "lease_heartbeat_at": lease.lease_heartbeat_at.isoformat(),
        }

    async def _persist_new_run_with_active_slot(self, record: RunRecord) -> bool | None:
        """Persist *record* and acquire the thread active slot.

        Returns ``None`` when the configured store has no lease/CAS support,
        ``False`` when another active slot won the race, and ``True`` when this
        record owns the slot. Supported stores transition the persisted row to
        ``running`` while the in-memory worker still moves through
        ``pending -> running`` via ``set_status``.
        """
        if self._store is None:
            return None
        try:
            await self._bind_round_to_run(record)
            payload = self._store_put_payload(record)
            pending_kwargs = {k: v for k, v in payload.items() if k not in {"thread_id", "status"}}
            await self._call_store_with_retry(
                "create_pending_run",
                record.run_id,
                lambda: self._store.create_pending_run(record.run_id, thread_id=record.thread_id, **pending_kwargs),
            )
            lease = await self._call_store_with_retry(
                "try_acquire_active_slot",
                record.run_id,
                lambda: self._store.try_acquire_active_slot(
                    record.thread_id,
                    record.run_id,
                    owner_worker_id=self._worker_id,
                    lease_expires_at=datetime.now(UTC) + _ACTIVE_SLOT_LEASE_TTL,
                ),
            )
        except NotImplementedError:
            try:
                await self._store.delete(record.run_id)
            except Exception:
                logger.warning("Failed to delete lease-unsupported pending run %s", record.run_id, exc_info=True)
            return None
        if lease is None:
            try:
                await self._store.delete(record.run_id)
            except Exception:
                logger.warning("Failed to delete rejected pending run %s", record.run_id, exc_info=True)
            return False
        self._apply_lease_to_record(record, lease)
        return True

    async def _persist_to_store(self, record: RunRecord, *, error: str | None = None) -> bool:
        """Best-effort persist run record to backing store."""
        return await self._persist_snapshot_to_store(
            record.run_id,
            self._store_put_payload(record, error=error),
        )

    async def _persist_status(self, record: RunRecord, status: RunStatus, *, error: str | None = None, terminal_reason: str | None = None) -> bool:
        """Best-effort persist a status transition to the backing store."""
        if self._store is None:
            return True
        if self._record_has_lease(record) and is_terminal_status(status):
            try:
                return bool(
                    await self._call_store_with_retry(
                        "complete_run",
                        record.run_id,
                        lambda: self._store.complete_run(
                            record.run_id,
                            from_statuses=ACTIVE_RUN_STATUS_VALUES,
                            terminal_status=status.value,
                            lease_token=record.lease_token or "",
                            generation=record.lease_generation or 0,
                            terminal_reason=terminal_reason,
                            error=error,
                        ),
                    )
                )
            except NotImplementedError:
                pass
            except Exception:
                logger.warning("Failed to persist lease terminal status for run %s", record.run_id, exc_info=True)
                return False
        row_recovery_payload = self._store_put_payload(record, error=error)
        try:
            updated = await self._call_store_with_retry(
                "update_status",
                record.run_id,
                lambda: self._store.update_status(record.run_id, status.value, error=error, terminal_reason=terminal_reason),
            )
            if updated is False:
                return await self._persist_snapshot_to_store(record.run_id, row_recovery_payload)
            return True
        except Exception:
            logger.warning("Failed to persist status update for run %s", record.run_id, exc_info=True)
            return False

    @staticmethod
    def _record_from_store(row: dict[str, Any]) -> RunRecord:
        """Build a read-only runtime record from a serialized store row.

        NULL status/on_disconnect columns (e.g. from rows written before those
        columns were added) default to ``pending`` and ``cancel`` respectively.
        """
        status = run_status_value(row.get("status")) or RunStatus.pending.value
        try:
            record_status: RunStatus | str = RunStatus(status)
        except ValueError:
            record_status = status

        metadata = row.get("metadata") or {}
        lease_generation = metadata.get("generation", row.get("generation"))
        try:
            lease_generation = int(lease_generation) if lease_generation is not None else None
        except (TypeError, ValueError):
            lease_generation = None
        return RunRecord(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            assistant_id=row.get("assistant_id"),
            status=record_status,
            on_disconnect=DisconnectMode(row.get("on_disconnect") or DisconnectMode.cancel.value),
            multitask_strategy=row.get("multitask_strategy") or "reject",
            metadata=metadata,
            kwargs=row.get("kwargs") or {},
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            user_id=row.get("user_id"),
            error=row.get("error"),
            terminal_reason=row.get("terminal_reason") or metadata.get("terminal_reason"),
            model_name=row.get("model_name"),
            store_only=True,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            llm_call_count=row.get("llm_call_count") or 0,
            lead_agent_tokens=row.get("lead_agent_tokens") or 0,
            subagent_tokens=row.get("subagent_tokens") or 0,
            middleware_tokens=row.get("middleware_tokens") or 0,
            token_usage_by_model=row.get("token_usage_by_model") or {},
            message_count=row.get("message_count") or 0,
            last_ai_message=row.get("last_ai_message"),
            first_human_message=row.get("first_human_message"),
            round_id=metadata.get("round_id") if isinstance(metadata.get("round_id"), str) else None,
            lease_token=metadata.get("lease_token") or row.get("lease_token"),
            lease_generation=lease_generation,
            lease_owner_worker_id=metadata.get("owner_worker_id") or row.get("owner_worker_id"),
        )

    @staticmethod
    def _message_text(value: Any) -> str:
        if value is None:
            return ""
        content = value.get("content") if isinstance(value, dict) else getattr(value, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return str(content or "")

    @classmethod
    def _current_intent_from_kwargs(cls, kwargs: dict[str, Any] | None) -> str | None:
        raw_input = (kwargs or {}).get("input")
        if not isinstance(raw_input, dict):
            return None
        messages = raw_input.get("messages")
        if not isinstance(messages, list):
            return None
        for message in reversed(messages):
            role = (message.get("role") or message.get("type")) if isinstance(message, dict) else getattr(message, "type", None)
            if role in {"human", "user"}:
                text = cls._message_text(message).strip()
                return text[:4000] if text else None
        return None

    @staticmethod
    def _round_context_from_info(info: dict[str, Any], *, run_id: str, current_intent: str | None) -> dict[str, Any]:
        context = {
            "round_id": info.get("round_id"),
            "state": info.get("state"),
            "current_run_id": run_id,
            "source_goal_run_id": info.get("source_goal_run_id"),
            "parent_round_id": info.get("parent_round_id"),
            "current_intent": current_intent or info.get("current_intent"),
            "accepted_next_action": info.get("accepted_next_action") or info.get("next_action"),
        }
        for key in ("artifact_refs", "evidence_refs"):
            refs = info.get(key)
            if isinstance(refs, list):
                context[key] = [ref for ref in refs if isinstance(ref, str) and ref]
        return context

    async def _bind_round_to_run(self, record: RunRecord) -> None:
        if self._round_store is None:
            return
        current_intent = self._current_intent_from_kwargs(record.kwargs)
        info = await self._round_store.bind_run(
            thread_id=record.thread_id,
            run_id=record.run_id,
            user_id=record.user_id,
            current_intent=current_intent,
            metadata=record.metadata,
        )
        round_id = info.get("round_id")
        if isinstance(round_id, str) and round_id:
            record.round_id = round_id
            record.metadata = {
                **(record.metadata or {}),
                "round_id": round_id,
                "round_context": self._round_context_from_info(info, run_id=record.run_id, current_intent=current_intent),
            }

    async def _persist_round_state_for_status(
        self,
        record: RunRecord,
        status: RunStatus,
        *,
        error: str | None = None,
        terminal_reason: str | None = None,
        next_action: str | None = None,
    ) -> None:
        if self._round_store is None:
            return
        if status == RunStatus.running:
            state = "executing"
            event_type = "run.executing"
        elif status == RunStatus.success:
            state = "closed"
            event_type = "round.closed"
        elif is_terminal_status(status):
            state = "blocked"
            event_type = "round.blocked"
        else:
            return
        try:
            info = await self._round_store.set_run_state(
                record.run_id,
                state=state,
                event_type=event_type,
                content={"run_status": status.value, "terminal_reason": terminal_reason, "error": error},
                next_action=next_action,
            )
            if isinstance(info, dict):
                context = dict(record.metadata.get("round_context") or {})
                context.update(self._round_context_from_info(info, run_id=record.run_id, current_intent=context.get("current_intent")))
                record.round_id = context.get("round_id") if isinstance(context.get("round_id"), str) else record.round_id
                record.metadata = {**(record.metadata or {}), "round_context": context}
                if record.round_id is not None:
                    record.metadata["round_id"] = record.round_id
                await self._persist_snapshot_to_store(record.run_id, self._store_put_payload(record))
        except Exception:
            logger.warning("Failed to persist round state for run %s", record.run_id, exc_info=True)

    async def update_run_completion(self, run_id: str, **kwargs) -> None:
        """Persist token usage and completion data to the backing store."""
        row_recovery_payload: dict[str, Any] | None = None
        lease_payload: tuple[str, int, str, str | None] | None = None
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                for key, value in kwargs.items():
                    if key == "status":
                        continue
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
                row_recovery_payload = self._store_put_payload(record, error=kwargs.get("error"))
                status_value = run_status_value(kwargs.get("status")) or run_status_value(record.status)
                if self._record_has_lease(record) and status_value is not None and is_terminal_status(status_value):
                    lease_payload = (record.lease_token or "", record.lease_generation or 0, status_value, record.terminal_reason)
        if self._store is None:
            return
        lease_backfilled = False
        if lease_payload is not None:
            lease_token, generation, terminal_status, terminal_reason = lease_payload
            try:
                updated = await self._call_store_with_retry(
                    "backfill_completion_metadata",
                    run_id,
                    lambda: self._store.backfill_completion_metadata(
                        run_id,
                        terminal_status=terminal_status,
                        lease_token=lease_token,
                        generation=generation,
                        terminal_reason=terminal_reason,
                        metadata={k: v for k, v in kwargs.items() if k != "status"},
                    ),
                )
                if updated is False:
                    logger.warning("Lease completion backfill for %s affected no rows", run_id)
                    return
                lease_backfilled = True
            except NotImplementedError:
                pass
            except Exception:
                logger.warning("Failed to backfill lease completion for %s", run_id, exc_info=True)
                return
        if not lease_backfilled:
            try:
                updated = await self._call_store_with_retry(
                    "update_run_completion",
                    run_id,
                    lambda: self._store.update_run_completion(run_id, **kwargs),
                )
                if updated is False:
                    if row_recovery_payload is None:
                        logger.warning("Failed to recreate missing run %s for completion persistence", run_id)
                        return
                    if not await self._persist_snapshot_to_store(run_id, row_recovery_payload):
                        return
                    recovered = await self._call_store_with_retry(
                        "update_run_completion",
                        run_id,
                        lambda: self._store.update_run_completion(run_id, **kwargs),
                    )
                    if recovered is False:
                        logger.warning("Run completion update for %s affected no rows after row recreation", run_id)
            except Exception:
                logger.warning("Failed to persist run completion for %s", run_id, exc_info=True)
        if record is not None:
            status_value = kwargs.get("status")
            try:
                status = RunStatus(status_value) if isinstance(status_value, str) else record.status
            except ValueError:
                status = record.status
            if status == RunStatus.success and isinstance(kwargs.get("last_ai_message"), str):
                await self._persist_round_state_for_status(
                    record,
                    RunStatus.success,
                    terminal_reason=record.terminal_reason or "success",
                    next_action=kwargs["last_ai_message"],
                )

    async def update_run_progress(self, run_id: str, **kwargs) -> None:
        """Persist a running token/message snapshot without changing status."""
        should_persist = True
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                should_persist = record.status == RunStatus.running
            if record is not None and should_persist:
                for key, value in kwargs.items():
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
        if should_persist and self._store is not None:
            try:
                await self._call_store_with_retry(
                    "update_run_progress",
                    run_id,
                    lambda: self._store.update_run_progress(run_id, **kwargs),
                )
            except Exception:
                logger.warning("Failed to persist run progress for %s", run_id, exc_info=True)

    async def create(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        user_id: str | None = None,
    ) -> RunRecord:
        """Create a new pending run and register it."""
        run_id = str(uuid.uuid4())
        now = _now_iso()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.pending,
            on_disconnect=on_disconnect,
            multitask_strategy=multitask_strategy,
            metadata=metadata or {},
            kwargs=kwargs or {},
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._runs[run_id] = record
            self._index_run_locked(record)
            persisted = False
            try:
                await self._persist_new_run_to_store(record)
                await self._bind_round_to_run(record)
                await self._persist_new_run_to_store(record)
                persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                if self._store is not None:
                    try:
                        await self._store.delete(run_id)
                    except Exception:
                        logger.warning("Failed to delete partially persisted run %s", run_id, exc_info=True)
                raise
            finally:
                # Also covers cancellation, which bypasses ``except Exception``.
                if not persisted:
                    self._runs.pop(run_id, None)
                    self._unindex_run_locked(run_id, record.thread_id)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def get(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """Return a run record by ID, or ``None``.

        Args:
            run_id: The run ID to look up.
            user_id: Optional user ID for permission filtering when hydrating from store.
        """
        await self.recover_stale_inflight_runs(run_id=run_id, user_id=user_id)
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record if _record_matches_user_id(record, user_id) else None
        if self._store is None:
            return None
        try:
            row = await self._store.get(run_id, user_id=user_id)
        except Exception:
            logger.warning("Failed to hydrate run %s from store", run_id, exc_info=True)
            return None
        # Re-check after store await: a concurrent create() may have inserted the
        # in-memory record while the store call was in flight.
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record if _record_matches_user_id(record, user_id) else None
        if row is None:
            return None
        try:
            return self._record_from_store(row)
        except Exception:
            logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
            return None

    async def aget(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """Return a run record by ID, checking the persistent store as fallback.

        Alias for :meth:`get` for backward compatibility.
        """
        return await self.get(run_id, user_id=user_id)

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        """Return runs for a given thread, newest first, at most ``limit`` records.

        In-memory runs take precedence only when the same ``run_id`` exists in both
        memory and the backing store. The merged result is then sorted newest-first
        by ``created_at`` and trimmed to ``limit`` (default 100).

        Args:
            thread_id: The thread ID to filter by.
            user_id: Optional user ID for permission filtering when hydrating from store.
            limit: Maximum number of runs to return.
        """
        await self.recover_stale_inflight_runs(thread_id=thread_id, user_id=user_id)
        async with self._lock:
            thread_memory_records = self._thread_records_locked(thread_id)
            memory_tie_rank = {record.run_id: index for index, record in enumerate(thread_memory_records)}
            memory_records = [record for record in thread_memory_records if _record_matches_user_id(record, user_id)]
        if self._store is None:
            return self._sort_newest_first(memory_records, memory_tie_rank)[:limit]
        records_by_id = {record.run_id: record for record in memory_records}
        try:
            rows = await self._store.list_by_thread(thread_id, user_id=user_id, limit=limit)
        except Exception:
            logger.warning("Failed to hydrate runs for thread %s from store", thread_id, exc_info=True)
            return self._sort_newest_first(memory_records, memory_tie_rank)[:limit]
        for row in rows:
            run_id = row.get("run_id")
            if run_id and run_id not in records_by_id:
                try:
                    records_by_id[run_id] = self._record_from_store(row)
                except Exception:
                    logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
        return self._sort_newest_first(records_by_id.values(), memory_tie_rank)[:limit]

    def _schedule_terminal_cleanup(self, run_id: str) -> None:
        if self._terminal_cleanup_delay < 0:
            return
        task = asyncio.create_task(self.cleanup(run_id, delay=self._terminal_cleanup_delay))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None, terminal_reason: str | None = None) -> None:
        """Transition a run to a new status."""
        previous_status: RunStatus | str | None = None
        previous_error: str | None = None
        previous_terminal_reason: str | None = None
        previous_updated_at: str | None = None
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_status called for unknown run %s", run_id)
                return
            rollback_completion = record.status == RunStatus.interrupted and record.abort_action == "rollback" and status == RunStatus.error and error == "Rolled back by user"
            if is_terminal_status(record.status) and record.status != status and not rollback_completion:
                logger.info("Ignoring late status transition for terminal run %s: %s -> %s", run_id, run_status_value(record.status), status.value)
                return
            if is_terminal_status(record.status) and record.terminal_reason is not None and terminal_reason not in {None, record.terminal_reason}:
                logger.info("Ignoring late terminal_reason overwrite for terminal run %s: %s -> %s", run_id, record.terminal_reason, terminal_reason)
                return
            previous_status = record.status
            previous_error = record.error
            previous_terminal_reason = record.terminal_reason
            previous_updated_at = record.updated_at
            record.status = status
            record.updated_at = _now_iso()
            if terminal_reason is not None:
                record.terminal_reason = terminal_reason
            if error is not None:
                record.error = error
        persisted = await self._persist_status(record, status, error=error, terminal_reason=terminal_reason)
        if self._record_has_lease(record) and is_terminal_status(status) and not persisted:
            async with self._lock:
                live = self._runs.get(run_id)
                if live is record and live.status == status:
                    live.status = previous_status if previous_status is not None else live.status
                    live.error = previous_error
                    live.terminal_reason = previous_terminal_reason
                    live.updated_at = previous_updated_at or live.updated_at
            logger.warning("Lease terminal status for run %s was not persisted; restored in-memory status for recovery", run_id)
            return
        await self._persist_round_state_for_status(record, status, error=error, terminal_reason=terminal_reason)
        if is_terminal_status(status):
            self._schedule_terminal_cleanup(run_id)
        logger.info("Run %s -> %s", run_id, status.value)

    async def heartbeat_active_lease(self, record: RunRecord) -> bool:
        if self._store is None or not self._record_has_lease(record):
            return True
        try:
            return bool(
                await self._call_store_with_retry(
                    "heartbeat_lease",
                    record.run_id,
                    lambda: self._store.heartbeat_lease(
                        record.run_id,
                        lease_token=record.lease_token or "",
                        generation=record.lease_generation or 0,
                        lease_expires_at=datetime.now(UTC) + _ACTIVE_SLOT_LEASE_TTL,
                    ),
                )
            )
        except NotImplementedError:
            return True
        except Exception:
            logger.warning("Failed to heartbeat lease for run %s", record.run_id, exc_info=True)
            return False

    async def consume_cancel_intent(self, record: RunRecord) -> Any | None:
        if self._store is None or not self._record_has_lease(record):
            return None
        try:
            intent = await self._call_store_with_retry(
                "consume_cancel_intent",
                record.run_id,
                lambda: self._store.consume_cancel_intent(
                    record.run_id,
                    lease_token=record.lease_token or "",
                    generation=record.lease_generation or 0,
                ),
            )
        except NotImplementedError:
            return None
        except Exception:
            logger.warning("Failed to consume cancel intent for run %s", record.run_id, exc_info=True)
            return None
        if intent is None:
            return None
        async with self._lock:
            live = self._runs.get(record.run_id)
            if live is not None:
                live.abort_action = intent.action
                live.status = "rolling_back" if intent.action == "rollback" else "cancelling"
                live.updated_at = _now_iso()
        return intent

    async def _persist_model_name(self, run_id: str, model_name: str | None) -> None:
        """Best-effort persist model_name update to the backing store."""
        if self._store is None:
            return
        try:
            await self._call_store_with_retry(
                "update_model_name",
                run_id,
                lambda: self._store.update_model_name(run_id, model_name),
            )
        except Exception:
            logger.warning("Failed to persist model_name update for run %s", run_id, exc_info=True)

    async def update_model_name(self, run_id: str, model_name: str | None) -> None:
        """Update the model name for a run."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("update_model_name called for unknown run %s", run_id)
                return
            record.model_name = model_name
            record.updated_at = _now_iso()
        await self._persist_model_name(run_id, model_name)
        logger.info("Run %s model_name=%s", run_id, model_name)

    async def cancel(self, run_id: str, *, action: str = "interrupt") -> bool:
        """Request cancellation of a run.

        Args:
            run_id: The run ID to cancel.
            action: "interrupt" keeps checkpoint, "rollback" reverts to pre-run state.

        Sets the abort event with the action reason and cancels the asyncio task.
        Returns ``True`` if cancellation was initiated **or** the run was already
        interrupted (idempotent — a second cancel is a no-op success).
        Returns ``False`` only when the run is unknown to this worker or has
        reached a terminal state other than interrupted (completed, failed, etc.).
        """
        if self._store is not None:
            try:
                cancel_result = await self._call_store_with_retry(
                    "request_cancel",
                    run_id,
                    lambda: self._store.request_cancel(run_id, action, requested_by=self._worker_id),
                )
            except NotImplementedError:
                cancel_result = None
            if cancel_result is not None and cancel_result.accepted and cancel_result.terminal is False:
                async with self._lock:
                    record = self._runs.get(run_id)
                    if record is not None and is_inflight_status(record.status):
                        record.abort_action = action
                        record.abort_event.set()
                        if record.task is not None and not record.task.done():
                            record.task.cancel()
                        if self._record_has_lease(record):
                            record.updated_at = _now_iso()
                            logger.info("Run %s cancel intent recorded (action=%s)", run_id, action)
                            return True
                if run_id not in self._runs:
                    logger.info("Run %s cancel intent recorded for non-local worker (action=%s)", run_id, action)
                    return True

        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            if record.status == RunStatus.interrupted:
                return True  # idempotent — already cancelled on this worker
            if not is_inflight_status(record.status):
                return False
            record.abort_action = action
            record.abort_event.set()
            if record.task is not None and not record.task.done():
                record.task.cancel()
            record.status = RunStatus.interrupted
            terminal_reason = "cancelled" if action != "rollback" else None
            if terminal_reason is not None:
                record.terminal_reason = terminal_reason
            record.updated_at = _now_iso()
        await self._persist_status(record, RunStatus.interrupted, terminal_reason=terminal_reason)
        await self._persist_round_state_for_status(record, RunStatus.interrupted, terminal_reason=terminal_reason)
        self._schedule_terminal_cleanup(run_id)
        logger.info("Run %s cancelled (action=%s)", run_id, action)
        return True

    async def _list_persisted_inflight_for_thread(self, thread_id: str) -> list[RunRecord]:
        """Best-effort persisted active-run lookup for fail-safe multitask checks."""
        if self._store is None:
            return []
        try:
            rows = await self._call_store_with_retry(
                "list_inflight",
                "*",
                lambda: self._store.list_inflight(),
            )
        except Exception:
            logger.warning("Failed to list persisted inflight runs for thread %s", thread_id, exc_info=True)
            raise

        records: list[RunRecord] = []
        for row in rows:
            if row.get("thread_id") != thread_id:
                continue
            try:
                records.append(self._record_from_store(row))
            except Exception:
                logger.warning("Failed to map persisted inflight run row for thread %s", thread_id, exc_info=True)
                raise
        return records

    async def create_or_reject(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        model_name: str | None = None,
        user_id: str | None = None,
    ) -> RunRecord:
        """Atomically check for inflight runs and create a leased new run.

        For ``reject`` strategy, raises ``ConflictError`` if thread
        already has an inflight run. For ``interrupt``/``rollback``, only local
        legacy inflight runs without an active-slot owner may be cancelled after
        the new run acquires the slot. If an existing active slot is still held,
        the new run is rejected instead of being inserted with a plain ``put``.

        This method holds the lock across both the check and the insert,
        eliminating the TOCTOU race in separate ``has_inflight`` + ``create``.
        """
        run_id = str(uuid.uuid4())
        now = _now_iso()

        _supported_strategies = ("reject", "interrupt", "rollback")
        interrupted_records: list[RunRecord] = []

        persisted_inflight = await self._list_persisted_inflight_for_thread(thread_id)

        async with self._lock:
            if multitask_strategy not in _supported_strategies:
                raise UnsupportedStrategyError(f"Multitask strategy '{multitask_strategy}' is not yet supported. Supported strategies: {', '.join(_supported_strategies)}")

            memory_by_id = {r.run_id: r for r in self._thread_records_locked(thread_id) if is_inflight_status(r.status)}
            inflight = list(memory_by_id.values())
            persisted_foreign_inflight = [r for r in persisted_inflight if r.run_id not in memory_by_id and is_inflight_status(r.status)]

            if multitask_strategy == "reject" and (inflight or persisted_foreign_inflight):
                raise ConflictError(f"Thread {thread_id} already has an active run")
            if multitask_strategy in ("interrupt", "rollback") and persisted_foreign_inflight and self._store is not None:
                for r in persisted_foreign_inflight:
                    try:
                        await self._store.request_cancel(r.run_id, multitask_strategy, requested_by=self._worker_id)
                    except NotImplementedError:
                        pass
                raise ConflictError(f"Thread {thread_id} already has an active run; cancellation requested")

            # Persisted-only rows may belong to another worker. Interrupt/rollback
            # records the cancel intent, then returns Conflict; the next run must
            # wait until the owner releases the active slot or lease recovery marks
            # it terminal.

            if multitask_strategy in ("interrupt", "rollback") and inflight:
                logger.info(
                    "Preparing to cancel %d inflight run(s) on thread %s (strategy=%s)",
                    len(inflight),
                    thread_id,
                    multitask_strategy,
                )

            record = RunRecord(
                run_id=run_id,
                thread_id=thread_id,
                assistant_id=assistant_id,
                status=RunStatus.pending,
                on_disconnect=on_disconnect,
                multitask_strategy=multitask_strategy,
                metadata=metadata or {},
                kwargs=kwargs or {},
                user_id=user_id,
                created_at=now,
                updated_at=now,
                model_name=model_name,
            )
            self._runs[run_id] = record
            self._index_run_locked(record)
            persisted = False
            try:
                if self._store is not None:
                    active_slot = await self._persist_new_run_with_active_slot(record)
                    if active_slot is False:
                        raise ConflictError(f"Thread {thread_id} already has an active run")
                    persisted = active_slot is True
                if not persisted:
                    await self._persist_new_run_to_store(record)
                    await self._bind_round_to_run(record)
                    await self._persist_new_run_to_store(record)
                    persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                if self._store is not None:
                    try:
                        await self._store.delete(run_id)
                    except Exception:
                        logger.warning("Failed to delete partially persisted run %s", run_id, exc_info=True)
                raise
            finally:
                # Also covers cancellation, which bypasses ``except Exception``.
                if not persisted:
                    self._runs.pop(run_id, None)
                    self._unindex_run_locked(run_id, record.thread_id)

            if multitask_strategy in ("interrupt", "rollback") and inflight:
                for r in inflight:
                    r.abort_action = multitask_strategy
                    r.abort_event.set()
                    if r.task is not None and not r.task.done():
                        r.task.cancel()
                    r.status = RunStatus.interrupted
                    r.updated_at = now
                    interrupted_records.append(r)

        for interrupted_record in interrupted_records:
            await self._persist_status(interrupted_record, RunStatus.interrupted)
            await self._persist_round_state_for_status(interrupted_record, RunStatus.interrupted)
            self._schedule_terminal_cleanup(interrupted_record.run_id)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def _recover_expired_active_leases(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        error: str,
    ) -> list[RunRecord]:
        if self._store is None:
            return []
        now_dt = datetime.now(UTC)
        try:
            leases = await self._call_store_with_retry(
                "list_expired_active_leases",
                "*",
                lambda: self._store.list_expired_active_leases(now_dt),
            )
        except NotImplementedError:
            return []
        except Exception:
            logger.warning("Failed to list expired active leases for recovery", exc_info=True)
            return []

        recovered: list[RunRecord] = []
        for lease in leases:
            if thread_id is not None and lease.thread_id != thread_id:
                continue
            if run_id is not None and lease.run_id != run_id:
                continue
            if user_id is not None:
                try:
                    row = await self._store.get(lease.run_id, user_id=user_id)
                except Exception:
                    logger.warning("Failed to verify expired lease owner for run %s", lease.run_id, exc_info=True)
                    continue
                if row is None:
                    continue
            try:
                updated = await self._call_store_with_retry(
                    "recover_expired_lease",
                    lease.run_id,
                    lambda: self._store.recover_expired_lease(
                        lease.run_id,
                        generation=lease.generation,
                        terminal_status=RunStatus.error.value,
                        recovery_worker_id=self._worker_id,
                        now=now_dt,
                        error=error,
                    ),
                )
            except NotImplementedError:
                return recovered
            except Exception:
                logger.warning("Failed to recover expired lease for run %s", lease.run_id, exc_info=True)
                continue
            if not updated:
                continue

            try:
                row = await self._store.get(lease.run_id, user_id=user_id)
            except Exception:
                logger.warning("Failed to hydrate recovered lease run %s", lease.run_id, exc_info=True)
                continue
            if row is None:
                continue
            try:
                record = self._record_from_store(row)
            except Exception:
                logger.warning("Failed to map recovered lease run %s", lease.run_id, exc_info=True)
                continue

            async with self._lock:
                live_record = self._runs.get(lease.run_id)
                if live_record is not None:
                    live_record.abort_action = "interrupt"
                    live_record.abort_event.set()
                    if live_record.task is not None and not live_record.task.done():
                        live_record.task.cancel()
                    live_record.status = record.status
                    live_record.error = record.error
                    live_record.terminal_reason = record.terminal_reason
                    live_record.updated_at = record.updated_at
                    record = live_record
            recovered.append(record)

        if recovered:
            logger.warning("Recovered %d expired active lease(s)", len(recovered))
        return recovered

    async def reconcile_orphaned_inflight_runs(
        self,
        *,
        error: str,
        before: str | None = None,
    ) -> list[RunRecord]:
        """Mark persisted active runs as failed when no local task owns them.

        Gateway runs are process-local: the asyncio task and abort event live in
        memory, while the run row is durable.  After a SQLite-backed gateway
        restart, any persisted ``pending`` or ``running`` row created before
        startup cannot still have a local worker.  This recovery step turns that
        ambiguous state into an explicit error instead of letting the UI show an
        indefinite active run.
        """
        if self._store is None:
            return []
        recovered = await self._recover_expired_active_leases(error=error)
        try:
            rows = await self._call_store_with_retry(
                "list_inflight",
                "*",
                lambda: self._store.list_inflight(before=before),
            )
        except Exception:
            logger.warning("Failed to list orphaned inflight runs for reconciliation", exc_info=True)
            return recovered

        now = _now_iso()
        for row in rows:
            try:
                record = self._record_from_store(row)
            except Exception:
                logger.warning("Failed to map orphaned run row during reconciliation", exc_info=True)
                continue

            async with self._lock:
                live_record = self._runs.get(record.run_id)
                if live_record is not None and is_inflight_status(live_record.status):
                    continue

            record.status = RunStatus.error
            record.error = error
            record.updated_at = now
            persisted = await self._persist_status(record, RunStatus.error, error=error)
            if not persisted:
                logger.warning("Skipped orphaned run %s recovery because error status was not persisted", record.run_id)
                continue
            recovered.append(record)

        if recovered:
            logger.warning("Recovered %d orphaned inflight run(s) as error", len(recovered))
        return recovered

    async def recover_stale_inflight_runs(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        before: str | None = None,
        error: str = _STALE_INFLIGHT_ERROR,
    ) -> list[RunRecord]:
        """Mark persisted inflight runs stale when no local worker can own them."""
        if self._store is None:
            return []
        recovered = await self._recover_expired_active_leases(thread_id=thread_id, run_id=run_id, user_id=user_id, error=error)
        cutoff = _parse_run_timestamp(before) or (datetime.now(UTC) - _STALE_INFLIGHT_TIMEOUT)
        try:
            rows = await self._call_store_with_retry(
                "list_inflight",
                "*",
                lambda: self._store.list_inflight(),
            )
        except Exception:
            logger.warning("Failed to list stale inflight runs for recovery", exc_info=True)
            return recovered

        now = _now_iso()
        for row in rows:
            if thread_id is not None and row.get("thread_id") != thread_id:
                continue
            if run_id is not None and row.get("run_id") != run_id:
                continue
            if user_id is not None and row.get("user_id") != user_id:
                continue
            last_update = _parse_run_timestamp(row.get("updated_at")) or _parse_run_timestamp(row.get("created_at"))
            if last_update is None or last_update > cutoff:
                continue
            try:
                record = self._record_from_store(row)
            except Exception:
                logger.warning("Failed to map stale inflight run row during recovery", exc_info=True)
                continue

            async with self._lock:
                live_record = self._runs.get(record.run_id)
                if live_record is not None:
                    if not _record_matches_user_id(live_record, user_id):
                        continue
                    if live_record.task is not None and not live_record.task.done():
                        continue
                    if not is_inflight_status(live_record.status):
                        continue
                    live_record.status = RunStatus.error
                    live_record.error = error
                    live_record.terminal_reason = "worker_lost"
                    live_record.updated_at = now
                    record = live_record
                else:
                    record.status = RunStatus.error
                    record.error = error
                    record.terminal_reason = "worker_lost"
                    record.updated_at = now

            persisted = await self._persist_status(record, RunStatus.error, error=error, terminal_reason="worker_lost")
            if not persisted:
                logger.warning("Skipped stale run %s recovery because error status was not persisted", record.run_id)
                continue
            recovered.append(record)

        if recovered:
            logger.warning("Recovered %d stale inflight run(s) as worker_lost", len(recovered))
        return recovered

    async def has_inflight(self, thread_id: str) -> bool:
        """Return ``True`` if *thread_id* has a pending or running run."""
        async with self._lock:
            return any(is_inflight_status(r.status) for r in self._thread_records_locked(thread_id))

    async def cleanup(self, run_id: str, *, delay: float = 300) -> None:
        """Remove a run record after an optional delay."""
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            record = self._runs.pop(run_id, None)
            if record is not None:
                self._unindex_run_locked(run_id, record.thread_id)
        logger.debug("Run record %s cleaned up", run_id)

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        """Cancel and bounded-await all in-flight runs on process shutdown.

        Chat runs execute in fire-and-forget background ``asyncio`` tasks that
        write checkpoints through a shared checkpointer. On shutdown the
        checkpointer's resources (e.g. the postgres connection pool owned by the
        gateway's ``AsyncExitStack``) are torn down; if a run task is still
        mid-graph at that point, langgraph's
        ``AsyncPregelLoop._checkpointer_put_after_previous`` runs its
        ``finally: await checkpointer.aput(...)`` against the closed pool. Because
        that put runs in a langgraph-internal task (not on ``run_agent``'s call
        stack), the resulting ``psycopg_pool.PoolClosed`` is not catchable by the
        worker and surfaces as an unhandled exception during ``asyncio.run()``
        shutdown (bytedance/deer-flow issue #3373).

        Draining in-flight runs *before* the checkpointer is closed lets each
        run that settles within ``timeout`` flush its final checkpoint while
        resources are still open. Only runs that do **not** settle on their own
        are marked ``interrupted`` — a run that completes (e.g. ``success``)
        during the drain keeps its real terminal status instead of being
        blanket-overwritten. The whole drain, including the trailing status
        persistence, is bounded by ``timeout`` so a run stuck in cleanup (or a
        slow store under DB pressure) cannot hang worker shutdown — the
        precondition for the signal-reentrancy deadlock guarded by
        ``app.gateway.app._SHUTDOWN_HOOK_TIMEOUT_SECONDS``. Runs still active
        after ``timeout`` are logged and may still race teardown.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        async with self._lock:
            inflight = [record for record in self._runs.values() if is_inflight_status(record.status) and record.task is not None and not record.task.done()]
            for record in inflight:
                record.abort_action = "interrupt"
                record.abort_event.set()
                record.task.cancel()  # type: ignore[union-attr]  # filtered above
                # Status is decided AFTER the drain (below), not here: a run that
                # completes on its own during the drain must keep its real status.

        if not inflight:
            return

        tasks = [record.task for record in inflight]
        _, pending = await asyncio.wait(tasks, timeout=timeout)

        # Only mark/persist ``interrupted`` for runs that did not settle on their
        # own (still pending after the timeout, or ended cancelled). A run that
        # finished normally during the drain keeps the status it set for itself.
        to_persist: list[RunRecord] = []
        async with self._lock:
            for record in inflight:
                task = record.task
                if task not in pending and not task.cancelled():
                    # Completed on its own — retrieve any surfaced exception so it
                    # is not reported as "never retrieved", and keep its status.
                    task.exception()  # type: ignore[union-attr]  # done & not cancelled
                    continue
                if is_inflight_status(record.status):
                    record.status = RunStatus.interrupted
                    record.updated_at = _now_iso()
                to_persist.append(record)

        # Bound the trailing status persistence within the remaining budget so a
        # slow store (``_call_store_with_retry`` can back off under DB pressure)
        # cannot push shutdown past ``timeout``.
        if to_persist:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning("Run drain budget exhausted before persisting %d interrupted run(s) on shutdown", len(to_persist))
            else:
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*(self._persist_status(record, RunStatus.interrupted) for record in to_persist), return_exceptions=True),
                        timeout=remaining,
                    )
                except TimeoutError:
                    logger.warning("Run drain status persistence exceeded the %.1fs budget; %d record(s) may not be persisted", timeout, len(to_persist))
                else:
                    # ``_persist_status`` is best-effort: it catches and logs its
                    # own failures, returning ``False``. Inspect the aggregate so a
                    # partial failure is surfaced at shutdown level (with the
                    # run_id) instead of being silently swallowed by the gather.
                    for record, result in zip(to_persist, results):
                        if isinstance(result, Exception):
                            logger.warning("Unexpected error persisting interrupted status for run %s during shutdown: %r", record.run_id, result)
                        elif result is False:
                            logger.warning("Could not persist interrupted status for run %s during shutdown", record.run_id)

        if pending:
            logger.warning("Run drain exceeded %.1fs on shutdown; %d run task(s) still active and may race checkpointer teardown", timeout, len(pending))
        logger.info("Drained %d in-flight run(s) on shutdown (%d settled within %.1fs)", len(inflight), len(inflight) - len(pending), timeout)


class ConflictError(Exception):
    """Raised when multitask_strategy=reject and thread has inflight runs."""


class UnsupportedStrategyError(Exception):
    """Raised when a multitask_strategy value is not yet implemented."""
