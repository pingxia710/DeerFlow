from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from sqlalchemy.exc import IntegrityError

from app.gateway.auth.models import User
from app.gateway.auth_disabled import AUTH_SOURCE_SESSION
from app.gateway.authz import AuthContext, Permissions
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
from app.gateway.routers import feedback
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime import RunManager

USER_ID = UUID("55555555-5555-5555-5555-555555555555")


def _make_app(feedback_repo):
    user = User(id=USER_ID, email="feedback-router@example.com", password_hash="x", system_role="user")
    app = make_authed_test_app(user_factory=lambda: user)

    @app.middleware("http")
    async def _stamp_auth_source(request, call_next):
        request.state.auth_source = AUTH_SOURCE_SESSION
        return await call_next(request)

    app.include_router(feedback.router)
    app.state.feedback_repo = feedback_repo
    return app


def test_list_feedback_scopes_to_current_user():
    repo = MagicMock()
    repo.list_by_run = AsyncMock(return_value=[])
    app = _make_app(repo)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/feedback")

    assert response.status_code == 200
    assert response.json() == []
    repo.list_by_run.assert_awaited_once_with("thread-1", "run-1", user_id=str(USER_ID))


def test_delete_feedback_scopes_lookup_and_delete_to_current_user():
    repo = MagicMock()
    repo.get = AsyncMock(return_value={"feedback_id": "fb-1", "thread_id": "thread-1", "run_id": "run-1"})
    repo.delete = AsyncMock(return_value=True)
    app = _make_app(repo)

    with TestClient(app) as client:
        response = client.delete("/api/threads/thread-1/runs/run-1/feedback/fb-1")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    repo.get.assert_awaited_once_with("fb-1", user_id=str(USER_ID))
    repo.delete.assert_awaited_once_with("fb-1", user_id=str(USER_ID))


def _internal_owner_request(*, feedback_repo, run_store=None):
    return SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
        state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE)),
        app=SimpleNamespace(state=SimpleNamespace(feedback_repo=feedback_repo, run_store=run_store)),
    )


def test_upsert_feedback_uses_internal_owner_header():
    repo = MagicMock()
    repo.upsert = AsyncMock(
        return_value={
            "feedback_id": "fb-1",
            "run_id": "run-1",
            "thread_id": "thread-1",
            "user_id": "owner-1",
            "rating": 1,
        }
    )
    run_store = MagicMock()
    run_store.get = AsyncMock(return_value={"run_id": "run-1", "thread_id": "thread-1", "user_id": "owner-1"})
    request = _internal_owner_request(feedback_repo=repo, run_store=run_store)

    async def _scenario():
        return await call_unwrapped(
            feedback.upsert_feedback,
            "thread-1",
            "run-1",
            feedback.FeedbackUpsertRequest(rating=1),
            request,
        )

    response = asyncio.run(_scenario())

    assert response["user_id"] == "owner-1"
    run_store.get.assert_awaited_once_with("run-1", user_id="owner-1")
    repo.upsert.assert_awaited_once_with(
        run_id="run-1",
        thread_id="thread-1",
        rating=1,
        user_id="owner-1",
        comment=None,
    )


def test_upsert_feedback_denies_foreign_run_owner():
    repo = MagicMock()
    repo.upsert = AsyncMock()
    run_store = MagicMock()
    run_store.get = AsyncMock(return_value={"run_id": "run-1", "thread_id": "thread-1", "user_id": "owner-2"})
    request = _internal_owner_request(feedback_repo=repo, run_store=run_store)

    async def _scenario():
        return await call_unwrapped(
            feedback.upsert_feedback,
            "thread-1",
            "run-1",
            feedback.FeedbackUpsertRequest(rating=1),
            request,
        )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_scenario())

    assert exc.value.status_code == 404
    run_store.get.assert_awaited_once_with("run-1", user_id="owner-1")
    repo.upsert.assert_not_awaited()


def test_feedback_stats_scopes_to_current_user():
    repo = MagicMock()
    repo.aggregate_by_run = AsyncMock(return_value={"run_id": "run-1", "total": 0, "positive": 0, "negative": 0})
    app = _make_app(repo)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/feedback/stats")

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "total": 0, "positive": 0, "negative": 0}
    repo.aggregate_by_run.assert_awaited_once_with("thread-1", "run-1", user_id=str(USER_ID))


def test_create_feedback_maps_duplicate_to_conflict():
    repo = MagicMock()
    repo.create = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("unique")))
    run_store = MagicMock()
    run_store.get = AsyncMock(return_value={"run_id": "run-1", "thread_id": "thread-1", "user_id": "owner-1"})
    request = _internal_owner_request(feedback_repo=repo, run_store=run_store)

    async def _scenario():
        return await call_unwrapped(
            feedback.create_feedback,
            "thread-1",
            "run-1",
            feedback.FeedbackCreateRequest(rating=1),
            request,
        )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_scenario())

    assert exc.value.status_code == 409
    assert exc.value.detail == "Feedback already exists for this run"


@pytest.mark.anyio
async def test_concurrent_create_feedback_returns_success_and_conflict(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
    from deerflow.persistence.feedback import FeedbackRepository

    url = f"sqlite+aiosqlite:///{tmp_path / 'feedback-router.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        repo = FeedbackRepository(get_session_factory())
        run_store = MagicMock()
        run_store.get = AsyncMock(return_value={"run_id": "run-1", "thread_id": "thread-1", "user_id": "owner-1"})
        request = _internal_owner_request(feedback_repo=repo, run_store=run_store)

        results = await asyncio.gather(
            *(
                call_unwrapped(
                    feedback.create_feedback,
                    "thread-1",
                    "run-1",
                    feedback.FeedbackCreateRequest(rating=1),
                    request,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )

        created = [result for result in results if isinstance(result, dict)]
        conflicts = [result for result in results if isinstance(result, HTTPException)]
        assert len(created) == 1
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409
        assert len(await repo.list_by_run("thread-1", "run-1", user_id="owner-1")) == 1
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_feedback_write_finishes_before_thread_delete_cleanup():
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    class BlockingFeedbackRepo:
        def __init__(self):
            self.rows = []

        async def upsert(self, **kwargs):
            write_started.set()
            await release_write.wait()
            row = {"feedback_id": "fb-1", "message_id": None, "created_at": "", **kwargs}
            self.rows.append(row)
            return row

        async def delete_by_thread(self, thread_id, *, user_id):
            self.rows = [row for row in self.rows if row["thread_id"] != thread_id or row["user_id"] != user_id]

    user = User(id=USER_ID, email="feedback-router@example.com", password_hash="x", system_role="user")
    user_id = str(USER_ID)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("thread-1", user_id=user_id)
    run_manager = RunManager()
    repo = BlockingFeedbackRepo()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(
            user=user,
            auth=AuthContext(user=user, permissions=[Permissions.THREADS_WRITE]),
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=thread_store,
                run_manager=run_manager,
                run_store=SimpleNamespace(
                    get=AsyncMock(
                        return_value={
                            "run_id": "run-1",
                            "thread_id": "thread-1",
                            "user_id": user_id,
                        }
                    )
                ),
                feedback_repo=repo,
            )
        ),
    )

    write_task = asyncio.create_task(
        feedback.upsert_feedback(
            thread_id="thread-1",
            run_id="run-1",
            body=feedback.FeedbackUpsertRequest(rating=1),
            request=request,
        )
    )
    await write_started.wait()

    async def delete_after_writes_drain():
        await run_manager.begin_thread_delete("thread-1")
        await repo.delete_by_thread("thread-1", user_id=user_id)

    delete_task = asyncio.create_task(delete_after_writes_drain())
    await asyncio.sleep(0)
    assert not delete_task.done()

    release_write.set()
    await write_task
    await delete_task
    assert repo.rows == []
