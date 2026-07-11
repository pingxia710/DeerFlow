import asyncio
import re
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.gateway.auth.models import User
from app.gateway.authz import AuthContext, Permissions
from app.gateway.routers import thread_runs, threads
from deerflow.config.paths import Paths
from deerflow.persistence.thread_meta import InvalidMetadataFilterError, ThreadMetaCreateResult
from deerflow.persistence.thread_meta.memory import THREADS_NS, MemoryThreadMetaStore
from deerflow.runtime import DisconnectMode, RunManager, RunRecord, RunStatus

_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_HISTORY_USER_ID = UUID("22222222-2222-2222-2222-222222222222")


def _make_user(user_id: UUID = _HISTORY_USER_ID) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role="user")


class _PermissiveThreadMetaStore(MemoryThreadMetaStore):
    """Memory store that skips user-id filtering for router tests.

    Owner isolation is exercised separately in
    ``test_memory_thread_meta_isolation.py``. Router tests need to drive
    the FastAPI surface end-to-end with a single fixed app user, but the
    stub auth middleware in ``_router_auth_helpers`` stamps a fresh UUID
    on every request, so the production filtering would reject every
    pre-seeded record. Bypass that filter so the test can focus on the
    timestamp wire format.
    """

    async def _get_owned_record(self, thread_id, user_id, method_name):  # type: ignore[override]
        item = await self._store.aget(THREADS_NS, thread_id)
        return dict(item.value) if item is not None else None

    async def check_access(self, thread_id, user_id, *, require_existing=False):  # type: ignore[override]
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return not require_existing
        return True

    async def create(self, thread_id, *, assistant_id=None, user_id=None, display_name=None, metadata=None):  # type: ignore[override]
        return await super().create(thread_id, assistant_id=assistant_id, user_id=None, display_name=display_name, metadata=metadata)

    async def search(self, *, metadata=None, status=None, limit=100, offset=0, user_id=None):  # type: ignore[override]
        return await super().search(metadata=metadata, status=status, limit=limit, offset=offset, user_id=None)


def _build_thread_app(*, user_factory=None) -> tuple[FastAPI, InMemoryStore, InMemorySaver]:
    """Build a stub-authed FastAPI app wired with an in-memory ThreadMetaStore.

    The thread_store on ``app.state`` is a permissive subclass of
    ``MemoryThreadMetaStore`` so tests can drive ``/api/threads``
    end-to-end and pre-seed legacy records via the underlying BaseStore.

    Returns ``(app, store, checkpointer)`` for direct seeding/inspection.
    """
    app = make_authed_test_app(user_factory=user_factory)
    store = InMemoryStore()
    checkpointer = InMemorySaver()
    app.state.store = store
    app.state.checkpointer = checkpointer
    app.state.thread_store = _PermissiveThreadMetaStore(store)
    app.state.run_manager = RunManager()
    app.include_router(threads.router)
    return app, store, checkpointer


def test_delete_thread_data_removes_thread_directory(tmp_path):
    paths = Paths(tmp_path)
    thread_dir = paths.thread_dir("thread-cleanup")
    workspace = paths.sandbox_work_dir("thread-cleanup")
    uploads = paths.sandbox_uploads_dir("thread-cleanup")
    outputs = paths.sandbox_outputs_dir("thread-cleanup")

    for directory in [workspace, uploads, outputs]:
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    (uploads / "report.pdf").write_bytes(b"pdf")
    (outputs / "result.json").write_text("{}", encoding="utf-8")

    assert thread_dir.exists()

    response = threads._delete_thread_data("thread-cleanup", paths=paths)

    assert response.success is True
    assert not thread_dir.exists()


def test_delete_thread_data_is_idempotent_for_missing_directory(tmp_path):
    paths = Paths(tmp_path)

    response = threads._delete_thread_data("missing-thread", paths=paths)

    assert response.success is True
    assert not paths.thread_dir("missing-thread").exists()


def test_delete_thread_data_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        threads._delete_thread_data("../escape", paths=paths)

    assert exc_info.value.status_code == 422
    assert "Invalid thread_id" in exc_info.value.detail


def test_delete_thread_route_cleans_thread_directory(tmp_path):
    paths = Paths(tmp_path)
    user_id = str(_HISTORY_USER_ID)
    thread_dir = paths.thread_dir("thread-route", user_id=user_id)
    paths.sandbox_work_dir("thread-route", user_id=user_id).mkdir(parents=True, exist_ok=True)
    (paths.sandbox_work_dir("thread-route", user_id=user_id) / "notes.txt").write_text("hello", encoding="utf-8")

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    asyncio.run(thread_store.create("thread-route", user_id=user_id))
    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    app.state.thread_store = thread_store
    app.state.run_manager = SimpleNamespace(
        begin_thread_delete=AsyncMock(),
        list_by_thread=AsyncMock(return_value=[]),
        cancel=AsyncMock(),
    )
    app.state.stream_bridge = SimpleNamespace(cleanup=AsyncMock())
    app.state.checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    app.state.run_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=0))
    app.state.run_event_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=0))
    app.state.feedback_repo = SimpleNamespace(delete_by_thread=AsyncMock(return_value=0))
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/thread-route")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Deleted local thread data for thread-route"}
    assert not thread_dir.exists()


def test_delete_thread_route_uses_trusted_internal_owner_bucket(tmp_path):
    import asyncio

    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

    paths = Paths(tmp_path)
    thread_id = "thread-route-internal"
    owner_user_id = "owner-delete"

    owner_dir = paths.thread_dir(thread_id, user_id=owner_user_id)
    default_dir = paths.thread_dir(thread_id, user_id="default")
    paths.sandbox_work_dir(thread_id, user_id=owner_user_id).mkdir(parents=True, exist_ok=True)
    paths.sandbox_work_dir(thread_id, user_id="default").mkdir(parents=True, exist_ok=True)

    store = InMemoryStore()
    thread_store = MemoryThreadMetaStore(store)
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: owner_user_id},
        state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE), auth_source="internal"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
                run_manager=SimpleNamespace(
                    begin_thread_delete=AsyncMock(),
                    list_by_thread=AsyncMock(return_value=[]),
                    cancel=AsyncMock(),
                ),
                stream_bridge=SimpleNamespace(cleanup=AsyncMock()),
                run_store=SimpleNamespace(delete_by_thread=AsyncMock(return_value=0)),
                run_event_store=SimpleNamespace(delete_by_thread=AsyncMock(return_value=0)),
                feedback_repo=SimpleNamespace(delete_by_thread=AsyncMock(return_value=0)),
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        ),
    )

    async def _scenario():
        await thread_store.create(thread_id, user_id=owner_user_id)
        with patch("app.gateway.routers.threads.get_paths", return_value=paths):
            response = await threads.delete_thread_data(thread_id=thread_id, request=request)
        deleted_owner_row = await thread_store.get(thread_id, user_id=owner_user_id)
        return response, deleted_owner_row

    response, deleted_owner_row = asyncio.run(_scenario())

    assert response.success is True
    assert not owner_dir.exists()
    assert default_dir.exists()
    assert deleted_owner_row is None


def test_delete_thread_route_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)

    app = make_authed_test_app()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/../escape")

    assert response.status_code == 404


def test_delete_thread_route_returns_422_for_route_safe_invalid_id(tmp_path):
    paths = Paths(tmp_path)

    user = _make_user()
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    asyncio.run(thread_store.create("thread.with.dot", user_id=str(user.id)))
    app = make_authed_test_app(user_factory=lambda: user)
    app.state.thread_store = thread_store
    app.state.run_manager = SimpleNamespace(
        begin_thread_delete=AsyncMock(),
        list_by_thread=AsyncMock(return_value=[]),
        cancel=AsyncMock(),
    )
    app.state.stream_bridge = SimpleNamespace(cleanup=AsyncMock())
    app.state.checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/thread.with.dot")

    assert response.status_code == 422
    assert "Invalid thread_id" in response.json()["detail"]


def test_delete_thread_data_returns_generic_500_error(tmp_path):
    paths = Paths(tmp_path)

    with (
        patch.object(paths, "delete_thread_dir", side_effect=OSError("/secret/path")),
        patch.object(threads.logger, "exception") as log_exception,
    ):
        with pytest.raises(HTTPException) as exc_info:
            threads._delete_thread_data("thread-cleanup", paths=paths)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to delete local thread data."
    assert "/secret/path" not in exc_info.value.detail
    log_exception.assert_called_once_with("Failed to delete thread data for %s", "thread-cleanup")


@pytest.mark.parametrize("owner_kind", ["missing", "null"])
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/api/threads/legacy-checkpoint", None),
        ("get", "/api/threads/legacy-checkpoint/state", None),
        ("post", "/api/threads/legacy-checkpoint/history", {"limit": 10}),
    ],
)
def test_checkpointer_reads_require_explicit_thread_owner(owner_kind, method, path, json):
    store = InMemoryStore()
    thread_store = MemoryThreadMetaStore(store)
    checkpointer = InMemorySaver()
    config = {"configurable": {"thread_id": "legacy-checkpoint", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"title": "secret"}
    asyncio.run(
        checkpointer.aput(
            config,
            checkpoint,
            {"created_at": "2026-01-01T00:00:00+00:00", "step": 0},
            {},
        )
    )
    if owner_kind == "null":
        asyncio.run(thread_store.create("legacy-checkpoint", user_id=None))

    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    app.state.thread_store = thread_store
    app.state.checkpointer = checkpointer
    app.include_router(threads.router)

    with TestClient(app) as client:
        response = getattr(client, method)(path, json=json) if json is not None else getattr(client, method)(path)

    assert response.status_code == 404


def test_explicit_thread_create_cannot_claim_metadata_less_legacy_checkpoint():
    thread_id = "legacy-create-takeover"
    store = InMemoryStore()
    thread_store = MemoryThreadMetaStore(store)
    checkpointer = InMemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "messages": [{"type": "human", "content": "legacy-secret"}],
    }
    asyncio.run(
        checkpointer.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            checkpoint,
            {"created_at": "2026-01-01T00:00:00+00:00", "step": 0},
            {},
        )
    )

    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    app.state.thread_store = thread_store
    app.state.checkpointer = checkpointer
    app.state.run_manager = RunManager()
    app.include_router(threads.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads",
            json={"thread_id": thread_id, "metadata": {}},
        )

    assert response.status_code == 409
    assert asyncio.run(thread_store.get(thread_id, user_id=None)) is None


def test_explicit_thread_create_cannot_claim_metadata_less_nonroot_checkpoint():
    thread_id = "legacy-nonroot-takeover"
    store = InMemoryStore()
    thread_store = MemoryThreadMetaStore(store)
    checkpointer = InMemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"]["messages"] = [
        {"type": "human", "content": "hidden legacy secret"},
    ]
    checkpoint["channel_versions"]["messages"] = "1"
    asyncio.run(
        checkpointer.aput(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": "hidden-subgraph",
                }
            },
            checkpoint,
            {"created_at": "2026-01-01T00:00:00+00:00", "step": 0},
            {"messages": "1"},
        )
    )

    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    app.state.thread_store = thread_store
    app.state.checkpointer = checkpointer
    app.state.run_manager = RunManager()
    app.include_router(threads.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads",
            json={"thread_id": thread_id, "metadata": {}},
        )

    assert response.status_code == 409
    assert asyncio.run(thread_store.get(thread_id, user_id=None)) is None


def test_thread_history_lists_only_root_checkpoint_namespace():
    thread_id = "root-history-only"
    owner_id = str(_HISTORY_USER_ID)
    store = InMemoryStore()
    thread_store = MemoryThreadMetaStore(store)
    checkpointer = InMemorySaver()
    asyncio.run(thread_store.create(thread_id, user_id=owner_id))

    for namespace, content in (("", "root message"), ("hidden-subgraph", "hidden secret")):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [
            {"type": "human", "content": content},
        ]
        checkpoint["channel_versions"]["messages"] = "1"
        asyncio.run(
            checkpointer.aput(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": namespace,
                    }
                },
                checkpoint,
                {"created_at": "2026-01-01T00:00:00+00:00", "step": 0},
                {"messages": "1"},
            )
        )

    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    app.state.thread_store = thread_store
    app.state.checkpointer = checkpointer
    app.state.run_manager = RunManager()
    app.include_router(threads.router)

    with TestClient(app) as client:
        response = client.post(
            f"/api/threads/{thread_id}/history",
            json={"limit": 10},
        )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert [message["content"] for entry in response.json() for message in entry["values"].get("messages", [])] == ["root message"]


def test_thread_history_before_returns_strictly_older_page():
    app, _store, checkpointer = _build_thread_app()
    thread_id = "history-before-cursor"
    checkpoint_ids = ["0001", "0002", "0003"]

    async def _seed() -> None:
        await app.state.thread_store.create(thread_id)
        for checkpoint_id in checkpoint_ids:
            checkpoint = empty_checkpoint()
            checkpoint["id"] = checkpoint_id
            await checkpointer.aput(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                checkpoint,
                {"created_at": f"2026-01-01T00:00:0{checkpoint_id[-1]}+00:00"},
                {},
            )

    asyncio.run(_seed())

    alist_calls = []
    original_alist = checkpointer.alist

    async def recording_alist(config, *, filter=None, before=None, limit=None):
        alist_calls.append({"config": config, "before": before, "limit": limit})
        async for item in original_alist(config, filter=filter, before=before, limit=limit):
            yield item

    checkpointer.alist = recording_alist

    with TestClient(app) as client:
        first_page = client.post(
            f"/api/threads/{thread_id}/history",
            json={"limit": 1},
        )
        cursor = first_page.json()[0]["checkpoint_id"]
        second_page = client.post(
            f"/api/threads/{thread_id}/history",
            json={"limit": 1, "before": cursor},
        )

    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    assert cursor == "0003"
    assert [entry["checkpoint_id"] for entry in second_page.json()] == ["0002"]
    assert alist_calls[1]["config"]["configurable"].get("checkpoint_id") is None
    assert alist_calls[1]["before"]["configurable"]["checkpoint_id"] == cursor


@pytest.mark.parametrize(
    ("repo_name", "probe_name", "probe_result", "probe_kwargs"),
    [
        ("run_event_store", "has_events", True, {"user_id": None}),
        ("feedback_repo", "list_by_thread", [{"id": "feedback"}], {"user_id": None, "limit": 1}),
        ("artifact_provenance_repo", "list_by_thread", [{"id": "artifact"}], {"user_id": None, "limit": 1}),
        ("round_state_store", "list_by_thread", [{"id": "round"}], {"user_id": None, "limit": 1}),
    ],
)
@pytest.mark.asyncio
async def test_metadata_less_thread_detects_every_legacy_repository_surface(
    tmp_path,
    repo_name,
    probe_name,
    probe_result,
    probe_kwargs,
):
    probe = AsyncMock(return_value=probe_result)
    repository = SimpleNamespace(**{probe_name: probe})
    state = SimpleNamespace(
        checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=None)),
        run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[])),
        run_event_store=None,
        feedback_repo=None,
        artifact_provenance_repo=None,
        round_state_store=None,
    )
    setattr(state, repo_name, repository)
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        found = await threads._metadata_less_thread_has_legacy_surfaces(
            "legacy-repository-only",
            "owner-a",
            request,
        )

    assert found is True
    probe.assert_awaited_once_with("legacy-repository-only", **probe_kwargs)


@pytest.mark.parametrize("category", ["trace", "lifecycle"])
@pytest.mark.asyncio
async def test_metadata_less_thread_detects_ownerless_non_message_event(
    tmp_path,
    category,
):
    from deerflow.runtime.events.store.memory import MemoryRunEventStore

    event_store = MemoryRunEventStore()
    await event_store.put(
        thread_id="legacy-event-only",
        run_id="legacy-run",
        event_type="run_start" if category == "lifecycle" else "llm_end",
        category=category,
        user_id=None,
    )
    state = SimpleNamespace(
        checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=None)),
        run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[])),
        run_event_store=event_store,
        feedback_repo=None,
        artifact_provenance_repo=None,
        round_state_store=None,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        found = await threads._metadata_less_thread_has_legacy_surfaces(
            "legacy-event-only",
            "owner-a",
            request,
        )

    assert found is True
    assert await event_store.count_messages("legacy-event-only", user_id=None) == 0


@pytest.mark.asyncio
async def test_metadata_less_thread_event_probe_keeps_count_messages_fallback(
    tmp_path,
):
    count_messages = AsyncMock(return_value=1)
    state = SimpleNamespace(
        checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=None)),
        run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[])),
        run_event_store=SimpleNamespace(count_messages=count_messages),
        feedback_repo=None,
        artifact_provenance_repo=None,
        round_state_store=None,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        found = await threads._metadata_less_thread_has_legacy_surfaces(
            "legacy-message-only",
            "owner-a",
            request,
        )

    assert found is True
    count_messages.assert_awaited_once_with("legacy-message-only", user_id=None)


@pytest.mark.asyncio
async def test_trusted_internal_create_rejects_metadata_less_foreign_owner_run(
    tmp_path,
):
    from app.gateway.internal_auth import (
        INTERNAL_OWNER_USER_ID_HEADER_NAME,
        INTERNAL_SYSTEM_ROLE,
    )
    from deerflow.runtime.runs.store.memory import MemoryRunStore

    thread_id = "foreign-owned-run"
    run_store = MemoryRunStore()
    await run_store.put(
        "foreign-run",
        thread_id=thread_id,
        status="success",
        user_id="owner-a",
    )
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    state = SimpleNamespace(
        checkpointer=InMemorySaver(),
        thread_store=thread_store,
        run_store=run_store,
        run_event_store=None,
        feedback_repo=None,
        artifact_provenance_repo=None,
        round_state_store=None,
        run_manager=RunManager(),
    )
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-b"},
        state=SimpleNamespace(
            user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE),
        ),
        app=SimpleNamespace(state=state),
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id=thread_id),
            request,
        )

    assert exc_info.value.status_code == 409
    assert await thread_store.get(thread_id, user_id=None) is None


@pytest.mark.asyncio
async def test_second_owner_preflight_rolls_back_new_claim_reservation(tmp_path):
    from app.gateway.internal_auth import (
        INTERNAL_OWNER_USER_ID_HEADER_NAME,
        INTERNAL_SYSTEM_ROLE,
    )

    thread_id = "owner-race-during-claim"
    repository = SimpleNamespace(
        list_owners_by_thread=AsyncMock(side_effect=[{"default"}, {"foreign-owner"}]),
        claim_legacy_by_thread=AsyncMock(),
    )
    checkpointer = InMemorySaver()
    await checkpointer.aput(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        empty_checkpoint(),
        {"step": -1, "source": "input", "writes": None},
        {},
    )
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    state = SimpleNamespace(
        checkpointer=checkpointer,
        thread_store=thread_store,
        run_store=repository,
        run_event_store=None,
        feedback_repo=None,
        artifact_provenance_repo=None,
        round_state_store=None,
        run_manager=RunManager(),
    )
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-a"},
        state=SimpleNamespace(
            user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE),
        ),
        app=SimpleNamespace(state=state),
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id=thread_id),
            request,
        )

    assert exc_info.value.status_code == 409
    assert await thread_store.get(thread_id, user_id=None) is None
    repository.claim_legacy_by_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_less_thread_detects_foreign_user_filesystem_bucket(tmp_path):
    paths = Paths(tmp_path)
    foreign_thread = paths.thread_dir("foreign-files", user_id="owner-a")
    foreign_thread.mkdir(parents=True)
    state = SimpleNamespace(
        checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=None)),
        run_store=None,
        run_event_store=None,
        feedback_repo=None,
        artifact_provenance_repo=None,
        round_state_store=None,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        found = await threads._metadata_less_thread_has_legacy_surfaces(
            "foreign-files",
            "owner-b",
            request,
        )

    assert found is True


@pytest.mark.asyncio
async def test_trusted_internal_create_converges_metadata_less_legacy_thread(
    tmp_path,
):
    from app.gateway.internal_auth import (
        INTERNAL_OWNER_USER_ID_HEADER_NAME,
        INTERNAL_SYSTEM_ROLE,
    )
    from deerflow.runtime.runs.store.memory import MemoryRunStore
    from deerflow.runtime.user_context import DEFAULT_USER_ID

    thread_id = "legacy-internal-create"
    owner_id = "owner-a"
    paths = Paths(tmp_path)
    legacy_dir = paths.thread_dir(thread_id)
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy.txt").write_text("legacy", encoding="utf-8")

    checkpointer = InMemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"title": "keep legacy checkpoint"}
    await checkpointer.aput(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        checkpoint,
        {"created_at": "2026-01-01T00:00:00+00:00", "step": 0},
        {},
    )
    before_checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    run_store = MemoryRunStore()
    await run_store.put(
        "legacy-run",
        thread_id=thread_id,
        status="success",
        user_id=DEFAULT_USER_ID,
    )
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: owner_id},
        state=SimpleNamespace(
            user=SimpleNamespace(id=DEFAULT_USER_ID, system_role=INTERNAL_SYSTEM_ROLE),
            auth_source="internal",
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=RunManager(),
                run_store=run_store,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        ),
    )

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        response = await call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id=thread_id),
            request,
        )

    assert response.thread_id == thread_id
    assert await thread_store.is_legacy_claim_complete(thread_id, owner_id)
    assert await run_store.get("legacy-run", user_id=owner_id) is not None
    assert (paths.thread_dir(thread_id, user_id=owner_id) / "legacy.txt").read_text(
        encoding="utf-8",
    ) == "legacy"
    current = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    assert current is not None and before_checkpoint is not None
    assert current.checkpoint["id"] == before_checkpoint.checkpoint["id"]


# ── Server-reserved metadata key stripping ──────────────────────────────────


def test_strip_reserved_metadata_removes_user_id():
    """Client-supplied user_id is dropped to prevent reflection attacks."""
    out = threads._strip_reserved_metadata({"user_id": "victim-id", "title": "ok"})
    assert out == {"title": "ok"}


def test_strip_reserved_metadata_passes_through_safe_keys():
    """Non-reserved keys are preserved verbatim."""
    md = {"title": "ok", "tags": ["a", "b"], "custom": {"x": 1}}
    assert threads._strip_reserved_metadata(md) == md


def test_strip_reserved_metadata_empty_input():
    """Empty / None metadata returns same object — no crash."""
    assert threads._strip_reserved_metadata({}) == {}


def test_strip_reserved_metadata_strips_all_reserved_keys():
    out = threads._strip_reserved_metadata({"user_id": "x", "keep": "me"})
    assert out == {"keep": "me"}


def test_client_metadata_cannot_set_legacy_claim_completion_marker():
    from deerflow.persistence.thread_meta.base import (
        LEGACY_CLAIM_COMPLETE_METADATA_KEY,
    )

    create = threads.ThreadCreateRequest(
        metadata={LEGACY_CLAIM_COMPLETE_METADATA_KEY: "attacker", "keep": "yes"},
    )
    patch = threads.ThreadPatchRequest(
        metadata={LEGACY_CLAIM_COMPLETE_METADATA_KEY: "attacker", "keep": "yes"},
    )

    assert create.metadata == {"keep": "yes"}
    assert patch.metadata == {"keep": "yes"}


@pytest.mark.parametrize(
    "thread_id",
    ["bad.thread", "../escape", "space id", "trailing-newline\n", "", "t" * 256],
)
def test_thread_create_request_rejects_ids_outside_runtime_path_contract(
    thread_id: str,
) -> None:
    with pytest.raises(ValueError):
        threads.ThreadCreateRequest(thread_id=thread_id)


def test_paths_reject_thread_id_with_trailing_newline(tmp_path) -> None:
    with pytest.raises(ValueError, match="Invalid thread_id"):
        Paths(tmp_path).thread_dir("looks-safe\n")


# ---------------------------------------------------------------------------
# ISO 8601 timestamp contract (issue #2594)
# ---------------------------------------------------------------------------
#
# Threads endpoints document ``created_at`` / ``updated_at`` as ISO
# timestamps and that is the format LangGraph Platform uses
# (``langgraph_sdk.schema.Thread.created_at: datetime`` JSON-encodes to
# ISO 8601). The tests below pin that contract end-to-end and also
# exercise the ``coerce_iso`` healing path for legacy unix-timestamp
# records written by older Gateway versions.


def test_create_and_search_threads_fail_closed_without_auth_middleware() -> None:
    app = FastAPI()
    store = InMemoryStore()
    checkpointer = InMemorySaver()
    app.state.store = store
    app.state.checkpointer = checkpointer
    app.state.thread_store = MemoryThreadMetaStore(store)
    app.include_router(threads.router)

    with TestClient(app) as client:
        create_response = client.post("/api/threads", json={"metadata": {}})
        search_response = client.post("/api/threads/search", json={"metadata": {}})

    assert create_response.status_code == 401
    assert search_response.status_code == 401


def test_create_and_search_threads_use_request_auth_user_without_contextvar() -> None:
    import asyncio

    from app.gateway.authz import AuthContext, Permissions

    class RecordingThreadStore:
        def __init__(self) -> None:
            self.get_calls = []
            self.create_calls = []
            self.search_calls = []

        async def get(self, thread_id, **kwargs):
            self.get_calls.append({"thread_id": thread_id, **kwargs})
            return None

        async def create(self, thread_id, **kwargs):
            self.create_calls.append({"thread_id": thread_id, **kwargs})
            return {}

        async def search(self, **kwargs):
            self.search_calls.append(kwargs)
            return []

    class RecordingCheckpointer:
        async def aput(self, *args, **kwargs):
            return None

    user_id = _HISTORY_USER_ID
    thread_store = RecordingThreadStore()
    run_manager = SimpleNamespace(
        begin_thread_recreate=AsyncMock(return_value=False),
        end_thread_write=AsyncMock(),
        end_thread_delete=AsyncMock(),
    )
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(
            user=SimpleNamespace(id="fallback-user"),
            auth=AuthContext(
                user=_make_user(user_id),
                permissions=[Permissions.THREADS_WRITE, Permissions.THREADS_READ],
            ),
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=RecordingCheckpointer(),
                thread_store=thread_store,
                run_manager=run_manager,
            )
        ),
    )

    async def _scenario():
        await threads.create_thread(
            threads.ThreadCreateRequest(thread_id="auth-thread", metadata={}),
            request=request,
        )
        await threads.search_threads(
            threads.ThreadSearchRequest(metadata={}),
            request=request,
        )

    asyncio.run(_scenario())

    assert thread_store.get_calls == [
        {"thread_id": "auth-thread", "user_id": str(user_id)},
        {"thread_id": "auth-thread", "user_id": None},
    ]
    assert thread_store.create_calls == [{"thread_id": "auth-thread", "assistant_id": None, "user_id": str(user_id), "metadata": {}}]
    assert thread_store.search_calls == [{"metadata": None, "status": None, "limit": 100, "offset": 0, "user_id": str(user_id)}]
    run_manager.begin_thread_recreate.assert_awaited_once_with("auth-thread")
    run_manager.end_thread_write.assert_awaited_once_with("auth-thread")
    run_manager.end_thread_delete.assert_not_awaited()


def test_create_thread_rejects_thread_id_owned_by_another_user() -> None:
    import asyncio

    from app.gateway.authz import AuthContext, Permissions

    class RecordingThreadStore:
        def __init__(self) -> None:
            self.get_calls = []
            self.create = AsyncMock()
            self.update_owner = AsyncMock()

        async def get(self, thread_id, **kwargs):
            self.get_calls.append({"thread_id": thread_id, **kwargs})
            if kwargs.get("user_id") is None:
                return {
                    "thread_id": thread_id,
                    "user_id": "other-user",
                    "status": "idle",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "metadata": {},
                }
            return None

    user_id = _HISTORY_USER_ID
    thread_store = RecordingThreadStore()
    checkpointer = SimpleNamespace(aput=AsyncMock())
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(
            user=_make_user(user_id),
            auth=AuthContext(
                user=_make_user(user_id),
                permissions=[Permissions.THREADS_WRITE],
            ),
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=RunManager(),
            )
        ),
    )

    async def _scenario():
        with pytest.raises(HTTPException) as exc_info:
            await threads.create_thread(
                threads.ThreadCreateRequest(thread_id="shared-thread", metadata={}),
                request=request,
            )
        return exc_info.value

    exc = asyncio.run(_scenario())

    assert exc.status_code == 409
    assert thread_store.get_calls == [
        {"thread_id": "shared-thread", "user_id": str(user_id)},
        {"thread_id": "shared-thread", "user_id": None},
    ]
    thread_store.create.assert_not_awaited()
    thread_store.update_owner.assert_not_awaited()
    checkpointer.aput.assert_not_awaited()


def test_create_thread_returns_iso_timestamps() -> None:
    app, _store, _checkpointer = _build_thread_app()

    with TestClient(app) as client:
        response = client.post("/api/threads", json={"metadata": {}})

    assert response.status_code == 200, response.text
    body = response.json()
    assert _ISO_TIMESTAMP_RE.match(body["created_at"]), body["created_at"]
    assert _ISO_TIMESTAMP_RE.match(body["updated_at"]), body["updated_at"]
    assert body["created_at"] == body["updated_at"]


def test_internal_owner_header_assigns_thread_to_owner() -> None:
    import asyncio

    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

    store = InMemoryStore()
    checkpointer = InMemorySaver()
    thread_store = MemoryThreadMetaStore(store)
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
        state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE), auth_source="internal"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=RunManager(),
            )
        ),
    )

    async def _scenario():
        response = await threads.create_thread(
            threads.ThreadCreateRequest(thread_id="channel-thread", metadata={}),
            request=request,
        )
        owner_row = await thread_store.get("channel-thread", user_id="owner-1")
        internal_row = await thread_store.get("channel-thread", user_id="default")
        return response, owner_row, internal_row

    response, owner_row, internal_row = asyncio.run(_scenario())

    assert response.thread_id == "channel-thread"
    assert owner_row is not None
    assert owner_row["user_id"] == "owner-1"
    assert internal_row is None


@pytest.mark.asyncio
async def test_create_thread_checkpoint_write_blocks_delete_gate():
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    class BlockingCheckpointer:
        async def aput(self, config, checkpoint, metadata, writes):
            write_started.set()
            await release_write.wait()
            return config

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    run_manager = RunManager()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="user-a")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=BlockingCheckpointer(),
                thread_store=thread_store,
                run_manager=run_manager,
            )
        ),
    )

    create_task = asyncio.create_task(
        call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id="create-delete-race"),
            request,
        )
    )
    await write_started.wait()

    delete_gate = asyncio.create_task(run_manager.begin_thread_delete("create-delete-race"))
    await asyncio.sleep(0)
    assert not delete_gate.done()

    release_write.set()
    await create_task
    await delete_gate


@pytest.mark.asyncio
async def test_create_thread_initial_checkpoint_blocks_run_creation():
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    class BlockingCheckpointer:
        async def aput(self, config, checkpoint, metadata, writes):
            write_started.set()
            await release_write.wait()
            return config

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    run_manager = RunManager()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="user-a")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=BlockingCheckpointer(),
                thread_store=thread_store,
                run_manager=run_manager,
            )
        ),
    )

    create_task = asyncio.create_task(
        call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id="create-run-race"),
            request,
        )
    )
    await write_started.wait()

    run_task = asyncio.create_task(run_manager.create_or_reject("create-run-race", user_id="user-a"))
    await asyncio.sleep(0)
    assert not run_task.done()

    release_write.set()
    await create_task
    run = await run_task
    assert run.thread_id == "create-run-race"


@pytest.mark.asyncio
async def test_concurrent_requested_thread_create_allows_only_one_foreign_owner():
    second_create_arrived = asyncio.Event()
    create_calls = 0

    class CoordinatedThreadStore(MemoryThreadMetaStore):
        async def create(self, *args, **kwargs):
            nonlocal create_calls
            create_calls += 1
            if create_calls == 1:
                await second_create_arrived.wait()
            else:
                second_create_arrived.set()
            return await super().create(*args, **kwargs)

    class RecordingCheckpointer:
        def __init__(self):
            self.calls = 0

        async def aput(self, *args, **kwargs):
            self.calls += 1

    thread_store = CoordinatedThreadStore(InMemoryStore())
    checkpointer = RecordingCheckpointer()
    app_state = SimpleNamespace(
        checkpointer=checkpointer,
        thread_store=thread_store,
        run_manager=RunManager(),
    )

    def request_for(user_id: str):
        return SimpleNamespace(headers={}, state=SimpleNamespace(user=SimpleNamespace(id=user_id)), app=SimpleNamespace(state=app_state))

    results = await asyncio.gather(
        call_unwrapped(threads.create_thread, threads.ThreadCreateRequest(thread_id="owner-race"), request_for("user-a")),
        call_unwrapped(threads.create_thread, threads.ThreadCreateRequest(thread_id="owner-race"), request_for("user-b")),
        return_exceptions=True,
    )

    responses = [result for result in results if isinstance(result, threads.ThreadResponse)]
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(responses) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 409
    row = await thread_store.get("owner-race", user_id=None)
    assert row is not None
    assert row["user_id"] in {"user-a", "user-b"}
    assert checkpointer.calls == 1


@pytest.mark.asyncio
async def test_concurrent_same_owner_checkpoint_failure_keeps_successful_metadata():
    second_create_arrived = asyncio.Event()
    first_checkpoint_done = asyncio.Event()
    create_calls = 0

    class CoordinatedThreadStore(MemoryThreadMetaStore):
        async def create(self, *args, **kwargs):
            nonlocal create_calls
            create_calls += 1
            if create_calls == 1:
                await second_create_arrived.wait()
            else:
                second_create_arrived.set()
                await first_checkpoint_done.wait()
            return await super().create(*args, **kwargs)

    class OneSuccessCheckpointer:
        def __init__(self):
            self.calls = 0
            self.saver = InMemorySaver()

        async def aput(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                first_checkpoint_done.set()
                raise RuntimeError("checkpoint failed")
            return await self.saver.aput(*args, **kwargs)

        async def aget_tuple(self, config):
            return await self.saver.aget_tuple(config)

    thread_store = CoordinatedThreadStore(InMemoryStore())
    checkpointer = OneSuccessCheckpointer()
    app_state = SimpleNamespace(
        checkpointer=checkpointer,
        thread_store=thread_store,
        run_manager=RunManager(),
    )
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(state=app_state),
    )

    results = await asyncio.gather(
        call_unwrapped(threads.create_thread, threads.ThreadCreateRequest(thread_id="same-owner-race"), request),
        call_unwrapped(threads.create_thread, threads.ThreadCreateRequest(thread_id="same-owner-race"), request),
        return_exceptions=True,
    )

    responses = [result for result in results if isinstance(result, threads.ThreadResponse)]
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(responses) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 500
    row = await thread_store.get("same-owner-race", user_id="same-owner")
    assert row is not None
    assert row["user_id"] == "same-owner"
    assert checkpointer.calls == 2
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": "same-owner-race", "checkpoint_ns": ""}}) is not None


@pytest.mark.asyncio
async def test_existing_same_owner_post_heals_missing_initial_checkpoint():
    class FailOnceCheckpointer:
        def __init__(self):
            self.calls = 0
            self.saver = InMemorySaver()

        async def aput(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("checkpoint failed")
            return await self.saver.aput(*args, **kwargs)

        async def aget_tuple(self, config):
            return await self.saver.aget_tuple(config)

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    checkpointer = FailOnceCheckpointer()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=RunManager(),
            )
        ),
    )
    body = threads.ThreadCreateRequest(thread_id="retry-initial-checkpoint", metadata={"source": "first"})

    with pytest.raises(HTTPException) as first_error:
        await call_unwrapped(threads.create_thread, body, request)
    second = await call_unwrapped(threads.create_thread, body, request)

    assert first_error.value.status_code == 500
    assert second.thread_id == "retry-initial-checkpoint"
    checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": "retry-initial-checkpoint", "checkpoint_ns": ""}})
    assert checkpoint is not None
    assert checkpointer.calls == 2


@pytest.mark.asyncio
async def test_recreate_retry_heals_checkpoint_and_reopens_delete_gate():
    class FailOnceCheckpointer:
        def __init__(self):
            self.calls = 0
            self.saver = InMemorySaver()

        async def aput(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("checkpoint failed")
            return await self.saver.aput(*args, **kwargs)

        async def aget_tuple(self, config):
            return await self.saver.aget_tuple(config)

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    checkpointer = FailOnceCheckpointer()
    run_manager = RunManager()
    await run_manager.begin_thread_delete("retry-recreate")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=run_manager,
            )
        ),
    )
    body = threads.ThreadCreateRequest(thread_id="retry-recreate")

    with pytest.raises(HTTPException) as first_error:
        await call_unwrapped(threads.create_thread, body, request)
    response = await call_unwrapped(threads.create_thread, body, request)

    assert first_error.value.status_code == 500
    assert response.thread_id == "retry-recreate"
    await run_manager.begin_thread_write("retry-recreate")
    await run_manager.end_thread_write("retry-recreate")


@pytest.mark.asyncio
async def test_concurrent_recreate_success_reopens_gate_when_winner_checkpoint_fails():
    winner_created = asyncio.Event()
    create_calls = 0

    class CoordinatedThreadStore(MemoryThreadMetaStore):
        async def create(self, *args, **kwargs):
            nonlocal create_calls
            create_calls += 1
            if create_calls == 1:
                await winner_created.wait()
                return await super().create(*args, **kwargs)
            result = await super().create(*args, **kwargs)
            winner_created.set()
            return result

    class FirstCheckpointFails:
        def __init__(self):
            self.calls = 0
            self.saver = InMemorySaver()

        async def aput(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("winner checkpoint failed")
            return await self.saver.aput(*args, **kwargs)

        async def aget_tuple(self, config):
            return await self.saver.aget_tuple(config)

    thread_store = CoordinatedThreadStore(InMemoryStore())
    checkpointer = FirstCheckpointFails()
    run_manager = RunManager()
    await run_manager.begin_thread_delete("concurrent-recreate")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=run_manager,
            )
        ),
    )
    body = threads.ThreadCreateRequest(thread_id="concurrent-recreate")

    results = await asyncio.gather(
        call_unwrapped(threads.create_thread, body, request),
        call_unwrapped(threads.create_thread, body, request),
        return_exceptions=True,
    )

    assert len([result for result in results if isinstance(result, threads.ThreadResponse)]) == 1
    assert len([result for result in results if isinstance(result, HTTPException)]) == 1
    await run_manager.begin_thread_write("concurrent-recreate")
    await run_manager.end_thread_write("concurrent-recreate")


@pytest.mark.asyncio
async def test_existing_same_owner_post_does_not_overwrite_checkpoint():
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    checkpointer = InMemorySaver()
    await thread_store.create("existing-checkpoint", user_id="same-owner")
    config = {"configurable": {"thread_id": "existing-checkpoint", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"title": "keep me"}
    await checkpointer.aput(config, checkpoint, {"step": 0, "source": "loop", "writes": {}}, {})
    before = await checkpointer.aget_tuple(config)
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=RunManager(),
            )
        ),
    )

    response = await call_unwrapped(
        threads.create_thread,
        threads.ThreadCreateRequest(thread_id="existing-checkpoint"),
        request,
    )

    after = await checkpointer.aget_tuple(config)
    assert response.thread_id == "existing-checkpoint"
    assert after is not None and before is not None
    assert after.checkpoint["id"] == before.checkpoint["id"]


@pytest.mark.asyncio
async def test_concurrent_same_owner_post_returns_canonical_created_record():
    canonical = ThreadMetaCreateResult(
        {
            "thread_id": "canonical-thread",
            "status": "idle",
            "created_at": "2026-01-02T03:04:05+00:00",
            "updated_at": "2026-01-02T03:04:06+00:00",
            "metadata": {"winner": "stored"},
            "user_id": "same-owner",
        },
        created=False,
    )

    class ConcurrentWinnerStore:
        async def get(self, thread_id, **kwargs):
            return None

        async def create(self, thread_id, **kwargs):
            return canonical

    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=InMemorySaver(),
                thread_store=ConcurrentWinnerStore(),
                run_manager=RunManager(),
            )
        ),
    )

    response = await call_unwrapped(
        threads.create_thread,
        threads.ThreadCreateRequest(
            thread_id="canonical-thread",
            metadata={"winner": "request"},
        ),
        request,
    )

    assert response.status == canonical["status"]
    assert response.created_at == canonical["created_at"]
    assert response.updated_at == canonical["updated_at"]
    assert response.metadata == canonical["metadata"]


@pytest.mark.asyncio
async def test_same_owner_post_rejects_durable_deleting_tombstone_after_restart():
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("deleting-thread", user_id="same-owner")
    await thread_store.update_status("deleting-thread", "deleting", user_id="same-owner")
    checkpointer = InMemorySaver()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=thread_store,
                run_manager=RunManager(),
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id="deleting-thread"),
            request,
        )

    assert exc_info.value.status_code == 409
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": "deleting-thread", "checkpoint_ns": ""}}) is None


@pytest.mark.asyncio
async def test_existing_post_does_not_bypass_delete_that_started_after_first_read():
    calls = 0

    class DeletingAfterFirstReadStore:
        async def get(self, thread_id, **kwargs):
            nonlocal calls
            calls += 1
            return {
                "thread_id": thread_id,
                "user_id": "same-owner",
                "status": "idle" if calls == 1 else "deleting",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "metadata": {},
            }

    run_manager = RunManager()
    await run_manager.begin_thread_delete("delete-race")
    checkpointer = SimpleNamespace(aput=AsyncMock())
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=SimpleNamespace(id="same-owner")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                thread_store=DeletingAfterFirstReadStore(),
                run_manager=run_manager,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await call_unwrapped(
            threads.create_thread,
            threads.ThreadCreateRequest(thread_id="delete-race"),
            request,
        )

    assert exc_info.value.status_code == 409
    checkpointer.aput.assert_not_awaited()


def test_internal_owner_header_cannot_reassign_real_owner_thread() -> None:
    import asyncio

    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

    store = InMemoryStore()
    checkpointer = InMemorySaver()
    thread_store = MemoryThreadMetaStore(store)

    async def _scenario():
        await thread_store.create("existing-thread", user_id="victim-owner")
        request = SimpleNamespace(
            headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
            state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE), auth_source="internal"),
            app=SimpleNamespace(state=SimpleNamespace(checkpointer=checkpointer, thread_store=thread_store)),
        )
        with pytest.raises(HTTPException) as exc_info:
            await threads.create_thread(
                threads.ThreadCreateRequest(thread_id="existing-thread", metadata={}),
                request=request,
            )
        victim_row = await thread_store.get("existing-thread", user_id="victim-owner")
        owner_row = await thread_store.get("existing-thread", user_id="owner-1")
        return exc_info.value, victim_row, owner_row

    exc, victim_row, owner_row = asyncio.run(_scenario())

    assert exc.status_code == 409
    assert victim_row is not None
    assert victim_row["user_id"] == "victim-owner"
    assert owner_row is None


def test_get_thread_returns_iso_for_legacy_unix_record() -> None:
    """A thread record written by older versions stores ``time.time()``
    floats. ``get_thread`` must transparently surface them as ISO so the
    frontend's ``new Date(...)`` parser does not break.
    """
    app, store, checkpointer = _build_thread_app()

    legacy_thread_id = "legacy-thread"
    legacy_ts = "1777252410.411327"

    async def _seed() -> None:
        await store.aput(
            THREADS_NS,
            legacy_thread_id,
            {
                "thread_id": legacy_thread_id,
                "status": "idle",
                "created_at": legacy_ts,
                "updated_at": legacy_ts,
                "metadata": {},
            },
        )
        from langgraph.checkpoint.base import empty_checkpoint

        await checkpointer.aput(
            {"configurable": {"thread_id": legacy_thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            {"step": -1, "source": "input", "writes": None, "parents": {}},
            {},
        )

    import asyncio

    asyncio.run(_seed())

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{legacy_thread_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert _ISO_TIMESTAMP_RE.match(body["created_at"]), body["created_at"]
    assert _ISO_TIMESTAMP_RE.match(body["updated_at"]), body["updated_at"]


def test_patch_thread_returns_iso_and_advances_updated_at() -> None:
    app, store, _checkpointer = _build_thread_app()
    thread_id = "patch-target"

    legacy_created = "1777000000.000000"
    legacy_updated = "1777000000.000000"

    async def _seed() -> None:
        await store.aput(
            THREADS_NS,
            thread_id,
            {
                "thread_id": thread_id,
                "status": "idle",
                "created_at": legacy_created,
                "updated_at": legacy_updated,
                "metadata": {"k": "v0"},
            },
        )

    import asyncio

    asyncio.run(_seed())

    with TestClient(app) as client:
        response = client.patch(f"/api/threads/{thread_id}", json={"metadata": {"k": "v1"}})

    assert response.status_code == 200, response.text
    body = response.json()
    assert _ISO_TIMESTAMP_RE.match(body["created_at"]), body["created_at"]
    assert _ISO_TIMESTAMP_RE.match(body["updated_at"]), body["updated_at"]
    # Patch issues a fresh ``updated_at`` via ``MemoryThreadMetaStore.update_metadata``,
    # so it must be > the migrated legacy ``created_at`` (both ISO strings
    # sort lexicographically by time when the format is consistent).
    assert body["updated_at"] > body["created_at"]
    assert body["metadata"] == {"k": "v1"}


def test_search_threads_normalizes_legacy_unix_seconds_to_iso() -> None:
    """``MemoryThreadMetaStore`` may hold legacy ``time.time()`` floats
    written by older Gateway versions. ``/search`` must surface them as
    ISO via ``coerce_iso`` so the frontend's ``new Date(...)`` parser
    does not break.
    """
    app, store, _checkpointer = _build_thread_app()

    async def _seed() -> None:
        # Legacy unix-second float (the literal value from issue #2594).
        await store.aput(
            THREADS_NS,
            "legacy",
            {
                "thread_id": "legacy",
                "status": "idle",
                "created_at": 1777000000.0,
                "updated_at": 1777000000.0,
                "metadata": {},
            },
        )
        # Modern ISO string, slightly later.
        await store.aput(
            THREADS_NS,
            "modern",
            {
                "thread_id": "modern",
                "status": "idle",
                "created_at": "2026-04-27T00:00:00+00:00",
                "updated_at": "2026-04-27T00:00:00+00:00",
                "metadata": {},
            },
        )

    import asyncio

    asyncio.run(_seed())

    with TestClient(app) as client:
        response = client.post("/api/threads/search", json={"limit": 10})

    assert response.status_code == 200, response.text
    items = response.json()
    assert {item["thread_id"] for item in items} == {"legacy", "modern"}
    for item in items:
        assert _ISO_TIMESTAMP_RE.match(item["created_at"]), item
        assert _ISO_TIMESTAMP_RE.match(item["updated_at"]), item


def test_memory_thread_meta_store_writes_iso_on_create() -> None:
    """``MemoryThreadMetaStore.create`` must emit ISO so newly created
    threads serialize correctly without depending on the router's
    ``coerce_iso`` heal path.
    """
    import asyncio

    store = InMemoryStore()
    repo = MemoryThreadMetaStore(store)

    async def _scenario() -> dict:
        await repo.create("fresh", user_id=None, metadata={"a": 1})
        record = (await store.aget(THREADS_NS, "fresh")).value
        return record

    record = asyncio.run(_scenario())
    assert _ISO_TIMESTAMP_RE.match(record["created_at"]), record
    assert _ISO_TIMESTAMP_RE.match(record["updated_at"]), record


def test_get_thread_state_returns_iso_for_legacy_checkpoint_metadata() -> None:
    """Checkpoints written by older Gateway versions stored
    ``created_at`` as a unix-second float in their metadata. The
    ``/state`` endpoint must surface that value as ISO so the frontend's
    ``new Date(...)`` parser does not break — same root cause as the
    thread-record bug fixed in #2594, but on the checkpoint side.
    """
    app, _store, checkpointer = _build_thread_app()
    thread_id = "legacy-state"

    async def _seed() -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        await checkpointer.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            {"step": -1, "source": "input", "writes": None, "parents": {}, "created_at": 1777252410.411327},
            {},
        )

    import asyncio

    asyncio.run(_seed())
    asyncio.run(app.state.thread_store.create(thread_id))

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/state")

    assert response.status_code == 200, response.text
    body = response.json()
    assert _ISO_TIMESTAMP_RE.match(body["created_at"]), body["created_at"]
    assert _ISO_TIMESTAMP_RE.match(body["checkpoint"]["ts"]), body["checkpoint"]


def test_get_thread_history_returns_iso_for_legacy_checkpoint_metadata() -> None:
    """``/history`` walks ``checkpointer.alist`` and emits one entry per
    checkpoint. Each entry's ``created_at`` must come out as ISO even if
    older checkpoints stored a unix-second float in their metadata.
    """
    app, _store, checkpointer = _build_thread_app()
    thread_id = "legacy-history"

    async def _seed() -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        await checkpointer.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            {"step": -1, "source": "input", "writes": None, "parents": {}, "created_at": 1777252410.411327},
            {},
        )

    import asyncio

    asyncio.run(_seed())
    asyncio.run(app.state.thread_store.create(thread_id))

    with TestClient(app) as client:
        response = client.post(f"/api/threads/{thread_id}/history", json={"limit": 10})

    assert response.status_code == 200, response.text
    entries = response.json()
    assert entries, "expected at least one history entry"
    for entry in entries:
        assert _ISO_TIMESTAMP_RE.match(entry["created_at"]), entry


def test_get_thread_history_scopes_turn_duration_to_current_user() -> None:
    app, _store, _checkpointer = _build_thread_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    thread_id = "shared-thread-id"
    other_user_id = "33333333-3333-3333-3333-333333333333"
    message_id = "shared-ai-message"

    def _run(run_id: str, user_id: str, created_at: str, updated_at: str) -> RunRecord:
        return RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=None,
            status=RunStatus.success,
            on_disconnect=DisconnectMode.continue_,
            user_id=user_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    current_user_id = str(_HISTORY_USER_ID)
    current_user_runs = [
        _run(
            "run-a",
            current_user_id,
            "2026-07-04T10:00:00+00:00",
            "2026-07-04T10:00:03+00:00",
        )
    ]
    other_user_runs = [
        _run(
            "run-b",
            other_user_id,
            "2026-07-04T10:00:00+00:00",
            "2026-07-04T10:01:39+00:00",
        )
    ]

    class UserScopedRunManager:
        def __init__(self):
            self.calls = []

        async def list_by_thread(self, thread_id_arg, *, user_id=None, limit=100):
            self.calls.append({"thread_id": thread_id_arg, "user_id": user_id, "limit": limit})
            if user_id == current_user_id:
                return current_user_runs
            return other_user_runs

    class UserScopedEventStore:
        def __init__(self):
            self.calls = []

        async def list_messages(self, thread_id_arg, *, limit=50, before_seq=None, after_seq=None, user_id=None):
            self.calls.append(
                {
                    "thread_id": thread_id_arg,
                    "limit": limit,
                    "before_seq": before_seq,
                    "after_seq": after_seq,
                    "user_id": user_id,
                }
            )
            if user_id == current_user_id:
                return [{"run_id": "run-a", "content": {"type": "ai", "id": message_id}}]
            return [{"run_id": "run-b", "content": {"type": "ai", "id": message_id}}]

    class FakeCheckpointer:
        async def alist(self, config, limit=None):
            assert config == {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            assert limit == 10
            yield SimpleNamespace(
                config={"configurable": {"checkpoint_id": "checkpoint-a"}},
                parent_config=None,
                metadata={
                    "step": -1,
                    "source": "input",
                    "writes": None,
                    "parents": {},
                    "created_at": "2026-07-04T10:00:04+00:00",
                },
                checkpoint={
                    "channel_values": {
                        "messages": [
                            {
                                "type": "ai",
                                "id": message_id,
                                "content": "assistant answer",
                            }
                        ]
                    }
                },
                tasks=[],
            )

    run_manager = UserScopedRunManager()
    event_store = UserScopedEventStore()
    app.state.checkpointer = FakeCheckpointer()
    app.state.run_manager = run_manager
    app.state.run_event_store = event_store
    asyncio.run(app.state.thread_store.create(thread_id))

    with TestClient(app) as client:
        response = client.post(f"/api/threads/{thread_id}/history", json={"limit": 10})

    assert response.status_code == 200, response.text
    messages = response.json()[0]["values"]["messages"]
    assert messages[0]["additional_kwargs"]["turn_duration"] == 3
    assert run_manager.calls == [{"thread_id": thread_id, "user_id": current_user_id, "limit": 100}]
    assert event_store.calls[0]["thread_id"] == thread_id
    assert event_store.calls[0]["user_id"] == current_user_id
    assert event_store.calls[0]["limit"] == 1000


# ── Metadata filter validation at API boundary ────────────────────────────────


def test_search_threads_rejects_invalid_key_at_api_boundary() -> None:
    """Keys that don't match [A-Za-z0-9_-]+ are rejected by the Pydantic
    validator on ThreadSearchRequest.metadata — 422 from both backends.
    """
    app, _store, _checkpointer = _build_thread_app()

    with TestClient(app) as client:
        response = client.post("/api/threads/search", json={"metadata": {"bad;key": "x"}})

    assert response.status_code == 422


def test_search_threads_rejects_unsupported_value_type_at_api_boundary() -> None:
    """Value types outside (None, bool, int, float, str) are rejected."""
    app, _store, _checkpointer = _build_thread_app()

    with TestClient(app) as client:
        response = client.post("/api/threads/search", json={"metadata": {"env": ["a", "b"]}})

    assert response.status_code == 422


def test_search_threads_returns_400_for_backend_invalid_metadata_filter() -> None:
    """If the backend still raises InvalidMetadataFilterError (defense in
    depth), the handler surfaces it as HTTP 400.
    """
    app, _store, _checkpointer = _build_thread_app()
    thread_store = app.state.thread_store

    async def _raise(**kwargs):
        raise InvalidMetadataFilterError("rejected")

    with TestClient(app) as client:
        with patch.object(thread_store, "search", side_effect=_raise):
            response = client.post("/api/threads/search", json={"metadata": {"valid_key": "x"}})

    assert response.status_code == 400
    assert "rejected" in response.json()["detail"]


def test_search_threads_succeeds_with_valid_metadata() -> None:
    """Sanity check: valid metadata passes through without error."""
    app, _store, _checkpointer = _build_thread_app()

    with TestClient(app) as client:
        response = client.post("/api/threads/search", json={"metadata": {"env": "prod"}})

    assert response.status_code == 200


# ── command-room RoundRecord read API ────────────────────────────────────────


def test_get_latest_command_room_round_returns_record(tmp_path):
    from deerflow.command_room.round_record import record_command_room_round

    paths = Paths(tmp_path)
    app = make_authed_test_app()
    app.include_router(threads.router)

    with patch("deerflow.command_room.round_record.get_paths", return_value=paths):
        record_command_room_round(
            thread_id="round-thread",
            agent_name="command-room",
            user_id="alice",
            user_message="SECRET_USER_INTENT_SHOULD_NOT_APPEAR",
            final_text="""Round Card
Goal: verify command room state
Evidence: worker self-claims only
Verdict: PASS
Next: stop

SECRET_FINAL_TEXT_SHOULD_NOT_APPEAR
""",
            audit_records=[
                {
                    "status": "completed",
                    "task_id": "lane-opposition",
                    "subagent_type": "opposition",
                    "description": "opposition check",
                    "prompt_sha256": "prompt-hash",
                    "prompt_chars": 10,
                    "result_sha256": "result-hash",
                    "result_chars": 20,
                    "signal": {
                        "valid": True,
                        "missing": [],
                        "fields": {
                            "Role": "opposition",
                            "Claim": "PASS is unsupported.",
                            "EvidenceRefs": "worker self-claims only",
                            "RedlineTouched": "true",
                            "RecommendedDecision": "STOP_CONFIRM",
                            "NextAction": "Collect evidence.",
                        },
                    },
                }
            ],
        )

        with (
            patch("app.gateway.routers.threads.get_request_storage_user_id", return_value="alice"),
            TestClient(app) as client,
        ):
            response = client.get("/api/threads/round-thread/command-room/rounds/latest")

    assert response.status_code == 200, response.text
    text = response.text
    assert "SECRET_USER_INTENT_SHOULD_NOT_APPEAR" not in text
    assert "SECRET_FINAL_TEXT_SHOULD_NOT_APPEAR" not in text

    body = response.json()
    latest = body["round"]
    assert latest["threadId"] == "round-thread"
    assert latest["verdict"]["decision"] == "PASS"
    assert latest["verdict"]["modelDecision"] == "PASS"
    assert latest["signals"][0]["recommendedDecision"] == "STOP_CONFIRM"
    assert latest["dispatchPlan"][0]["role"] == "opposition"
    assert latest["signals"][0]["outputRef"] == {"chars": 20, "sha256": "result-hash"}


def test_get_latest_command_room_round_returns_404_when_missing(tmp_path):
    paths = Paths(tmp_path)
    app = make_authed_test_app()
    app.include_router(threads.router)

    with (
        patch("deerflow.command_room.round_record.get_paths", return_value=paths),
        patch("app.gateway.routers.threads.get_request_storage_user_id", return_value="alice"),
        TestClient(app) as client,
    ):
        response = client.get("/api/threads/missing-round/command-room/rounds/latest")

    assert response.status_code == 404
    assert "RoundRecord" in response.json()["detail"]


def test_get_latest_command_room_round_is_user_scoped(tmp_path):
    from deerflow.command_room.round_record import record_command_room_round

    paths = Paths(tmp_path)
    app = make_authed_test_app()
    app.include_router(threads.router)

    with patch("deerflow.command_room.round_record.get_paths", return_value=paths):
        record_command_room_round(
            thread_id="shared-thread-id",
            agent_name="command-room",
            user_id="alice",
            user_message="intent",
            final_text=(
                "Round Card\n"
                "Evidence: concrete ref\n"
                "Opposition:\n"
                "Not dispatched because: low risk\n"
                "Risk class: low\n"
                "Evidence basis: deterministic\n"
                "No permission expansion: true\n"
                "No PASS from worker self-claim: true\n"
                "Verdict: PASS\n"
                "Next: done\n"
            ),
            audit_records=[],
        )

        with (
            patch("app.gateway.routers.threads.get_request_storage_user_id", return_value="bob"),
            TestClient(app) as client,
        ):
            response = client.get("/api/threads/shared-thread-id/command-room/rounds/latest")

    assert response.status_code == 404


def test_get_latest_command_room_round_uses_internal_owner_header(tmp_path):
    import asyncio

    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
    from deerflow.command_room.round_record import record_command_room_round

    paths = Paths(tmp_path)
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
        state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE)),
    )

    with patch("deerflow.command_room.round_record.get_paths", return_value=paths):
        record_command_room_round(
            thread_id="channel-round-thread",
            agent_name="command-room",
            user_id="owner-1",
            user_message="intent",
            final_text=(
                "Round Card\n"
                "Evidence: command output\n"
                "Opposition:\n"
                "Not dispatched because: low risk\n"
                "Risk class: low\n"
                "Evidence basis: deterministic\n"
                "No permission expansion: true\n"
                "No PASS from worker self-claim: true\n"
                "Verdict: PASS\n"
                "Next: done\n"
            ),
            audit_records=[],
        )

        response = asyncio.run(call_unwrapped(threads.get_latest_command_room_round, "channel-round-thread", request))

    assert response.round["threadId"] == "channel-round-thread"
    assert response.round["verdict"]["decision"] == "PASS"


def test_get_latest_command_room_round_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)
    app = make_authed_test_app()
    app.include_router(threads.router)

    with (
        patch("deerflow.command_room.round_record.get_paths", return_value=paths),
        patch("app.gateway.routers.threads.get_request_storage_user_id", return_value="alice"),
        TestClient(app) as client,
    ):
        response = client.get("/api/threads/thread.with.dot/command-room/rounds/latest")

    assert response.status_code == 422
    assert "Invalid thread_id" in response.json()["detail"]


# ── update_thread_state: each call inserts a new checkpoint (regression) ───────


def test_update_thread_state_inserts_new_checkpoint_each_call() -> None:
    """Each ``POST /state`` must INSERT a distinct, time-ordered checkpoint.

    Regression for the in-place REPLACE bug: before the fix the new
    checkpoint reused the previous checkpoint["id"], so InMemorySaver/SQLite
    overwrote the existing row and history never grew. The fix assigns a
    fresh uuid6 to checkpoint["id"] before aput.
    """
    app, _store, checkpointer = _build_thread_app()

    with TestClient(app) as client:
        created = client.post("/api/threads", json={"metadata": {}})
        assert created.status_code == 200, created.text
        thread_id = created.json()["thread_id"]

        r1 = client.post(f"/api/threads/{thread_id}/state", json={"values": {"title": "First"}})
        assert r1.status_code == 200, r1.text
        r2 = client.post(f"/api/threads/{thread_id}/state", json={"values": {"title": "Second"}})
        assert r2.status_code == 200, r2.text

    import asyncio

    async def _collect():
        return [cp async for cp in checkpointer.alist({"configurable": {"thread_id": thread_id}})]

    history = asyncio.run(_collect())

    # 1 empty checkpoint from create_thread + 1 per update call.
    assert len(history) >= 3, f"expected >=3 checkpoints, got {len(history)}"

    ids = [cp.config["configurable"]["checkpoint_id"] for cp in history]
    assert len(ids) == len(set(ids)), f"duplicate checkpoint ids: {ids}"
    # alist() returns newest-first; uuid6 is time-ordered so newest > oldest.
    assert ids[0] > ids[-1], f"checkpoint ids not time-ordered (uuid4 instead of uuid6?): {ids}"

    # aput must PRESERVE the endpoint-assigned checkpoint["id"], not mint its own
    # and discard the payload's. If it generated a fresh id internally the fix
    # would be a no-op (the bug would never have existed). Assert the id returned
    # in each response round-tripped into the persisted history, and that the two
    # update writes kept the endpoint's uuid6 time-ordering through aput.
    resp_ids = [r1.json()["checkpoint_id"], r2.json()["checkpoint_id"]]
    assert all(cid is not None for cid in resp_ids), f"response missing checkpoint_id: {resp_ids}"
    assert set(resp_ids) <= set(ids), f"aput discarded endpoint-assigned id: returned {resp_ids}, stored {ids}"
    assert resp_ids[1] > resp_ids[0], f"endpoint-assigned uuid6 not preserved/ordered through aput: {resp_ids}"


@pytest.mark.asyncio
async def test_update_thread_state_holds_checkpoint_write_barrier() -> None:
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    class BlockingCheckpointer:
        async def aget_tuple(self, _config):
            return SimpleNamespace(
                checkpoint={"id": "old", "channel_values": {}},
                metadata={"created_at": "2026-01-01T00:00:00+00:00"},
            )

        async def aput(self, _config, checkpoint, _metadata, _writes):
            write_started.set()
            await release_write.wait()
            return {
                "configurable": {
                    "thread_id": "thread-write",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint["id"],
                }
            }

    run_manager = RunManager()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=BlockingCheckpointer(),
                thread_store=MemoryThreadMetaStore(InMemoryStore()),
                run_manager=run_manager,
            )
        )
    )
    body = threads.ThreadStateUpdateRequest(values={"draft": "value"})

    update_task = asyncio.create_task(threads.update_thread_state.__wrapped__("thread-write", body, request))
    await write_started.wait()
    delete_task = asyncio.create_task(run_manager.begin_thread_delete("thread-write"))
    await asyncio.sleep(0)

    assert not delete_task.done()

    release_write.set()
    response = await update_task
    await delete_task
    assert response.values == {"draft": "value"}

    with pytest.raises(HTTPException) as exc_info:
        await threads.update_thread_state.__wrapped__("thread-write", body, request)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_thread_cleans_runs_and_events_with_owner_boundary(tmp_path):
    from app.gateway.routers.threads import delete_thread_data
    from deerflow.runtime.events.store.memory import MemoryRunEventStore
    from deerflow.runtime.runs.store.memory import MemoryRunStore

    class AppState:
        pass

    class App:
        pass

    class Request:
        pass

    request = Request()
    request.app = App()
    request.app.state = AppState()
    request.app.state.run_store = MemoryRunStore()
    request.app.state.run_event_store = MemoryRunEventStore()
    request.app.state.feedback_repo = SimpleNamespace(
        delete_by_thread=AsyncMock(return_value=1),
        delete_legacy_by_thread=AsyncMock(return_value=1),
    )
    request.app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    request.app.state.run_manager = SimpleNamespace(
        begin_thread_delete=AsyncMock(),
        list_by_thread=AsyncMock(return_value=[]),
        cancel=AsyncMock(),
    )
    request.app.state.stream_bridge = SimpleNamespace(cleanup=AsyncMock())
    request.app.state.checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    request.app.state.round_state_store = None
    request.state = type("State", (), {"user": type("User", (), {"id": "user-a"})()})()

    await request.app.state.thread_store.create("thread-x", user_id="user-a")
    await request.app.state.run_store.put("run-a", thread_id="thread-x", user_id="user-a")
    await request.app.state.run_store.put("run-b", thread_id="thread-x", user_id="user-b")
    await request.app.state.run_store.put("run-legacy", thread_id="thread-x", user_id=None)
    await request.app.state.run_event_store.put(thread_id="thread-x", run_id="run-a", event_type="message", category="message", content="a", user_id="user-a")
    await request.app.state.run_event_store.put(thread_id="thread-x", run_id="run-b", event_type="message", category="message", content="b", user_id="user-b")
    await request.app.state.run_event_store.put(thread_id="thread-x", run_id="run-legacy", event_type="message", category="message", content="legacy", user_id=None)

    response = await delete_thread_data.__wrapped__("thread-x", request)

    assert response.success is True
    request.app.state.feedback_repo.delete_by_thread.assert_awaited_once_with("thread-x", user_id="user-a")
    request.app.state.feedback_repo.delete_legacy_by_thread.assert_awaited_once_with("thread-x")
    assert await request.app.state.run_store.list_by_thread("thread-x", user_id="user-a") == []
    assert len(await request.app.state.run_event_store.list_messages("thread-x", user_id="user-a")) == 0
    assert [r["run_id"] for r in await request.app.state.run_store.list_by_thread("thread-x", user_id="user-b")] == ["run-b"]
    assert len(await request.app.state.run_event_store.list_messages("thread-x", user_id="user-b")) == 1
    assert [r["run_id"] for r in await request.app.state.run_store.list_by_thread("thread-x", user_id=None)] == ["run-b"]
    assert [e["run_id"] for e in await request.app.state.run_event_store.list_messages("thread-x", user_id=None)] == ["run-b"]


@pytest.mark.asyncio
async def test_patch_thread_write_finishes_before_delete_barrier():
    class BlockingThreadStore(MemoryThreadMetaStore):
        def __init__(self, store):
            super().__init__(store)
            self.write_started = asyncio.Event()
            self.allow_write = asyncio.Event()

        async def update_metadata(self, *args, **kwargs):
            self.write_started.set()
            await self.allow_write.wait()
            await super().update_metadata(*args, **kwargs)

    user = SimpleNamespace(id="user-a", system_role="user")
    thread_store = BlockingThreadStore(InMemoryStore())
    await thread_store.create("thread-patch", user_id="user-a")
    run_manager = RunManager()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(
            user=user,
            auth=AuthContext(
                user=user,
                permissions=[Permissions.THREADS_WRITE],
            ),
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=run_manager,
            )
        ),
    )
    patch_task = asyncio.create_task(
        threads.patch_thread(
            thread_id="thread-patch",
            body=threads.ThreadPatchRequest(metadata={"key": "value"}),
            request=request,
        )
    )
    await asyncio.wait_for(thread_store.write_started.wait(), timeout=1)
    delete_task = asyncio.create_task(run_manager.begin_thread_delete("thread-patch"))
    await asyncio.sleep(0)
    try:
        assert not delete_task.done()
    finally:
        thread_store.allow_write.set()

    await patch_task
    await delete_task


@pytest.mark.asyncio
async def test_legacy_claim_moves_all_default_thread_state_to_owner(tmp_path):
    from deerflow.persistence.round_state import MemoryRoundStateStore
    from deerflow.runtime.events.store.memory import MemoryRunEventStore
    from deerflow.runtime.runs.store.memory import MemoryRunStore
    from deerflow.runtime.user_context import DEFAULT_USER_ID

    thread_id = "thread-legacy-claim"
    owner_id = "owner-a"
    paths = Paths(tmp_path)
    default_dir = paths.thread_dir(thread_id, user_id=DEFAULT_USER_ID)
    legacy_dir = paths.thread_dir(thread_id)
    (default_dir / "audit").mkdir(parents=True)
    (default_dir / "audit" / "role_state.jsonl").write_text("default", encoding="utf-8")
    (legacy_dir / "user-data").mkdir(parents=True)
    (legacy_dir / "user-data" / "legacy.txt").write_text("legacy", encoding="utf-8")

    run_store = MemoryRunStore()
    await run_store.put(
        "run-default",
        thread_id=thread_id,
        status="success",
        user_id=DEFAULT_USER_ID,
    )
    event_store = MemoryRunEventStore()
    await event_store.put(
        thread_id=thread_id,
        run_id="run-default",
        event_type="message",
        category="message",
        user_id=DEFAULT_USER_ID,
    )
    round_store = MemoryRoundStateStore()
    await round_store.bind_run(
        thread_id=thread_id,
        run_id="run-default",
        user_id=DEFAULT_USER_ID,
    )
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create(thread_id, user_id=DEFAULT_USER_ID)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=RunManager(),
                run_store=run_store,
                run_event_store=event_store,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=round_store,
            )
        )
    )

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        await threads._claim_legacy_thread_related_data(thread_id, owner_id, request)

    claimed_run = await run_store.get("run-default", user_id=owner_id)
    assert claimed_run is not None
    assert await event_store.count_messages(thread_id, user_id=owner_id) == 1
    assert len(await round_store.list_by_thread(thread_id, user_id=owner_id)) == 1
    owner_dir = paths.thread_dir(thread_id, user_id=owner_id)
    assert (owner_dir / "audit" / "role_state.jsonl").read_text(encoding="utf-8") == "default"
    assert (owner_dir / "user-data" / "legacy.txt").read_text(encoding="utf-8") == "legacy"
    assert not default_dir.exists()
    assert not legacy_dir.exists()


@pytest.mark.asyncio
async def test_legacy_claim_failure_is_not_silently_ignored(tmp_path):
    failing_repo = SimpleNamespace(
        list_owners_by_thread=AsyncMock(return_value=set()),
        claim_legacy_by_thread=AsyncMock(side_effect=RuntimeError("claim failed")),
    )
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-claim-failure", user_id="default")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=RunManager(),
                run_store=failing_repo,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        )
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(RuntimeError, match="claim failed"),
    ):
        await threads._claim_legacy_thread_related_data(
            "thread-claim-failure",
            "owner-a",
            request,
        )

    reserved = await thread_store.get("thread-claim-failure", user_id=None)
    assert reserved is not None
    assert reserved["user_id"] == "owner-a"
    assert (
        await thread_store.is_legacy_claim_complete(
            "thread-claim-failure",
            "owner-a",
        )
        is False
    )

    failing_repo.claim_legacy_by_thread.side_effect = None
    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        await threads._claim_legacy_thread_related_data(
            "thread-claim-failure",
            "owner-a",
            request,
        )
    assert (
        await thread_store.is_legacy_claim_complete(
            "thread-claim-failure",
            "owner-a",
        )
        is True
    )


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_incomplete_legacy_claim_stays_inaccessible_until_retry_completes(
    tmp_path,
    failure_type,
):
    repository = SimpleNamespace(
        list_owners_by_thread=AsyncMock(return_value={"default"}),
        claim_legacy_by_thread=AsyncMock(side_effect=failure_type("claim interrupted")),
    )
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-partial-claim", user_id="default")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=RunManager(),
                run_store=repository,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        )
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(failure_type),
    ):
        await threads._claim_legacy_thread_related_data(
            "thread-partial-claim",
            "owner-a",
            request,
        )

    assert (
        await thread_store.check_access(
            "thread-partial-claim",
            "owner-a",
            require_existing=True,
        )
        is False
    )

    repository.claim_legacy_by_thread.side_effect = None
    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        await threads._claim_legacy_thread_related_data(
            "thread-partial-claim",
            "owner-a",
            request,
        )

    assert await thread_store.is_legacy_claim_complete("thread-partial-claim", "owner-a") is True
    assert (
        await thread_store.check_access(
            "thread-partial-claim",
            "owner-a",
            require_existing=True,
        )
        is True
    )


@pytest.mark.asyncio
async def test_legacy_claims_for_different_threads_do_not_share_delete_gate(tmp_path):
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def claim(thread_id, _owner_user_id):
        if thread_id == "thread-a":
            first_started.set()
            await release_first.wait()

    repository = SimpleNamespace(
        list_owners_by_thread=AsyncMock(return_value={"default"}),
        claim_legacy_by_thread=AsyncMock(side_effect=claim),
    )
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-a", user_id="default")
    await thread_store.create("thread-b", user_id="default")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=RunManager(),
                run_store=repository,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        )
    )

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        first = asyncio.create_task(threads._claim_legacy_thread_related_data("thread-a", "owner-a", request))
        await first_started.wait()
        await asyncio.wait_for(
            threads._claim_legacy_thread_related_data("thread-b", "owner-b", request),
            timeout=0.5,
        )
        release_first.set()
        await first

    assert await thread_store.is_legacy_claim_complete("thread-a", "owner-a") is True
    assert await thread_store.is_legacy_claim_complete("thread-b", "owner-b") is True


@pytest.mark.asyncio
async def test_cancelled_filesystem_claim_finishes_before_releasing_delete_gate(tmp_path):
    paths = Paths(tmp_path)
    migration_started = threading.Event()
    release_migration = threading.Event()

    def slow_claim(_thread_id, _owner_user_id):
        migration_started.set()
        release_migration.wait(1.0)
        return 0

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-filesystem-cancel", user_id="default")
    run_manager = RunManager()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=run_manager,
                run_store=None,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        )
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=paths),
        patch.object(paths, "claim_legacy_thread_dirs", side_effect=slow_claim),
    ):
        task = asyncio.create_task(
            threads._claim_legacy_thread_related_data(
                "thread-filesystem-cancel",
                "owner-a",
                request,
            )
        )
        await asyncio.to_thread(migration_started.wait, 1.0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert task.done() is False
        assert "thread-filesystem-cancel" in run_manager._deleting_threads
        release_migration.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "thread-filesystem-cancel" not in run_manager._deleting_threads
    assert (
        await thread_store.check_access(
            "thread-filesystem-cancel",
            "owner-a",
            require_existing=True,
        )
        is False
    )


@pytest.mark.asyncio
async def test_legacy_claim_validates_paths_before_reserving_or_claiming(tmp_path):
    claim_repo = SimpleNamespace(claim_legacy_by_thread=AsyncMock())
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-invalid-owner", user_id="default")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=RunManager(),
                run_store=claim_repo,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        )
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(ValueError, match="user_id"),
    ):
        await threads._claim_legacy_thread_related_data(
            "thread-invalid-owner",
            "bad@owner",
            request,
        )

    claim_repo.claim_legacy_by_thread.assert_not_awaited()
    row = await thread_store.get("thread-invalid-owner", user_id=None)
    assert row is not None
    assert row["user_id"] == "default"


@pytest.mark.asyncio
async def test_legacy_claim_rejects_active_run_before_reserving_owner(tmp_path):
    claim_repo = SimpleNamespace(claim_legacy_by_thread=AsyncMock())
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-active-claim", user_id="default")
    run_manager = RunManager()
    await run_manager.create("thread-active-claim", user_id="default")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=run_manager,
                run_store=claim_repo,
                run_event_store=None,
                feedback_repo=None,
                artifact_provenance_repo=None,
                round_state_store=None,
            )
        )
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await threads._claim_legacy_thread_related_data(
            "thread-active-claim",
            "owner-a",
            request,
        )

    assert exc_info.value.status_code == 409
    claim_repo.claim_legacy_by_thread.assert_not_awaited()
    row = await thread_store.get("thread-active-claim", user_id=None)
    assert row is not None
    assert row["user_id"] == "default"


@pytest.mark.asyncio
async def test_delete_thread_cancels_active_runs_and_cleans_streams(tmp_path):
    from app.gateway.routers.threads import delete_thread_data

    class AppState:
        pass

    class App:
        pass

    class Request:
        pass

    running = RunRecord(
        run_id="run-active",
        thread_id="thread-x",
        assistant_id=None,
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id="user-a",
    )
    finished = RunRecord(
        run_id="run-finished",
        thread_id="thread-x",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect=DisconnectMode.continue_,
        user_id="user-a",
    )

    request = Request()
    request.app = App()
    request.app.state = AppState()
    request.app.state.run_manager = SimpleNamespace(
        begin_thread_delete=AsyncMock(),
        list_by_thread=AsyncMock(return_value=[running, finished]),
        cancel=AsyncMock(return_value=True),
    )
    request.app.state.stream_bridge = SimpleNamespace(cleanup=AsyncMock())
    request.app.state.run_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=2))
    request.app.state.run_event_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=2))
    request.app.state.feedback_repo = SimpleNamespace(delete_by_thread=AsyncMock(return_value=2))
    request.app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    request.app.state.checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    request.app.state.round_state_store = None
    request.state = SimpleNamespace(user=SimpleNamespace(id="user-a"))

    await request.app.state.thread_store.create("thread-x", user_id="user-a")

    task = asyncio.create_task(asyncio.Event().wait())
    running.task = task

    async def cancel_run(run_id):
        assert run_id == running.run_id
        running.status = RunStatus.interrupted
        task.cancel()
        return True

    request.app.state.run_manager.cancel = AsyncMock(side_effect=cancel_run)

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        response = await delete_thread_data.__wrapped__("thread-x", request)

    assert response.success is True
    request.app.state.run_manager.begin_thread_delete.assert_awaited_once_with("thread-x")
    assert request.app.state.run_manager.list_by_thread.await_count == 2
    request.app.state.run_manager.cancel.assert_awaited_once_with("run-active")
    assert request.app.state.stream_bridge.cleanup.await_args_list == [
        (("run-active",),),
        (("run-finished",),),
    ]


@pytest.mark.asyncio
async def test_delete_thread_missing_owner_row_fails_closed():
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        run_manager=SimpleNamespace(list_by_thread=AsyncMock(return_value=[]), cancel=AsyncMock()),
    )

    with pytest.raises(HTTPException) as exc_info:
        await threads.delete_thread_data.__wrapped__("missing-thread", request)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_waits_for_exclusive_gate_before_writing_tombstone():
    thread_id = "delete-claim-race"
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create(thread_id, user_id="user-a")
    run_manager = RunManager()
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        run_manager=run_manager,
    )
    await run_manager.begin_thread_delete(thread_id)

    try:
        with pytest.raises(HTTPException) as exc_info:
            await threads.delete_thread_data.__wrapped__(thread_id, request)

        assert exc_info.value.status_code == 409
        row = await thread_store.get(thread_id, user_id="user-a")
        assert row is not None
        assert row["status"] == "idle"
    finally:
        await run_manager.end_thread_delete(thread_id)


def _delete_request(*, thread_store, checkpointer, run_manager, round_store=None):
    if not hasattr(run_manager, "begin_thread_delete"):
        run_manager.begin_thread_delete = AsyncMock()
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                checkpointer=checkpointer,
                run_manager=run_manager,
                stream_bridge=SimpleNamespace(cleanup=AsyncMock()),
                run_store=SimpleNamespace(delete_by_thread=AsyncMock(return_value=0)),
                run_event_store=SimpleNamespace(delete_by_thread=AsyncMock(return_value=0)),
                feedback_repo=SimpleNamespace(delete_by_thread=AsyncMock(return_value=0)),
                artifact_provenance_repo=None,
                round_state_store=round_store,
            )
        ),
        state=SimpleNamespace(user=SimpleNamespace(id="user-a")),
        headers={},
    )


@pytest.mark.asyncio
async def test_delete_thread_memory_backend_skips_absent_feedback_repo(tmp_path):
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-x", user_id="user-a")
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        run_manager=SimpleNamespace(
            begin_thread_delete=AsyncMock(),
            list_by_thread=AsyncMock(return_value=[]),
            cancel=AsyncMock(),
        ),
    )
    request.app.state.feedback_repo = None

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        response = await threads.delete_thread_data.__wrapped__("thread-x", request)

    assert response.success is True
    assert await thread_store.get("thread-x", user_id="user-a") is None


@pytest.mark.asyncio
async def test_delete_thread_keeps_owner_barrier_when_checkpoint_delete_fails(tmp_path):
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-x", user_id="user-a")
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock(side_effect=RuntimeError("checkpoint busy")))
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=checkpointer,
        run_manager=SimpleNamespace(list_by_thread=AsyncMock(return_value=[]), cancel=AsyncMock()),
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await threads.delete_thread_data.__wrapped__("thread-x", request)

    assert exc_info.value.status_code == 500
    row = await thread_store.get("thread-x", user_id="user-a")
    assert row is not None
    assert row["status"] == "deleting"


@pytest.mark.asyncio
async def test_delete_thread_retry_reuses_closed_barrier_after_checkpoint_failure(
    tmp_path,
):
    thread_id = "retry-delete"
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create(thread_id, user_id="user-a")
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock(side_effect=[RuntimeError("checkpoint busy"), None]))
    run_manager = RunManager()
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=checkpointer,
        run_manager=run_manager,
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as first_error,
    ):
        await threads.delete_thread_data.__wrapped__(thread_id, request)

    assert first_error.value.status_code == 500
    assert thread_id not in run_manager._deleting_threads

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        response = await threads.delete_thread_data.__wrapped__(thread_id, request)

    assert response.success is True
    assert checkpointer.adelete_thread.await_count == 2
    assert await thread_store.get(thread_id, user_id="user-a") is None


@pytest.mark.asyncio
async def test_delete_thread_retry_converges_after_cancelled_repository_cleanup(tmp_path):
    thread_id = "cancelled-delete-retry"
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create(thread_id, user_id="user-a")
    run_manager = RunManager()
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        run_manager=run_manager,
    )
    request.app.state.run_event_store = SimpleNamespace(
        delete_by_thread=AsyncMock(side_effect=[asyncio.CancelledError(), 0]),
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(asyncio.CancelledError),
    ):
        await threads.delete_thread_data.__wrapped__(thread_id, request)

    row = await thread_store.get(thread_id, user_id="user-a")
    assert row is not None
    assert row["status"] == "deleting"
    assert thread_id not in run_manager._deleting_threads

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        response = await threads.delete_thread_data.__wrapped__(thread_id, request)

    assert response.success is True
    assert await thread_store.get(thread_id, user_id="user-a") is None


@pytest.mark.parametrize(
    "repository_name",
    [
        "run_event_store",
        "run_store",
        "feedback_repo",
        "artifact_provenance_repo",
    ],
)
@pytest.mark.asyncio
async def test_delete_thread_keeps_owner_barrier_when_owned_cleanup_fails(
    tmp_path,
    repository_name,
):
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-x", user_id="user-a")
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        run_manager=SimpleNamespace(
            begin_thread_delete=AsyncMock(),
            list_by_thread=AsyncMock(return_value=[]),
            cancel=AsyncMock(),
        ),
    )
    repository = SimpleNamespace(delete_by_thread=AsyncMock(side_effect=RuntimeError("storage busy")))
    setattr(request.app.state, repository_name, repository)

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await threads.delete_thread_data.__wrapped__("thread-x", request)

    assert exc_info.value.status_code == 500
    row = await thread_store.get("thread-x", user_id="user-a")
    assert row is not None
    assert row["status"] == "deleting"


@pytest.mark.asyncio
async def test_delete_thread_keeps_owner_barrier_when_checkpointer_is_unavailable(tmp_path):
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-x", user_id="user-a")
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=None,
        run_manager=SimpleNamespace(list_by_thread=AsyncMock(return_value=[]), cancel=AsyncMock()),
    )

    with (
        patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await threads.delete_thread_data.__wrapped__("thread-x", request)

    assert exc_info.value.status_code == 500
    row = await thread_store.get("thread-x", user_id="user-a")
    assert row is not None
    assert row["status"] == "deleting"


@pytest.mark.asyncio
async def test_delete_thread_keeps_owner_barrier_when_local_task_does_not_stop(tmp_path):
    release = asyncio.Event()
    started = asyncio.Event()

    async def stubborn_task():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(stubborn_task())
    await started.wait()
    record = RunRecord(
        run_id="run-active",
        thread_id="thread-x",
        assistant_id=None,
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id="user-a",
    )
    record.task = task

    async def cancel_run(run_id):
        assert run_id == record.run_id
        task.cancel()
        record.status = RunStatus.interrupted
        return True

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-x", user_id="user-a")
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=checkpointer,
        run_manager=SimpleNamespace(
            list_by_thread=AsyncMock(return_value=[record]),
            cancel=AsyncMock(side_effect=cancel_run),
        ),
    )

    try:
        with (
            patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)),
            patch("app.gateway.routers.threads._DELETE_RUN_DRAIN_TIMEOUT_SECONDS", 0.01, create=True),
            pytest.raises(HTTPException) as exc_info,
        ):
            await threads.delete_thread_data.__wrapped__("thread-x", request)

        assert exc_info.value.status_code == 409
        assert await thread_store.get("thread-x", user_id="user-a") is not None
        checkpointer.adelete_thread.assert_not_awaited()
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_delete_thread_clears_owner_scoped_round_state(tmp_path):
    from deerflow.persistence.round_state import MemoryRoundStateStore

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-x", user_id="user-a")
    round_store = MemoryRoundStateStore()
    await round_store.bind_run(thread_id="thread-x", run_id="run-a", user_id="user-a")
    request = _delete_request(
        thread_store=thread_store,
        checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        run_manager=SimpleNamespace(list_by_thread=AsyncMock(return_value=[]), cancel=AsyncMock()),
        round_store=round_store,
    )

    with patch("app.gateway.routers.threads.get_paths", return_value=Paths(tmp_path)):
        response = await threads.delete_thread_data.__wrapped__("thread-x", request)

    assert response.success is True
    assert await round_store.list_by_thread("thread-x", user_id="user-a") == []
    assert await thread_store.get("thread-x", user_id="user-a") is None


@pytest.mark.asyncio
async def test_search_threads_syncs_latest_worker_lost_thread_status() -> None:
    expected_user_id = str(_HISTORY_USER_ID)
    thread_id = "worker-lost-search-thread"

    class RecordingThreadStore:
        def __init__(self) -> None:
            self.update_calls: list[tuple[str, str, str | None]] = []

        async def search(self, *, metadata=None, status=None, limit=100, offset=0, user_id=None):
            assert user_id == expected_user_id
            return [
                {
                    "thread_id": thread_id,
                    "status": "running",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "metadata": {},
                }
            ]

        async def update_status(self, requested_thread_id: str, status: str, *, user_id: str | None = None):
            self.update_calls.append((requested_thread_id, status, user_id))

    class RecordingRunManager:
        async def list_by_thread(self, requested_thread_id: str, *, user_id: str | None = None, limit: int = 100):
            assert requested_thread_id == thread_id
            assert user_id == expected_user_id
            assert limit == 1
            return [
                RunRecord(
                    run_id="run-worker-lost",
                    thread_id=thread_id,
                    assistant_id=None,
                    status=RunStatus.error,
                    terminal_reason="worker_lost",
                    on_disconnect=DisconnectMode.continue_,
                    user_id=expected_user_id,
                )
            ]

    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    thread_store = RecordingThreadStore()
    app.state.thread_store = thread_store
    app.state.run_manager = RecordingRunManager()
    request = SimpleNamespace(app=app, state=SimpleNamespace(user=_make_user(_HISTORY_USER_ID)))

    responses = await call_unwrapped(threads.search_threads, threads.ThreadSearchRequest(metadata={}), request)
    filtered_responses = await call_unwrapped(threads.search_threads, threads.ThreadSearchRequest(metadata={}, status="running"), request)

    assert [response.status for response in responses] == ["error"]
    assert filtered_responses == []
    assert thread_store.update_calls == [(thread_id, "error", expected_user_id), (thread_id, "error", expected_user_id)]


@pytest.mark.asyncio
async def test_list_runs_syncs_thread_error_for_latest_worker_lost() -> None:
    expected_user_id = str(_HISTORY_USER_ID)
    thread_id = "worker-lost-thread"

    class RecordingRunManager:
        async def list_by_thread(
            self,
            requested_thread_id: str,
            *,
            user_id: str | None = None,
            limit: int = 100,
        ):
            assert requested_thread_id == thread_id
            assert user_id == expected_user_id
            assert limit == 100
            return [
                RunRecord(
                    run_id="run-worker-lost",
                    thread_id=thread_id,
                    assistant_id=None,
                    status=RunStatus.error,
                    terminal_reason="worker_lost",
                    on_disconnect=DisconnectMode.continue_,
                    user_id=expected_user_id,
                )
            ]

    class RecordingThreadStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None]] = []

        async def update_status(self, requested_thread_id: str, status: str, *, user_id: str | None = None):
            self.calls.append((requested_thread_id, status, user_id))

    app = make_authed_test_app(user_factory=lambda: _make_user(_HISTORY_USER_ID))
    thread_store = RecordingThreadStore()
    app.state.run_manager = RecordingRunManager()
    app.state.thread_store = thread_store
    request = SimpleNamespace(app=app, state=SimpleNamespace(user=_make_user(_HISTORY_USER_ID)))

    responses = await call_unwrapped(thread_runs.list_runs, thread_id, request)

    assert [response.run_id for response in responses] == ["run-worker-lost"]
    assert thread_store.calls == [(thread_id, "error", expected_user_id)]
