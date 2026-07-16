import asyncio
from pathlib import Path

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import HTTPException
from starlette.requests import Request

import app.gateway.routers.thread_runs as thread_runs


def _request(user_id: str = "owner") -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = type("User", (), {"id": user_id, "system_role": "user"})()
    return request


class _Paths:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        assert thread_id == "thread-1"
        assert user_id == "owner"
        return self.workspace


class _RoundStore:
    def __init__(self, lane: dict) -> None:
        self.lane = lane

    async def get_task_lane(self, **kwargs):
        assert kwargs == {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "user_id": "owner",
        }
        return self.lane


def test_command_room_plan_artifact_reads_only_the_fixed_spec_path(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    spec = workspace / "command-room-loop" / "thread-1" / "01-planning" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Approved plan\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside content", encoding="utf-8")
    lane = {
        "status": "completed",
        "handoff": {
            "container_artifact_kind": "spec",
            "container_artifact_written": True,
            "container_artifact_path": str(outside),
        },
    }
    monkeypatch.setattr(thread_runs, "get_paths", lambda: _Paths(workspace))
    monkeypatch.setattr(thread_runs, "get_round_state_store", lambda _request: _RoundStore(lane))

    response = asyncio.run(
        call_unwrapped(
            thread_runs.get_command_room_plan_artifact,
            "thread-1",
            "run-1",
            "task-1",
            _request(),
        )
    )

    assert response.body == b"# Approved plan\n"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_command_room_plan_artifact_rejects_unwritten_or_non_plan_tasks(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(thread_runs, "get_paths", lambda: _Paths(workspace))
    monkeypatch.setattr(
        thread_runs,
        "get_round_state_store",
        lambda _request: _RoundStore(
            {
                "status": "completed",
                "handoff": {
                    "container_artifact_kind": "execution",
                    "container_artifact_written": True,
                },
            }
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            call_unwrapped(
                thread_runs.get_command_room_plan_artifact,
                "thread-1",
                "run-1",
                "task-1",
                _request(),
            )
        )

    assert exc_info.value.status_code == 404


def test_command_room_plan_artifact_reads_the_owner_package_spec(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    spec = workspace / "command-room-loop" / "thread-1" / "packages" / "package-a" / "01-planning" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Package A plan\n", encoding="utf-8")
    lane = {
        "status": "completed",
        "handoff": {
            "container_artifact_kind": "spec",
            "container_artifact_written": True,
            "work_package_id": "package-a",
        },
    }
    monkeypatch.setattr(thread_runs, "get_paths", lambda: _Paths(workspace))
    monkeypatch.setattr(thread_runs, "get_round_state_store", lambda _request: _RoundStore(lane))

    response = asyncio.run(
        call_unwrapped(
            thread_runs.get_command_room_plan_artifact,
            "thread-1",
            "run-1",
            "task-1",
            _request(),
        )
    )

    assert response.body == b"# Package A plan\n"
