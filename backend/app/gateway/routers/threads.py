"""Thread CRUD, state, and history endpoints.

Combines the existing thread-local filesystem cleanup with LangGraph
Platform-compatible thread management backed by the checkpointer.

Channel values returned in state responses are serialized through
:func:`deerflow.runtime.serialization.serialize_channel_values` to
ensure LangChain message objects are converted to JSON-safe dicts
matching the LangGraph Platform wire format expected by the
``useStream`` React hook.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langgraph.checkpoint.base import empty_checkpoint, uuid6
from pydantic import BaseModel, Field, field_validator

from app.gateway.authz import require_permission
from app.gateway.checkpoint_owner import owner_checkpoint_config
from app.gateway.deps import get_checkpointer, get_run_manager
from app.gateway.internal_auth import get_trusted_internal_owner_user_id
from app.gateway.path_utils import get_request_storage_user_id
from app.gateway.utils import sanitize_log_param
from deerflow.config.paths import Paths, get_paths, validate_thread_id
from deerflow.persistence.thread_meta import ThreadMetaConflictError
from deerflow.persistence.thread_meta.base import LEGACY_CLAIM_COMPLETE_METADATA_KEY, LEGACY_CLAIMING_STATUS
from deerflow.runtime import ConflictError, serialize_channel_values_for_api
from deerflow.runtime.runs.schemas import is_inflight_status, run_status_value
from deerflow.runtime.user_context import DEFAULT_USER_ID
from deerflow.utils.cancellation import await_task_through_repeated_cancellation
from deerflow.utils.time import coerce_iso, now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["threads"])


# Metadata keys that the server controls; clients are not allowed to set
# them. Pydantic ``@field_validator("metadata")`` strips them on every
# inbound model below so a malicious client cannot reflect a forged
# owner identity through the API surface. Defense-in-depth — the
# row-level invariant is still ``threads_meta.user_id`` populated from
# the auth contextvar; this list closes the metadata-blob echo gap.
_SERVER_RESERVED_METADATA_KEYS: frozenset[str] = frozenset({"owner_id", "user_id", LEGACY_CLAIM_COMPLETE_METADATA_KEY})
_ACTIVE_THREAD_SEARCH_STATUSES = frozenset({"busy", "pending", "running", "cancelling", "rolling_back"})
_WORKER_LOST_TERMINAL_REASONS = frozenset({"worker_lost", "lease_expired_recovered"})
_DELETE_RUN_DRAIN_TIMEOUT_SECONDS = 5.0
_DELETE_GATE_ACQUIRED_REQUEST_ATTR = "_deerflow_delete_gate_acquired"


def _strip_reserved_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return ``metadata`` with server-controlled keys removed."""
    if not metadata:
        return metadata or {}
    return {k: v for k, v in metadata.items() if k not in _SERVER_RESERVED_METADATA_KEYS}


def _release_delete_gate_on_failure(func):
    """Release only the failed request's delete attempt, keeping its tombstone."""

    @functools.wraps(func)
    async def wrapper(thread_id: str, request: Request):
        try:
            result = await func(thread_id, request)
        except BaseException:
            acquired = bool(
                getattr(
                    request.state,
                    _DELETE_GATE_ACQUIRED_REQUEST_ATTR,
                    False,
                )
            )
            setattr(request.state, _DELETE_GATE_ACQUIRED_REQUEST_ATTR, False)
            if acquired:
                try:
                    await asyncio.shield(get_run_manager(request).end_thread_delete(thread_id))
                except BaseException:
                    logger.exception(
                        "Could not release failed delete attempt for thread %s",
                        sanitize_log_param(thread_id),
                    )
            raise
        setattr(request.state, _DELETE_GATE_ACQUIRED_REQUEST_ATTR, False)
        return result

    return wrapper


async def _sync_recovered_thread_search_statuses(
    rows: list[dict[str, Any]],
    request: Request,
    *,
    user_id: str | None,
) -> list[dict[str, Any]]:
    if not any(row.get("status") in _ACTIVE_THREAD_SEARCH_STATUSES for row in rows):
        return rows

    from app.gateway.deps import get_run_manager, get_thread_store

    run_manager = get_run_manager(request)
    thread_store = get_thread_store(request)
    synced: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") not in _ACTIVE_THREAD_SEARCH_STATUSES:
            synced.append(row)
            continue
        thread_id = row.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            synced.append(row)
            continue
        try:
            latest_runs = await run_manager.list_by_thread(thread_id, user_id=user_id, limit=1)
        except Exception:
            logger.debug("Failed to sync recovered run status for thread %s", sanitize_log_param(thread_id), exc_info=True)
            synced.append(row)
            continue
        latest = latest_runs[0] if latest_runs else None
        terminal_reason = getattr(latest, "terminal_reason", None)
        if latest is None or run_status_value(latest.status) != "error" or terminal_reason not in _WORKER_LOST_TERMINAL_REASONS:
            synced.append(row)
            continue
        try:
            await thread_store.update_status(thread_id, "error", user_id=user_id)
        except Exception:
            logger.debug("Failed to persist recovered thread status for %s", sanitize_log_param(thread_id), exc_info=True)
        synced.append({**row, "status": "error"})
    return synced


def _supports_user_id_keyword(callable_obj: Any) -> bool:
    """Return True when a store method can accept ``user_id=...``."""
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    return "user_id" in parameters or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values())


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class ThreadDeleteResponse(BaseModel):
    """Response model for thread cleanup."""

    success: bool
    message: str


class ThreadResponse(BaseModel):
    """Response model for a single thread."""

    thread_id: str = Field(description="Unique thread identifier")
    status: str = Field(default="idle", description="Thread status: idle, busy, interrupted, error")
    created_at: str = Field(default="", description="ISO timestamp")
    updated_at: str = Field(default="", description="ISO timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Thread metadata")
    values: dict[str, Any] = Field(default_factory=dict, description="Current state channel values")
    interrupts: dict[str, Any] = Field(default_factory=dict, description="Pending interrupts")


class ThreadCreateRequest(BaseModel):
    """Request body for creating a thread."""

    thread_id: str | None = Field(default=None, description="Optional thread ID (auto-generated if omitted)")
    assistant_id: str | None = Field(default=None, description="Associate thread with an assistant")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Initial metadata")

    _strip_reserved = field_validator("metadata")(classmethod(lambda cls, v: _strip_reserved_metadata(v)))

    @field_validator("thread_id")
    @classmethod
    def _validate_thread_id(cls, value: str | None) -> str | None:
        return validate_thread_id(value) if value is not None else None


class ThreadSearchRequest(BaseModel):
    """Request body for searching threads."""

    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata filter (exact match)")
    limit: int = Field(default=100, ge=1, le=1000, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    status: str | None = Field(default=None, description="Filter by thread status")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_filters(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Reject filter entries the SQL backend cannot compile.

        Enforces consistent behaviour across SQL and memory backends.
        See ``deerflow.persistence.json_compat`` for the shared validators.
        """
        if not v:
            return v
        from deerflow.persistence.json_compat import validate_metadata_filter_key, validate_metadata_filter_value

        bad_entries: list[str] = []
        for key, value in v.items():
            if not validate_metadata_filter_key(key):
                bad_entries.append(f"{key!r} (unsafe key)")
            elif not validate_metadata_filter_value(value):
                bad_entries.append(f"{key!r} (unsupported value type {type(value).__name__})")
        if bad_entries:
            raise ValueError(f"Invalid metadata filter entries: {', '.join(bad_entries)}")
        return v


class ThreadStateResponse(BaseModel):
    """Response model for thread state."""

    values: dict[str, Any] = Field(default_factory=dict, description="Current channel values")
    next: list[str] = Field(default_factory=list, description="Next tasks to execute")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Checkpoint metadata")
    checkpoint: dict[str, Any] = Field(default_factory=dict, description="Checkpoint info")
    checkpoint_id: str | None = Field(default=None, description="Current checkpoint ID")
    parent_checkpoint_id: str | None = Field(default=None, description="Parent checkpoint ID")
    created_at: str | None = Field(default=None, description="Checkpoint timestamp")
    tasks: list[dict[str, Any]] = Field(default_factory=list, description="Interrupted task details")


class ThreadPatchRequest(BaseModel):
    """Request body for patching thread metadata."""

    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata to merge")

    _strip_reserved = field_validator("metadata")(classmethod(lambda cls, v: _strip_reserved_metadata(v)))


class ThreadStateUpdateRequest(BaseModel):
    """Request body for updating thread state (human-in-the-loop resume)."""

    values: dict[str, Any] | None = Field(default=None, description="Channel values to merge")
    checkpoint_id: str | None = Field(default=None, description="Checkpoint to branch from")
    checkpoint: dict[str, Any] | None = Field(default=None, description="Full checkpoint object")
    as_node: str | None = Field(default=None, description="Node identity for the update")


class HistoryEntry(BaseModel):
    """Single checkpoint history entry."""

    checkpoint_id: str
    parent_checkpoint_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    next: list[str] = Field(default_factory=list)


class ThreadHistoryRequest(BaseModel):
    """Request body for checkpoint history."""

    limit: int = Field(default=10, ge=1, le=100, description="Maximum entries")
    before: str | None = Field(default=None, description="Cursor for pagination")


class CommandRoomRoundResponse(BaseModel):
    """Latest persisted command-room RoundRecord for a thread."""

    round: dict[str, Any] = Field(description="Latest command-room RoundRecord")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delete_thread_data(thread_id: str, paths: Paths | None = None, *, user_id: str | None = None) -> ThreadDeleteResponse:
    """Delete local persisted filesystem data for a thread."""
    path_manager = paths or get_paths()
    try:
        path_manager.delete_thread_dir(thread_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError:
        # Not critical — thread data may not exist on disk
        logger.debug("No local thread data to delete for %s", sanitize_log_param(thread_id))
        return ThreadDeleteResponse(success=True, message=f"No local data for {thread_id}")
    except Exception as exc:
        logger.exception("Failed to delete thread data for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete local thread data.") from exc

    logger.info("Deleted local thread data for %s", sanitize_log_param(thread_id))
    return ThreadDeleteResponse(success=True, message=f"Deleted local thread data for {thread_id}")


def _derive_thread_status(checkpoint_tuple) -> str:
    """Derive thread status from checkpoint metadata."""
    if checkpoint_tuple is None:
        return "idle"
    pending_writes = getattr(checkpoint_tuple, "pending_writes", None) or []

    # Check for error in pending writes
    for pw in pending_writes:
        if len(pw) >= 2 and pw[1] == "__error__":
            return "error"

    # Check for pending next tasks (indicates interrupt)
    tasks = getattr(checkpoint_tuple, "tasks", None)
    if tasks:
        return "interrupted"

    return "idle"


async def _cleanup_thread_runtime_state(thread_id: str, request: Request, *, user_id: str | None) -> None:
    """Cancel and drain local runs before destructive thread cleanup."""
    from app.gateway.deps import get_run_manager, get_stream_bridge

    try:
        run_manager = get_run_manager(request)
        runs = await run_manager.list_by_thread(thread_id, user_id=user_id, limit=100000)
    except Exception as exc:
        logger.exception("Could not list active runs for %s before delete", sanitize_log_param(thread_id))
        raise HTTPException(status_code=409, detail="Thread runtime could not be stopped") from exc

    local_tasks: set[asyncio.Task] = set()
    unstable = False
    for run in runs:
        if not is_inflight_status(run.status):
            continue
        task = getattr(run, "task", None)
        try:
            cancelled = await run_manager.cancel(run.run_id)
        except Exception:
            logger.exception("Could not cancel run %s before thread delete", sanitize_log_param(run.run_id))
            unstable = True
            continue
        if not cancelled or task is None:
            unstable = True
        elif not task.done():
            local_tasks.add(task)

    if local_tasks:
        _, pending = await asyncio.wait(local_tasks, timeout=_DELETE_RUN_DRAIN_TIMEOUT_SECONDS)
        unstable = unstable or bool(pending)

    try:
        remaining = await run_manager.list_by_thread(thread_id, user_id=user_id, limit=100000)
    except Exception as exc:
        logger.exception("Could not verify stopped runs for %s before delete", sanitize_log_param(thread_id))
        raise HTTPException(status_code=409, detail="Thread runtime could not be stopped") from exc
    unstable = unstable or any(is_inflight_status(run.status) for run in remaining)
    if unstable:
        raise HTTPException(status_code=409, detail="Thread runtime is still active")

    try:
        bridge = get_stream_bridge(request)
    except Exception:
        logger.debug("Could not access stream bridge for %s before delete (not critical)", sanitize_log_param(thread_id))
        return

    for run in runs:
        try:
            await bridge.cleanup(run.run_id)
        except Exception:
            logger.debug("Could not cleanup stream for run %s before thread delete (not critical)", sanitize_log_param(run.run_id))


async def _run_blocking_claim_completion_safe(func, *args):
    """Keep the claim gate closed until a started filesystem mutation ends."""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await await_task_through_repeated_cancellation(task)
        except Exception:
            logger.warning("Filesystem legacy claim failed after cancellation", exc_info=True)
        raise cancelled


async def _claim_legacy_thread_related_data(
    thread_id: str,
    owner_user_id: str,
    request: Request,
    *,
    rollback_new_reservation: bool = False,
) -> None:
    """Reserve and converge every legacy surface on one concrete owner."""
    state = request.app.state
    paths = get_paths()
    # Validate every path component before any repository can commit an owner
    # change. ``thread_dir`` is pure path construction and does not create data.
    paths.thread_dir(thread_id)
    paths.thread_dir(thread_id, user_id=DEFAULT_USER_ID)
    paths.thread_dir(thread_id, user_id=owner_user_id)

    run_manager = get_run_manager(request)
    thread_store = state.thread_store
    repos = [
        getattr(state, "run_store", None),
        getattr(state, "run_event_store", None),
        getattr(state, "feedback_repo", None),
        getattr(state, "artifact_provenance_repo", None),
        getattr(state, "round_state_store", None),
    ]

    await run_manager.begin_thread_delete(thread_id)
    try:
        runs = await run_manager.list_by_thread(
            thread_id,
            user_id=None,
            limit=100_000,
        )
        if any(is_inflight_status(run.status) for run in runs):
            raise HTTPException(
                status_code=409,
                detail="Legacy thread cannot be claimed while a run is active",
            )

        try:
            await _assert_legacy_thread_claimable(
                thread_id,
                owner_user_id,
                request,
                repos=repos,
                paths=paths,
            )
        except BaseException:
            # No repository or filesystem mutation has started yet, so a row
            # inserted only to reserve this attempt can be removed safely.
            if rollback_new_reservation:
                await asyncio.shield(thread_store.delete(thread_id, user_id=owner_user_id))
            raise

        if not await thread_store.claim_legacy_owner(thread_id, owner_user_id):
            raise HTTPException(
                status_code=409,
                detail="Thread ID is already in use",
            )

        for repo in repos:
            claim = getattr(repo, "claim_legacy_by_thread", None)
            if claim is None:
                continue
            await claim(thread_id, owner_user_id)
        await _run_blocking_claim_completion_safe(
            paths.claim_legacy_thread_dirs,
            thread_id,
            owner_user_id,
        )
        if not await thread_store.mark_legacy_claim_complete(
            thread_id,
            owner_user_id,
        ):
            raise HTTPException(
                status_code=409,
                detail="Thread owner changed during legacy claim",
            )
    finally:
        await run_manager.end_thread_delete(thread_id)


def _thread_filesystem_owners(paths: Paths, thread_id: str) -> set[str]:
    """Return user buckets that contain this globally unique thread ID."""
    owners: set[str] = set()
    users_dir = paths.base_dir / "users"
    try:
        entries = list(os.scandir(users_dir))
    except FileNotFoundError:
        return owners
    for entry in entries:
        candidate = Path(entry.path) / "threads" / thread_id
        if not os.path.lexists(candidate):
            continue
        # A symlinked user bucket is never safe to claim, even when its name
        # matches the requested owner.
        owners.add(f"!symlink:{entry.name}" if entry.is_symlink() else entry.name)
    return owners


async def _repository_thread_owners(repository: Any, thread_id: str) -> set[str | None]:
    """Enumerate owners, failing closed only when an older store has data."""
    if repository is None:
        return set()
    list_owners = getattr(repository, "list_owners_by_thread", None)
    if callable(list_owners):
        try:
            return set(await list_owners(thread_id))
        except NotImplementedError:
            pass

    list_by_thread = getattr(repository, "list_by_thread", None)
    if callable(list_by_thread):
        rows = await list_by_thread(thread_id, user_id=None, limit=1)
        if not rows:
            return set()
        raise HTTPException(status_code=409, detail="Legacy thread ownership cannot be verified")

    has_events = getattr(repository, "has_events", None)
    if callable(has_events):
        if not await has_events(thread_id, user_id=None):
            return set()
        raise HTTPException(status_code=409, detail="Legacy thread ownership cannot be verified")

    raise HTTPException(status_code=409, detail="Legacy thread ownership cannot be verified")


async def _assert_legacy_thread_claimable(
    thread_id: str,
    owner_user_id: str,
    request: Request,
    *,
    repos: list[Any] | None = None,
    paths: Paths | None = None,
) -> None:
    """Reject a claim when any persistent surface names another real owner."""
    state = request.app.state
    repositories = repos or [
        getattr(state, "run_store", None),
        getattr(state, "run_event_store", None),
        getattr(state, "feedback_repo", None),
        getattr(state, "artifact_provenance_repo", None),
        getattr(state, "round_state_store", None),
    ]
    allowed_owners = {None, DEFAULT_USER_ID, owner_user_id}
    for repository in repositories:
        owners = await _repository_thread_owners(repository, thread_id)
        if any(owner not in allowed_owners for owner in owners):
            raise HTTPException(status_code=409, detail="Thread ID contains data owned by another user")

    storage_paths = paths or get_paths()
    file_owners = await asyncio.to_thread(_thread_filesystem_owners, storage_paths, thread_id)
    if any(owner not in {DEFAULT_USER_ID, owner_user_id} for owner in file_owners):
        raise HTTPException(status_code=409, detail="Thread ID contains data owned by another user")


async def _write_initial_checkpoint(
    checkpointer: Any,
    *,
    thread_id: str,
    user_id: str,
    metadata: dict[str, Any],
    created_at: str,
    only_if_missing: bool,
) -> None:
    config = owner_checkpoint_config(thread_id, user_id, checkpoint_ns="")
    if only_if_missing:
        get_checkpoint = getattr(checkpointer, "aget_tuple", None)
        if get_checkpoint is None:
            raise RuntimeError("checkpointer does not support aget_tuple")
        if await get_checkpoint(config) is not None:
            return
    checkpoint_metadata = {
        "step": -1,
        "source": "input",
        "writes": None,
        "parents": {},
        **metadata,
        "created_at": created_at,
    }
    await checkpointer.aput(config, empty_checkpoint(), checkpoint_metadata, {})


async def _metadata_less_thread_has_legacy_surfaces(
    thread_id: str,
    owner_user_id: str | None,
    request: Request,
) -> bool:
    """Fail closed before assigning an unowned explicit ID to a caller."""
    checkpointer = get_checkpointer(request)
    list_checkpoints = getattr(checkpointer, "alist", None)
    if callable(list_checkpoints):
        async for _checkpoint in list_checkpoints(
            {"configurable": {"thread_id": thread_id}},
            limit=1,
        ):
            return True
    else:
        # Compatibility for custom savers that predate ``alist``. Built-in
        # savers enumerate every namespace through the branch above.
        get_checkpoint = getattr(checkpointer, "aget_tuple", None)
        if callable(get_checkpoint):
            checkpoint = await get_checkpoint(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": "",
                    }
                }
            )
            if checkpoint is not None:
                return True

    run_store = getattr(request.app.state, "run_store", None)
    list_runs = getattr(run_store, "list_by_thread", None)
    if callable(list_runs):
        rows = await list_runs(
            thread_id,
            user_id=None,
            limit=1,
        )
        if rows:
            return True

    event_store = getattr(request.app.state, "run_event_store", None)
    has_events = getattr(event_store, "has_events", None)
    if callable(has_events):
        if await has_events(thread_id, user_id=None):
            return True
    else:
        # Compatibility for custom/older stores that only expose the message
        # projection. Built-in stores use the all-category existence probe.
        count_messages = getattr(event_store, "count_messages", None)
        if callable(count_messages) and await count_messages(thread_id, user_id=None):
            return True

    for repository_name in (
        "feedback_repo",
        "artifact_provenance_repo",
        "round_state_store",
    ):
        repository = getattr(request.app.state, repository_name, None)
        list_by_thread = getattr(repository, "list_by_thread", None)
        if not callable(list_by_thread):
            continue
        rows = await list_by_thread(
            thread_id,
            user_id=None,
            limit=1,
        )
        if rows:
            return True

    paths = get_paths()

    def has_files() -> bool:
        legacy = paths.thread_dir(thread_id)
        return legacy.exists() or legacy.is_symlink() or bool(_thread_filesystem_owners(paths, thread_id))

    return await asyncio.to_thread(has_files)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.delete("/{thread_id}", response_model=ThreadDeleteResponse)
@require_permission(
    "threads",
    "delete",
    owner_check=True,
    require_existing=True,
    allow_deleting_owner=True,
)
@_release_delete_gate_on_failure
async def delete_thread_data(thread_id: str, request: Request) -> ThreadDeleteResponse:
    """Delete local persisted filesystem data for a thread.

    Cleans DeerFlow-managed thread directories, removes checkpoint data,
    and removes the thread_meta row from the configured ThreadMetaStore
    (sqlite or memory).
    """
    from app.gateway.deps import get_round_state_store, get_run_event_store, get_run_store, get_thread_store

    storage_user_id = get_request_storage_user_id(request)
    thread_store = get_thread_store(request)
    try:
        run_manager = get_run_manager(request)
        await run_manager.begin_thread_delete(thread_id)
        setattr(request.state, _DELETE_GATE_ACQUIRED_REQUEST_ATTR, True)
    except Exception as exc:
        logger.exception(
            "Could not acquire exclusive delete gate for %s",
            sanitize_log_param(thread_id),
        )
        raise HTTPException(
            status_code=409,
            detail="Thread runtime could not be stopped",
        ) from exc
    try:
        await thread_store.update_status(thread_id, "deleting", user_id=storage_user_id)
        deleting_row = await thread_store.get(thread_id, user_id=storage_user_id)
    except Exception as exc:
        logger.exception("Could not establish delete barrier for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=409, detail="Thread delete barrier could not be established") from exc
    if deleting_row is None or deleting_row.get("status") != "deleting":
        raise HTTPException(status_code=409, detail="Thread delete barrier could not be established")

    await _cleanup_thread_runtime_state(thread_id, request, user_id=storage_user_id)

    # Delete only this owner's physical checkpoint key before removing the
    # external owner boundary.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        logger.error("Checkpointer unavailable while deleting thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread checkpoints")
    try:
        delete_checkpoints = getattr(checkpointer, "adelete_thread", None)
        if delete_checkpoints is None:
            raise RuntimeError("checkpointer does not support adelete_thread")
        checkpoint_thread_id = owner_checkpoint_config(
            thread_id,
            storage_user_id,
        )["configurable"]["thread_id"]
        await delete_checkpoints(checkpoint_thread_id)
    except Exception as exc:
        logger.exception("Could not delete checkpoints for thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread checkpoints") from exc

    # Clean local filesystem only after no worker can write another checkpoint.
    response = _delete_thread_data(thread_id, user_id=storage_user_id)

    # Remove owner-scoped persisted data before dropping the owner boundary so
    # recreating the same thread_id cannot surface stale run history.
    try:
        event_store = get_run_event_store(request)
        await event_store.delete_by_thread(thread_id, user_id=storage_user_id)
        delete_legacy = getattr(event_store, "delete_legacy_by_thread", None)
        if delete_legacy is not None:
            await delete_legacy(thread_id)
    except Exception as exc:
        logger.exception("Could not delete run_events for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread run events") from exc

    try:
        run_store = get_run_store(request)
        await run_store.delete_by_thread(thread_id, user_id=storage_user_id)
        delete_legacy = getattr(run_store, "delete_legacy_by_thread", None)
        if delete_legacy is not None:
            await delete_legacy(thread_id)
    except Exception as exc:
        logger.exception("Could not delete runs for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread runs") from exc

    try:
        feedback_repo = getattr(request.app.state, "feedback_repo", None)
        if feedback_repo is not None:
            await feedback_repo.delete_by_thread(thread_id, user_id=storage_user_id)
            delete_legacy = getattr(feedback_repo, "delete_legacy_by_thread", None)
            if delete_legacy is not None:
                await delete_legacy(thread_id)
    except Exception as exc:
        logger.exception("Could not delete feedback for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread feedback") from exc

    try:
        artifact_provenance_repo = getattr(request.app.state, "artifact_provenance_repo", None)
        if artifact_provenance_repo is not None:
            await artifact_provenance_repo.delete_by_thread(thread_id, user_id=storage_user_id)
            delete_legacy = getattr(artifact_provenance_repo, "delete_legacy_by_thread", None)
            if delete_legacy is not None:
                await delete_legacy(thread_id)
    except Exception as exc:
        logger.exception("Could not delete artifact provenance for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread artifact provenance") from exc

    round_store = get_round_state_store(request)
    if round_store is not None:
        try:
            await round_store.delete_by_thread(thread_id, user_id=storage_user_id)
            delete_legacy = getattr(round_store, "delete_legacy_by_thread", None)
            if delete_legacy is not None:
                await delete_legacy(thread_id)
        except Exception as exc:
            logger.exception("Could not delete round state for %s", sanitize_log_param(thread_id))
            raise HTTPException(status_code=500, detail="Failed to delete thread round state") from exc

    # Owner metadata is the final boundary removed. Any earlier failure leaves
    # the deleting tombstone in place, blocking workers and API reads.
    try:
        await thread_store.delete(thread_id, user_id=storage_user_id)
    except Exception as exc:
        logger.exception("Could not delete thread_meta for %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to delete thread metadata") from exc

    return response


@router.post("", response_model=ThreadResponse)
@require_permission("threads", "write")
async def create_thread(body: ThreadCreateRequest, request: Request) -> ThreadResponse:
    """Create a new thread.

    Writes a thread_meta record (so the thread appears in /threads/search)
    and an empty checkpoint (so state endpoints work immediately).
    Idempotent for the current owner: returns the existing record when
    ``thread_id`` already exists for this user.
    """
    from app.gateway.deps import get_thread_store

    checkpointer = get_checkpointer(request)
    thread_store = get_thread_store(request)
    requested_thread_id = body.thread_id
    thread_id = requested_thread_id or str(uuid.uuid4())
    now = now_iso()
    storage_user_id = get_request_storage_user_id(request)
    trusted_owner_user_id = get_trusted_internal_owner_user_id(request)
    thread_owner_kwargs = {"user_id": storage_user_id}
    # ``body.metadata`` is already stripped of server-reserved keys by
    # ``ThreadCreateRequest._strip_reserved`` — see the model definition.

    # Idempotency: return existing record when already present
    existing_record = await thread_store.get(thread_id, **thread_owner_kwargs)
    unscoped_record = None
    if existing_record is None and (requested_thread_id or trusted_owner_user_id):
        unscoped_record = await thread_store.get(thread_id, user_id=None)
    durable_record = existing_record or unscoped_record
    if durable_record is not None and durable_record.get("status") == "deleting":
        raise HTTPException(status_code=409, detail="Thread is being deleted")
    if durable_record is None and requested_thread_id:
        has_legacy_surfaces = await _metadata_less_thread_has_legacy_surfaces(
            thread_id,
            storage_user_id,
            request,
        )
        if has_legacy_surfaces:
            if not trusted_owner_user_id or storage_user_id is None:
                raise HTTPException(
                    status_code=409,
                    detail="Thread ID contains unowned legacy data",
                )
            await _assert_legacy_thread_claimable(
                thread_id,
                storage_user_id,
                request,
            )
            created_reservation = False
            try:
                reservation = await thread_store.create(
                    thread_id,
                    assistant_id=getattr(body, "assistant_id", None),
                    **thread_owner_kwargs,
                    metadata=body.metadata,
                    status=LEGACY_CLAIMING_STATUS,
                )
                created_reservation = getattr(reservation, "created", True)
            except ThreadMetaConflictError:
                # A concurrent trusted request may have installed the same
                # reservation. The owner CAS below remains authoritative.
                pass
            await _claim_legacy_thread_related_data(
                thread_id,
                storage_user_id,
                request,
                rollback_new_reservation=created_reservation,
            )
            existing_record = await thread_store.get(
                thread_id,
                **thread_owner_kwargs,
            )
            unscoped_record = await thread_store.get(thread_id, user_id=None)
            durable_record = existing_record or unscoped_record
    if trusted_owner_user_id and durable_record is not None:
        current_owner = durable_record.get("user_id")
        if current_owner not in (None, DEFAULT_USER_ID, storage_user_id):
            raise HTTPException(status_code=409, detail="Thread ID is already in use")
        if not await thread_store.is_legacy_claim_complete(
            thread_id,
            storage_user_id,
        ):
            await _claim_legacy_thread_related_data(thread_id, storage_user_id, request)
        existing_record = await thread_store.get(thread_id, **thread_owner_kwargs)
    if existing_record is None and unscoped_record is not None:
        if unscoped_record.get("user_id") == storage_user_id:
            existing_record = unscoped_record
        else:
            raise HTTPException(status_code=409, detail="Thread ID is already in use")
    if existing_record is not None:
        run_manager = get_run_manager(request)
        reopening_delete_gate = False
        try:
            await run_manager.begin_thread_write(thread_id)
        except ConflictError as exc:
            current_record = await thread_store.get(thread_id, **thread_owner_kwargs)
            if current_record is None or current_record.get("status") == "deleting":
                raise HTTPException(status_code=409, detail="Thread is being deleted") from exc
            reopening_delete_gate = await run_manager.begin_thread_recreate(thread_id)
        try:
            # A recreate retry may bypass a stale in-memory delete gate left
            # after a successful DELETE. Re-read the durable barrier after the
            # writer is registered so it cannot bypass an active deletion.
            current_record = await thread_store.get(thread_id, **thread_owner_kwargs)
            if current_record is None or current_record.get("status") == "deleting":
                raise HTTPException(status_code=409, detail="Thread is being deleted")
            existing_record = current_record
            try:
                await _write_initial_checkpoint(
                    checkpointer,
                    thread_id=thread_id,
                    user_id=storage_user_id,
                    metadata=existing_record.get("metadata", {}),
                    created_at=coerce_iso(existing_record.get("created_at", now)),
                    only_if_missing=True,
                )
            except Exception as exc:
                logger.exception("Failed to ensure checkpoint for thread %s", sanitize_log_param(thread_id))
                raise HTTPException(status_code=500, detail="Failed to create thread") from exc
        finally:
            await run_manager.end_thread_write(thread_id)
        if reopening_delete_gate:
            await run_manager.end_thread_delete(thread_id)
        return ThreadResponse(
            thread_id=thread_id,
            status=existing_record.get("status", "idle"),
            created_at=coerce_iso(existing_record.get("created_at", "")),
            updated_at=coerce_iso(existing_record.get("updated_at", "")),
            metadata=existing_record.get("metadata", {}),
        )

    run_manager = get_run_manager(request)
    reopening_delete_gate = await run_manager.begin_thread_recreate(thread_id)
    created_metadata = False
    try:
        # Write thread_meta so the thread appears in /threads/search immediately.
        try:
            create_result = await thread_store.create(
                thread_id,
                assistant_id=getattr(body, "assistant_id", None),
                **thread_owner_kwargs,
                metadata=body.metadata,
            )
            created_metadata = getattr(create_result, "created", True)
            if created_metadata and trusted_owner_user_id and storage_user_id is not None:
                await thread_store.mark_legacy_claim_complete(
                    thread_id,
                    storage_user_id,
                )
        except ThreadMetaConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            logger.exception(
                "Failed to write thread_meta for %s",
                sanitize_log_param(thread_id),
            )
            raise HTTPException(status_code=500, detail="Failed to create thread")

        # Write an empty checkpoint so state endpoints work immediately.
        try:
            await _write_initial_checkpoint(
                checkpointer,
                thread_id=thread_id,
                user_id=storage_user_id,
                metadata=create_result.get("metadata", body.metadata),
                created_at=coerce_iso(create_result.get("created_at", now)),
                only_if_missing=not created_metadata,
            )
        except Exception as exc:
            logger.exception(
                "Failed to create checkpoint for thread %s",
                sanitize_log_param(thread_id),
            )
            # Explicit IDs are retryable/idempotent and may already be shared by
            # a concurrent same-owner request whose checkpoint succeeded.
            if created_metadata and requested_thread_id is None:
                try:
                    await thread_store.delete(thread_id, **thread_owner_kwargs)
                except Exception:
                    logger.exception(
                        "Failed to remove partial thread metadata for %s",
                        sanitize_log_param(thread_id),
                    )
            raise HTTPException(status_code=500, detail="Failed to create thread") from exc
    finally:
        await run_manager.end_thread_write(thread_id)

    if reopening_delete_gate:
        await run_manager.end_thread_delete(thread_id)

    logger.info("Thread created: %s", sanitize_log_param(thread_id))
    return ThreadResponse(
        thread_id=thread_id,
        status=create_result.get("status", "idle"),
        created_at=coerce_iso(create_result.get("created_at", now)),
        updated_at=coerce_iso(create_result.get("updated_at", now)),
        metadata=create_result.get("metadata", {}),
    )


@router.post("/search", response_model=list[ThreadResponse])
@require_permission("threads", "read")
async def search_threads(body: ThreadSearchRequest, request: Request) -> list[ThreadResponse]:
    """Search and list threads.

    Delegates to the configured ThreadMetaStore implementation
    (SQL-backed for sqlite/postgres, Store-backed for memory mode).
    """
    from app.gateway.deps import get_thread_store
    from deerflow.persistence.thread_meta import InvalidMetadataFilterError

    repo = get_thread_store(request)
    user_id = get_request_storage_user_id(request)
    try:
        rows = await repo.search(
            metadata=body.metadata or None,
            status=body.status,
            limit=body.limit,
            offset=body.offset,
            user_id=user_id,
        )
    except InvalidMetadataFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = await _sync_recovered_thread_search_statuses(rows, request, user_id=user_id)
    if body.status is not None:
        rows = [row for row in rows if row.get("status", "idle") == body.status]
    return [
        ThreadResponse(
            thread_id=r["thread_id"],
            status=r.get("status", "idle"),
            # ``coerce_iso`` heals legacy unix-second values that
            # ``MemoryThreadMetaStore`` historically wrote with ``time.time()``;
            # SQL-backed rows already arrive as ISO strings and pass through.
            created_at=coerce_iso(r.get("created_at", "")),
            updated_at=coerce_iso(r.get("updated_at", "")),
            metadata=r.get("metadata", {}),
            values={"title": r["display_name"]} if r.get("display_name") else {},
            interrupts={},
        )
        for r in rows
    ]


@router.patch("/{thread_id}", response_model=ThreadResponse)
@require_permission(
    "threads",
    "write",
    owner_check=True,
    require_existing=True,
    thread_write_guard=True,
)
async def patch_thread(thread_id: str, body: ThreadPatchRequest, request: Request) -> ThreadResponse:
    """Merge metadata into a thread record."""
    from app.gateway.deps import get_thread_store

    thread_store = get_thread_store(request)
    storage_user_id = get_request_storage_user_id(request)
    record = await thread_store.get(thread_id, user_id=storage_user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    # ``body.metadata`` already stripped by ``ThreadPatchRequest._strip_reserved``.
    try:
        await thread_store.update_metadata(thread_id, body.metadata, user_id=storage_user_id)
    except Exception:
        logger.exception("Failed to patch thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to update thread")

    # Re-read to get the merged metadata + refreshed updated_at
    record = await thread_store.get(thread_id, user_id=storage_user_id) or record
    return ThreadResponse(
        thread_id=thread_id,
        status=record.get("status", "idle"),
        created_at=coerce_iso(record.get("created_at", "")),
        updated_at=coerce_iso(record.get("updated_at", "")),
        metadata=record.get("metadata", {}),
    )


@router.get("/{thread_id}", response_model=ThreadResponse)
@require_permission("threads", "read", owner_check=True)
async def get_thread(thread_id: str, request: Request) -> ThreadResponse:
    """Get thread info.

    Reads metadata from the ThreadMetaStore and derives the accurate
    execution status from the checkpointer.  Falls back to the checkpointer
    alone for threads that pre-date ThreadMetaStore adoption (backward compat).
    """
    from app.gateway.deps import get_thread_store

    thread_store = get_thread_store(request)
    checkpointer = get_checkpointer(request)

    storage_user_id = get_request_storage_user_id(request)
    record: dict | None = await thread_store.get(thread_id, user_id=storage_user_id)

    # Derive accurate status from the checkpointer
    config = owner_checkpoint_config(thread_id, storage_user_id, checkpoint_ns="")
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
    except Exception:
        logger.exception("Failed to get checkpoint for thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to get thread")

    if record is None and checkpoint_tuple is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    # If the thread exists in the checkpointer but not in thread_meta (e.g.
    # legacy data created before thread_meta adoption), synthesize a minimal
    # record from the checkpoint metadata.
    if record is None and checkpoint_tuple is not None:
        ckpt_meta = getattr(checkpoint_tuple, "metadata", {}) or {}
        record = {
            "thread_id": thread_id,
            "status": "idle",
            "created_at": coerce_iso(ckpt_meta.get("created_at", "")),
            "updated_at": coerce_iso(ckpt_meta.get("updated_at", ckpt_meta.get("created_at", ""))),
            "metadata": {k: v for k, v in ckpt_meta.items() if k not in ("created_at", "updated_at", "step", "source", "writes", "parents")},
        }

    if record is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    status = _derive_thread_status(checkpoint_tuple) if checkpoint_tuple is not None else record.get("status", "idle")
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {} if checkpoint_tuple is not None else {}
    channel_values = checkpoint.get("channel_values", {})

    return ThreadResponse(
        thread_id=thread_id,
        status=status,
        created_at=coerce_iso(record.get("created_at", "")),
        updated_at=coerce_iso(record.get("updated_at", "")),
        metadata=record.get("metadata", {}),
        values=serialize_channel_values_for_api(channel_values),
    )


@router.get("/{thread_id}/command-room/rounds/latest", response_model=CommandRoomRoundResponse)
@require_permission("threads", "read", owner_check=True)
async def get_latest_command_room_round(thread_id: str, request: Request) -> CommandRoomRoundResponse:
    """Return the latest persisted command-room RoundRecord for a thread."""
    from deerflow.command_room.round_record import latest_command_room_round

    try:
        record = await asyncio.to_thread(latest_command_room_round, thread_id=thread_id, user_id=get_request_storage_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to read command-room RoundRecord for thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to read command-room RoundRecord") from exc

    if record is None:
        raise HTTPException(status_code=404, detail=f"Command-room RoundRecord for thread {thread_id} not found")

    return CommandRoomRoundResponse(round=record)


# ---------------------------------------------------------------------------
@router.get("/{thread_id}/state", response_model=ThreadStateResponse)
@require_permission("threads", "read", owner_check=True)
async def get_thread_state(thread_id: str, request: Request) -> ThreadStateResponse:
    """Get the latest state snapshot for a thread.

    Channel values are serialized to ensure LangChain message objects
    are converted to JSON-safe dicts.
    """
    checkpointer = get_checkpointer(request)
    storage_user_id = get_request_storage_user_id(request)

    config = owner_checkpoint_config(thread_id, storage_user_id, checkpoint_ns="")
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
    except Exception:
        logger.exception("Failed to get state for thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to get thread state")

    if checkpoint_tuple is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    metadata = getattr(checkpoint_tuple, "metadata", {}) or {}
    checkpoint_id = None
    ckpt_config = getattr(checkpoint_tuple, "config", {})
    if ckpt_config:
        checkpoint_id = ckpt_config.get("configurable", {}).get("checkpoint_id")

    channel_values = checkpoint.get("channel_values", {})

    parent_config = getattr(checkpoint_tuple, "parent_config", None)
    parent_checkpoint_id = None
    if parent_config:
        parent_checkpoint_id = parent_config.get("configurable", {}).get("checkpoint_id")

    tasks_raw = getattr(checkpoint_tuple, "tasks", []) or []
    next_tasks = [t.name for t in tasks_raw if hasattr(t, "name")]
    tasks = [{"id": getattr(t, "id", ""), "name": getattr(t, "name", "")} for t in tasks_raw]

    values = serialize_channel_values_for_api(channel_values)

    return ThreadStateResponse(
        values=values,
        next=next_tasks,
        metadata=metadata,
        checkpoint={"id": checkpoint_id, "ts": coerce_iso(metadata.get("created_at", ""))},
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=parent_checkpoint_id,
        created_at=coerce_iso(metadata.get("created_at", "")),
        tasks=tasks,
    )


@router.post("/{thread_id}/state", response_model=ThreadStateResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def update_thread_state(thread_id: str, body: ThreadStateUpdateRequest, request: Request) -> ThreadStateResponse:
    """Update thread state (e.g. for human-in-the-loop resume or title rename).

    Writes a new checkpoint that merges *body.values* into the latest
    channel values, then syncs any updated ``title`` field through the
    ThreadMetaStore abstraction so that ``/threads/search`` reflects the
    change immediately in both sqlite and memory backends.
    """
    from app.gateway.deps import get_thread_store

    checkpointer = get_checkpointer(request)
    thread_store = get_thread_store(request)

    # checkpoint_ns must be present in the config for aput — default to ""
    # (the root graph namespace).  checkpoint_id is optional; omitting it
    # fetches the latest checkpoint for the thread.
    storage_user_id = get_request_storage_user_id(request)
    read_config = owner_checkpoint_config(
        thread_id,
        storage_user_id,
        checkpoint_ns="",
        checkpoint_id=body.checkpoint_id,
    )

    try:
        checkpoint_tuple = await checkpointer.aget_tuple(read_config)
    except Exception:
        logger.exception("Failed to get state for thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to get thread state")

    if checkpoint_tuple is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    # Work on mutable copies so we don't accidentally mutate cached objects.
    checkpoint: dict[str, Any] = dict(getattr(checkpoint_tuple, "checkpoint", {}) or {})
    metadata: dict[str, Any] = dict(getattr(checkpoint_tuple, "metadata", {}) or {})
    channel_values: dict[str, Any] = dict(checkpoint.get("channel_values", {}))

    if body.values:
        channel_values.update(body.values)

    checkpoint["channel_values"] = channel_values
    metadata["updated_at"] = now_iso()

    if body.as_node:
        metadata["source"] = "update"
        metadata["step"] = metadata.get("step", 0) + 1
        metadata["writes"] = {body.as_node: body.values}

    # Assign a new checkpoint ID so aput performs an INSERT rather than an
    # in-place REPLACE of the existing row.  Use uuid6 (time-ordered) rather
    # than uuid4 (random) so the new ID is always lexicographically greater
    # than the previous one — LangGraph's checkpointers determine the "latest"
    # checkpoint by max(checkpoint_ids) string order, matching the uuid6 epoch.
    checkpoint["id"] = str(uuid6())

    # aput requires checkpoint_ns in the config — use the same config used for the
    # read (which always includes checkpoint_ns=""). The fresh checkpoint ID is
    # assigned above via checkpoint["id"]; keep checkpoint_id out of the config so
    # the write is keyed by the new checkpoint payload rather than the prior read.
    # All supported savers (InMemorySaver, AsyncSqliteSaver, AsyncPostgresSaver)
    # persist and echo back checkpoint["id"] verbatim — none mint their own — so
    # the new_config below carries the uuid6 we assigned here. (Regression-locked
    # by test_update_thread_state_inserts_new_checkpoint_each_call.)
    write_config = owner_checkpoint_config(
        thread_id,
        storage_user_id,
        checkpoint_ns="",
    )
    run_manager = get_run_manager(request)
    try:
        await run_manager.begin_thread_write(thread_id)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        try:
            new_config = await checkpointer.aput(write_config, checkpoint, metadata, {})
        except Exception:
            logger.exception(
                "Failed to update state for thread %s",
                sanitize_log_param(thread_id),
            )
            raise HTTPException(status_code=500, detail="Failed to update thread state")

        new_checkpoint_id: str | None = None
        if isinstance(new_config, dict):
            new_checkpoint_id = new_config.get("configurable", {}).get("checkpoint_id")

        # Sync title changes through the ThreadMetaStore abstraction so
        # /threads/search reflects them in sqlite and memory backends.
        if thread_store and body.values and "title" in body.values:
            new_title = body.values["title"]
            if new_title:  # Skip empty strings and None
                try:
                    await thread_store.update_display_name(
                        thread_id,
                        new_title,
                        user_id=get_request_storage_user_id(request),
                    )
                except Exception:
                    logger.debug(
                        "Failed to sync title to thread_meta for %s (non-fatal)",
                        sanitize_log_param(thread_id),
                    )
    finally:
        await run_manager.end_thread_write(thread_id)

    return ThreadStateResponse(
        values=serialize_channel_values_for_api(channel_values),
        next=[],
        metadata=metadata,
        checkpoint_id=new_checkpoint_id,
        created_at=coerce_iso(metadata.get("created_at", "")),
    )


@router.post("/{thread_id}/history", response_model=list[HistoryEntry])
@require_permission("threads", "read", owner_check=True)
async def get_thread_history(thread_id: str, body: ThreadHistoryRequest, request: Request) -> list[HistoryEntry]:
    """Get checkpoint history for a thread.

    Messages are read from the checkpointer's channel values (the
    authoritative source) and serialized via
    :func:`~deerflow.runtime.serialization.serialize_channel_values`.
    Only the latest (first) checkpoint carries the ``messages`` key to
    avoid duplicating them across every entry.
    """
    checkpointer = get_checkpointer(request)
    storage_user_id = get_request_storage_user_id(request)

    config: dict[str, Any] = owner_checkpoint_config(
        thread_id,
        storage_user_id,
        checkpoint_ns="",
    )
    before_config: dict[str, Any] | None = None
    if body.before:
        before_config = owner_checkpoint_config(
            thread_id,
            storage_user_id,
            checkpoint_ns="",
            checkpoint_id=body.before,
        )

    entries: list[HistoryEntry] = []
    is_latest_checkpoint = True
    try:
        list_kwargs: dict[str, Any] = {"limit": body.limit}
        if before_config is not None:
            list_kwargs["before"] = before_config
        async for checkpoint_tuple in checkpointer.alist(config, **list_kwargs):
            ckpt_config = getattr(checkpoint_tuple, "config", {})
            parent_config = getattr(checkpoint_tuple, "parent_config", None)
            metadata = getattr(checkpoint_tuple, "metadata", {}) or {}
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}

            checkpoint_id = ckpt_config.get("configurable", {}).get("checkpoint_id", "")
            parent_id = None
            if parent_config:
                parent_id = parent_config.get("configurable", {}).get("checkpoint_id")

            channel_values = checkpoint.get("channel_values", {})

            # Build values from checkpoint channel_values
            values: dict[str, Any] = {}
            if title := channel_values.get("title"):
                values["title"] = title
            if thread_data := channel_values.get("thread_data"):
                values["thread_data"] = thread_data

            # Attach messages only to the latest checkpoint entry.
            if is_latest_checkpoint:
                messages = channel_values.get("messages")
                if messages:
                    serialized_msgs = serialize_channel_values_for_api({"messages": messages}).get("messages", [])
                    try:
                        from app.gateway.deps import get_run_event_store, get_run_manager
                        from app.gateway.routers.thread_runs import compute_run_durations

                        run_mgr = get_run_manager(request)
                        event_store = get_run_event_store(request)
                        user_id = get_request_storage_user_id(request)

                        runs = await run_mgr.list_by_thread(thread_id, user_id=user_id)

                        # FIXME: Fetching limit=1000 silently drops durations for messages older than the cap on long threads.
                        # We do this full fetch because raw LangGraph messages lack a native run_id link.

                        list_messages_kwargs: dict[str, Any] = {"limit": 1000}
                        if _supports_user_id_keyword(event_store.list_messages):
                            list_messages_kwargs["user_id"] = user_id
                        events = await event_store.list_messages(thread_id, **list_messages_kwargs)

                        if runs and serialized_msgs:
                            # 1. Map each run_id to its actual duration
                            run_durations = compute_run_durations(runs)

                            # 2. Map every message id directly to its parent run_id
                            msg_to_run = {}
                            for e in events:
                                content = e.get("content", {})
                                if isinstance(content, dict) and content.get("type") == "ai" and "id" in content:
                                    msg_to_run[content["id"]] = e["run_id"]

                            # 3. Inject the exact correct duration into each AI message
                            for msg in serialized_msgs:
                                if msg.get("type") == "ai":
                                    msg_id = msg.get("id")
                                    run_id = msg_to_run.get(msg_id)
                                    if run_id and run_id in run_durations:
                                        if "additional_kwargs" not in msg:
                                            msg["additional_kwargs"] = {}
                                        msg["additional_kwargs"]["turn_duration"] = run_durations[run_id]

                    except Exception:
                        logger.warning("Failed to inject turn_duration for thread %s", thread_id, exc_info=True)

                    values["messages"] = serialized_msgs

            is_latest_checkpoint = False

            # Derive next tasks
            tasks_raw = getattr(checkpoint_tuple, "tasks", []) or []
            next_tasks = [t.name for t in tasks_raw if hasattr(t, "name")]

            # Strip LangGraph internal keys from metadata
            user_meta = {k: v for k, v in metadata.items() if k not in ("created_at", "updated_at", "step", "source", "writes", "parents")}
            # Keep step for ordering context
            if "step" in metadata:
                user_meta["step"] = metadata["step"]

            entries.append(
                HistoryEntry(
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=parent_id,
                    metadata=user_meta,
                    values=values,
                    created_at=coerce_iso(metadata.get("created_at", "")),
                    next=next_tasks,
                )
            )
    except Exception:
        logger.exception("Failed to get history for thread %s", sanitize_log_param(thread_id))
        raise HTTPException(status_code=500, detail="Failed to get thread history")

    return entries
