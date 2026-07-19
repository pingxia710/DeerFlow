"""Test-only controls for the isolated WP-1 E3 runner.

This module is imported only by ``scripts/run_e3_gateway.py`` after that
launcher has proved it is a loopback process rooted in a fresh E3 directory.
It deliberately has no production import path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from pydantic import PrivateAttr

_MODES = frozenset({"r-a", "r-b", "wf"})
_TASK_ID = "e3-r-child"
_MUTATING_ROUND_STORE_METHODS = frozenset(
    {
        "bind_run",
        "record_task_events",
        "claim_background_wake",
        "renew_background_wake_claim",
        "release_background_wake_claim",
        "set_round_status",
    }
)


def _within(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def loopback(host: str) -> bool:
    try:
        return socket.getaddrinfo(host, None)[0][4][0] in {"127.0.0.1", "::1"}
    except socket.gaierror:
        return False


def validate_mount(*, environ: Mapping[str, str] | None = None, host: str) -> tuple[Path, str]:
    """Return the isolated root/mode or reject before importing the Gateway."""
    env = os.environ if environ is None else environ
    if env.get("DEERFLOW_E3_TEST") != "1":
        raise RuntimeError("E3 controls require DEERFLOW_E3_TEST=1")
    root = Path(env.get("DEERFLOW_E3_ROOT", "")).resolve()
    home = env.get("DEER_FLOW_HOME", "")
    mode = env.get("DEERFLOW_E3_MODE", "")
    if not root.is_dir() or not _within(home, root / "home"):
        raise RuntimeError("E3 controls require DEER_FLOW_HOME below E3_ROOT/home")
    if not loopback(host) or env.get("DEERFLOW_E3_BIND_HOST") != host:
        raise RuntimeError("E3 controls require an explicit loopback bind host")
    if mode not in _MODES:
        raise RuntimeError("E3 controls require one of r-a, r-b, wf")
    return root, mode


def redact_public(value: Any) -> Any:
    """Keep controller evidence on the public, allowlisted side of the seam."""
    forbidden = {"error", "handoff", "last_status", "prompt", "result", "claim_id"}
    if isinstance(value, Mapping):
        return {str(key): redact_public(item) for key, item in value.items() if str(key).lower() not in forbidden}
    if isinstance(value, list):
        return [redact_public(item) for item in value]
    if isinstance(value, str):
        return value.replace("http_503", "[redacted]")
    return value


def write_control(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


class E3ChairModel(BaseChatModel):
    """A deterministic Chair used only to drive the real task transport."""

    _ignored: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self._ignored = kwargs

    @property
    def _llm_type(self) -> str:
        return "deerflow-e3-chair"

    def _match(self, messages: list[BaseMessage]) -> AIMessage:
        text = "\n".join(_message_text(message) for message in messages)
        if any(message.name == "command_room_background_result" for message in messages):
            return AIMessage(content=f"E3_WAKE_ACK_{os.environ.get('DEERFLOW_E3_NONCE', 'missing')}")
        if any(message.type == "tool" and getattr(message, "name", None) == "task" for message in messages):
            return AIMessage(content="E3 child task admitted.")
        if "E3_CHILD_" in text:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": _TASK_ID,
                        "name": "task",
                        "args": {
                            "description": "E3 real one-shot child",
                            "prompt": "Return the exact text E3 child completed.",
                            "subagent_type": "general-purpose",
                        },
                    }
                ],
            )
        return AIMessage(content="E3 metadata.")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._match(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        turn = self._match(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=turn.content, tool_calls=turn.tool_calls))

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self


@dataclass
class E3ControlState:
    root: Path
    mode: str
    wake_start_calls: int = 0
    child_observations: list[dict[str, bool]] = field(default_factory=list)
    wf_jobs: dict[str, dict[str, str]] = field(default_factory=dict)
    zero_write_calls: list[str] = field(default_factory=list)
    lane_digest_before: str | None = None
    lane_digest_after: str | None = None
    gate: asyncio.Event = field(default_factory=asyncio.Event)


def _command_paths(args: tuple[Any, ...]) -> list[str]:
    values = [str(value) for value in args]
    paths: list[str] = []
    for index, value in enumerate(values[:-1]):
        if value in {"--cd", "--add-dir"}:
            paths.append(values[index + 1])
    return paths


class _ZeroWriteRoundStore:
    def __init__(self, wrapped: Any, state: E3ControlState) -> None:
        self._wrapped = wrapped
        self._state = state

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._wrapped, name)
        if name not in _MUTATING_ROUND_STORE_METHODS or not callable(target):
            return target

        async def blocked(*args: Any, **kwargs: Any) -> Any:
            self._state.zero_write_calls.append(name)
            raise AssertionError(f"wake-facts GET attempted a write through {name}")

        return blocked


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def _router(state: E3ControlState) -> APIRouter:
    router = APIRouter(prefix="/api/test-only/e3", tags=["test-only-e3"])

    @router.post("/wf/terminal-job")
    async def wf_terminal_job(request: Request) -> dict[str, str]:
        from app.gateway.path_utils import get_request_storage_user_id
        from deerflow.runtime.background_tasks import CommandRoomBackgroundJob, CommandRoomBackgroundOutcome

        user_id = get_request_storage_user_id(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="E3 WF requires an authenticated temporary owner")
        nonce = uuid.uuid4().hex
        thread_id, run_id, task_id = (f"e3-wf-{nonce}", f"e3-wf-run-{nonce}", f"e3-wf-task-{nonce}")
        app_state = request.app.state
        await app_state.thread_store.create(thread_id, assistant_id="command-room", user_id=user_id, metadata={})
        await app_state.run_store.put(
            run_id,
            thread_id=thread_id,
            assistant_id="command-room",
            user_id=user_id,
            status="success",
            created_at="2026-07-17T00:00:00+00:00",
            metadata={"completed_at": "2026-07-17T00:00:01+00:00"},
        )
        round_row = await app_state.round_state_store.bind_run(thread_id=thread_id, run_id=run_id, user_id=user_id)
        await app_state.run_event_store.put_batch(
            [
                {
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "event_type": "llm.human.input",
                    "category": "message",
                    "content": HumanMessage(content="E3 completed child evidence", id=f"{nonce}-human").model_dump(),
                    "metadata": {"caller": "command-room"},
                    "created_at": "2026-07-17T00:00:00+00:00",
                    "user_id": user_id,
                }
            ]
        )

        async def execute() -> CommandRoomBackgroundOutcome:
            return CommandRoomBackgroundOutcome(status="completed", result=f"E3_WF_{nonce}")

        await app_state.command_room_background_service.bind(request).dispatch(
            CommandRoomBackgroundJob(
                thread_id=thread_id,
                source_run_id=run_id,
                task_id=task_id,
                description="E3 completed child",
                subagent_type="general-purpose",
                execute=execute,
            )
        )
        state.wf_jobs[nonce] = {
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": task_id,
            "user_id": user_id,
            "round_id": str(round_row["round_id"]),
        }
        return {"thread_id": thread_id, "run_id": run_id, "task_id": task_id, "nonce": nonce}

    @router.get("/wf/status/{nonce}")
    async def wf_status(nonce: str, request: Request) -> dict[str, Any]:
        from app.gateway.path_utils import get_request_storage_user_id

        job = state.wf_jobs.get(nonce)
        if job is None or get_request_storage_user_id(request) != job["user_id"]:
            raise HTTPException(status_code=404, detail="E3 WF job not found")
        lane = await request.app.state.round_state_store.get_task_lane(thread_id=job["thread_id"], run_id=job["run_id"], task_id=job["task_id"], user_id=job["user_id"])
        handoff = lane.get("handoff") if isinstance(lane, Mapping) else {}
        background = handoff.get("background_recovery") if isinstance(handoff, Mapping) else {}
        wake = background.get("wake") if isinstance(background, Mapping) else {}
        return {
            **{key: job[key] for key in ("thread_id", "run_id", "task_id")},
            "round_id": job["round_id"],
            "wake_failed": isinstance(wake, Mapping) and wake.get("state") == "failed",
            "wake_attempts": wake.get("attempts") if isinstance(wake, Mapping) else None,
            "wake_start_calls": state.wake_start_calls,
        }

    @router.post("/wf/arm-zero-write/{nonce}")
    async def wf_arm_zero_write(nonce: str, request: Request) -> dict[str, str]:
        job = state.wf_jobs.get(nonce)
        if job is None:
            raise HTTPException(status_code=404, detail="E3 WF job not found")
        store = request.app.state.round_state_store
        lane = await store.get_task_lane(thread_id=job["thread_id"], run_id=job["run_id"], task_id=job["task_id"], user_id=job["user_id"])
        state.lane_digest_before = _digest(lane)
        request.app.state.round_state_store = _ZeroWriteRoundStore(store, state)
        return {"armed": "true"}

    @router.get("/wf/zero-write/{nonce}")
    async def wf_zero_write(nonce: str, request: Request) -> dict[str, Any]:
        job = state.wf_jobs.get(nonce)
        if job is None:
            raise HTTPException(status_code=404, detail="E3 WF job not found")
        store = request.app.state.round_state_store
        wrapped = getattr(store, "_wrapped", store)
        lane = await wrapped.get_task_lane(thread_id=job["thread_id"], run_id=job["run_id"], task_id=job["task_id"], user_id=job["user_id"])
        state.lane_digest_after = _digest(lane)
        return {
            "mutator_calls": len(state.zero_write_calls),
            "lane_digest_unchanged": state.lane_digest_before == state.lane_digest_after,
        }

    @router.get("/r/status")
    async def r_status() -> dict[str, Any]:
        observer_path = state.root / "control" / "child-observer.json"
        if observer_path.is_file():
            payload = json.loads(observer_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        observation = state.child_observations[0] if state.child_observations else {}
        return {"child_started": len(state.child_observations), **observation}

    return router


def mount_controls(app: Any, *, root: Path, mode: str) -> E3ControlState:
    """Mount controls after launcher validation; never call this from production."""
    from app.gateway import command_room_background as background

    state = E3ControlState(root=root, mode=mode)
    app.state.e3_control_state = state
    app.include_router(_router(state))
    if mode == "wf":
        original_delay = background._WAKE_RETRY_SECONDS

        async def fail_wake(*args: Any, **kwargs: Any) -> Any:
            state.wake_start_calls += 1
            raise HTTPException(status_code=503, detail="E3 deterministic wake failure")

        background._start_wake_run = fail_wake
        background._WAKE_RETRY_SECONDS = min(float(original_delay), 0.01)
        return state

    if mode != "r-a":
        return state

    original_subprocess = asyncio.create_subprocess_exec

    async def observed_subprocess(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        paths = _command_paths(args)
        env = kwargs.get("env") or {}
        state.child_observations.append(
            {
                "sandbox_workspace_write": "workspace-write" in {str(value) for value in args},
                "paths_within_root": bool(paths) and all(_within(path, root) for path in paths),
                "no_deer_flow_env": not any(str(key).startswith("DEER_FLOW_") for key in env),
            }
        )
        write_control(
            root / "control" / "child-observer.json",
            {"child_started": len(state.child_observations), **state.child_observations[-1]},
        )
        return await original_subprocess(*args, **kwargs)

    asyncio.create_subprocess_exec = observed_subprocess
    original_claim = background.CommandRoomBackgroundService._claim_wake

    async def gated_claim(service: Any, job: Any, snapshot: Any) -> str | None:
        if getattr(job, "task_id", None) == _TASK_ID:
            write_control(
                root / "control" / "outcome-durable.json",
                {"task_id_hash": hashlib.sha256(_TASK_ID.encode()).hexdigest(), "wake_claimed": False},
            )
            await state.gate.wait()
        return await original_claim(service, job, snapshot)

    background.CommandRoomBackgroundService._claim_wake = gated_claim
    return state


__all__ = ["E3ChairModel", "loopback", "mount_controls", "redact_public", "validate_mount", "write_control"]
