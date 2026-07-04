from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.runtime import RunRecord, RunStatus
from deerflow.runtime.artifacts import build_artifact_index
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

_USER_ID = UUID("55555555-5555-5555-5555-555555555555")


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
    )


def test_build_artifact_index_from_task_event_artifact_refs() -> None:
    events = [
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "user_id": "user-1",
            "event_type": "task_completed",
            "category": "message",
            "seq": 7,
            "created_at": "2026-01-01T00:00:00Z",
            "content": {
                "task_id": "task-1",
                "artifact_refs": ["/mnt/user-data/outputs/report.md", "/mnt/user-data/outputs/report.md"],
                "action_result": {"evidence_refs": ["/mnt/user-data/outputs/evidence.json"]},
            },
            "metadata": {"caller": "task_event"},
        }
    ]

    index = build_artifact_index(events)

    assert [entry["virtual_path"] for entry in index] == [
        "/mnt/user-data/outputs/report.md",
        "/mnt/user-data/outputs/evidence.json",
    ]
    assert index[0]["task_id"] == "task-1"
    assert index[0]["source_tool"] == "task"
    assert index[0]["source_event_seq"] == 7
    assert index[0]["provenance"] == {
        "kind": "runtime_observed",
        "store": "run_events",
        "caller": "task_event",
    }


def test_run_journal_records_presented_artifacts_from_command() -> None:
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, user_id="user-1", flush_threshold=100)
    tool_run_id = uuid4()

    journal.on_tool_start(
        {"name": "present_files"},
        "",
        run_id=tool_run_id,
        tags=["lead_agent"],
        metadata={"langgraph_node": "tools"},
    )
    journal.on_tool_end(
        Command(
            update={
                "artifacts": ["/mnt/user-data/outputs/report.md"],
                "messages": [ToolMessage("Successfully presented files", tool_call_id="tc-1")],
            }
        ),
        run_id=tool_run_id,
    )
    asyncio.run(journal.flush())

    events = asyncio.run(store.list_events("thread-1", "run-1"))
    index = build_artifact_index(events)

    assert len(index) == 1
    assert index[0]["virtual_path"] == "/mnt/user-data/outputs/report.md"
    assert index[0]["source_event_type"] == "artifact.presented"
    assert index[0]["source_tool"] == "present_files"
    assert index[0]["source_node"] == "tools"
    assert index[0]["user_id"] == "user-1"


def test_list_run_artifacts_endpoint_returns_runtime_observed_index(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "report.md"
    artifact_payload = b"runtime observed report"
    artifact_path.write_bytes(artifact_payload)
    active_path = tmp_path / "page.html"
    active_payload = b"<html><script>alert(1)</script></html>"
    active_path.write_bytes(active_payload)
    paths = {
        "/mnt/user-data/outputs/report.md": artifact_path,
        "/mnt/user-data/outputs/page.html": active_path,
    }
    monkeypatch.setattr(thread_runs, "resolve_thread_virtual_path", lambda _thread_id, path, **_kwargs: paths[path])

    app = make_authed_test_app(
        user_factory=lambda: User(
            id=_USER_ID,
            email="user@example.com",
            password_hash="x",
            system_role="user",
        )
    )
    app.include_router(thread_runs.router)

    class EventStore:
        def __init__(self) -> None:
            self.list_events = AsyncMock(
                return_value=[
                    {
                        "thread_id": "thread-1",
                        "run_id": "run-1",
                        "event_type": "artifact.presented",
                        "category": "artifact",
                        "seq": 3,
                        "content": {"artifact_refs": ["/mnt/user-data/outputs/report.md", "/mnt/user-data/outputs/page.html"]},
                        "metadata": {"caller": "lead_agent", "source_tool": "present_files"},
                    }
                ]
            )

    event_store = EventStore()
    app.state.run_event_store = event_store
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=_run_record()))

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/artifacts")

    assert response.status_code == 200
    artifact = response.json()[0]
    assert artifact["virtual_path"] == "/mnt/user-data/outputs/report.md"
    assert artifact["display_policy"] == "inline"
    assert artifact["sha256"] == hashlib.sha256(artifact_payload).hexdigest()
    assert artifact["size_bytes"] == len(artifact_payload)
    active_artifact = response.json()[1]
    assert active_artifact["virtual_path"] == "/mnt/user-data/outputs/page.html"
    assert active_artifact["display_policy"] == "attachment"
    assert active_artifact["sha256"] == hashlib.sha256(active_payload).hexdigest()
    assert active_artifact["size_bytes"] == len(active_payload)
    event_store.list_events.assert_awaited_once_with(
        "thread-1",
        "run-1",
        event_types=["artifact.presented", "task_completed", "task_failed", "task_cancelled", "task_timed_out"],
        limit=500,
        user_id=str(_USER_ID),
    )
