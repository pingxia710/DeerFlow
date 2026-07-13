"""Target-state checkpoint owner isolation contracts.

LangGraph checkpointers are physically keyed by an owner-qualified thread id,
checkpoint namespace, and checkpoint id. These tests pin that boundary at the
gateway and worker call sites.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from app.gateway.auth.models import User

USER_A_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _request_for_user(checkpointer):
    user = User(id=USER_A_ID, email="owner-a@example.com", password_hash="x", system_role="user")
    return SimpleNamespace(
        headers={},
        state=SimpleNamespace(user=user),
        app=SimpleNamespace(state=SimpleNamespace(checkpointer=checkpointer)),
    )


def test_pending_migration_apply_checkpoint_to_run_config_passes_owner_namespace():
    from app.gateway.services import apply_checkpoint_to_run_config

    class FakeCheckpointer:
        def __init__(self):
            self.seen_config = None

        async def aget_tuple(self, config):
            self.seen_config = config
            return SimpleNamespace(config=config, checkpoint={"channel_values": {}})

    checkpointer = FakeCheckpointer()
    body = SimpleNamespace(checkpoint=None, checkpoint_id="ckpt-b")
    config = {"configurable": {"thread_id": "shared-thread"}}

    asyncio.run(apply_checkpoint_to_run_config(config, body=body, thread_id="shared-thread", request=_request_for_user(checkpointer)))

    configurable = checkpointer.seen_config["configurable"]
    assert configurable.get("user_id") == str(USER_A_ID)


def test_pending_migration_thread_history_passes_owner_namespace_to_checkpointer():
    from _router_auth_helpers import make_authed_test_app
    from fastapi.testclient import TestClient

    from app.gateway.routers import threads

    class FakeCheckpointer:
        def __init__(self):
            self.seen_config = None

        async def alist(self, config, limit=None):
            self.seen_config = config
            if False:
                yield None

    checkpointer = FakeCheckpointer()
    app = make_authed_test_app(user_factory=lambda: User(id=USER_A_ID, email="owner-a@example.com", password_hash="x", system_role="user"))
    app.state.checkpointer = checkpointer
    app.include_router(threads.router)

    with TestClient(app) as client:
        response = client.post("/api/threads/shared-thread/history", json={"limit": 1})

    assert response.status_code == 200
    assert checkpointer.seen_config["configurable"].get("user_id") == str(USER_A_ID)


def test_pending_migration_worker_title_sync_passes_owner_namespace_to_checkpointer():
    from deerflow.runtime.runs.worker import _sync_checkpoint_title_to_thread_store

    class FakeCheckpointer:
        def __init__(self):
            self.seen_config = None

        async def aget_tuple(self, config):
            self.seen_config = config
            return None

    class FakeThreadStore:
        async def get(self, *args, **kwargs):
            raise AssertionError("thread store should not be reached when no checkpoint exists")

        async def update_display_name(self, *args, **kwargs):
            raise AssertionError("thread store should not be reached when no checkpoint exists")

    checkpointer = FakeCheckpointer()

    asyncio.run(_sync_checkpoint_title_to_thread_store(checkpointer, FakeThreadStore(), "shared-thread", user_id=str(USER_A_ID)))

    assert checkpointer.seen_config["configurable"].get("user_id") == str(USER_A_ID)
