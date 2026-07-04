from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.auth_disabled import AUTH_SOURCE_SESSION
from app.gateway.routers import feedback

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
