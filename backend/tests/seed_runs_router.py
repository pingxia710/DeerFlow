"""Test-only run/message seeder for the multi-run render-order e2e (issue #3352).

Mounted **only** by ``scripts/run_replay_gateway.py`` (the replay e2e gateway)
and never by the production app, so it cannot ship. It lets a Playwright spec
stand up a thread with >=2 runs whose per-run messages exercise the frontend's
reload / history-rebuild ordering path — with no real model, no recording, and
no API key.

Why a seeder instead of recording a conversation: issue #3352 only reproduces
when the checkpoint no longer holds the older messages (post-compression), so
the frontend rebuilds them from the per-run history endpoints. A seeder lets us
create exactly that precondition deterministically — runs in the run store +
per-run ``category="message"`` events, and **no checkpoint** — so on reload the
buggy ``findLatestUnloadedRunIndex`` + prepend in ``core/threads/hooks.ts`` is
the sole source of truth and its reversed order becomes observable.

It writes through the gateway's OWN ``app.state.run_store`` +
``app.state.run_event_store`` using the request's auth context, so the seeded
``user_id`` matches the browser session that reads it back. The event shape
mirrors exactly what ``runtime/journal.py`` writes for real runs
(``event_type`` ``llm.human.input`` / ``llm.ai.response``, ``category``
``"message"``, ``content`` = ``message.model_dump()``, ``metadata.caller`` =
``"lead_agent"``).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.gateway.path_utils import get_request_storage_user_id

router = APIRouter(prefix="/api/test-only", tags=["test-only"])

# Mirror runtime/journal.py: human prompts are recorded as ``llm.human.input``
# and assistant turns as ``llm.ai.response``; both land in ``category="message"``.
_EVENT_TYPE = {
    "human": "llm.human.input",
    "ai": "llm.ai.response",
    "tool": "llm.tool.result",
}


class SeedMessage(BaseModel):
    role: Literal["human", "ai", "tool"]
    content: str
    id: str
    caller: str = "lead_agent"
    tool_call_id: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)


class SeedRun(BaseModel):
    run_id: str
    # ISO timestamp; RunManager.list_by_thread sorts newest-first by created_at,
    # so a later created_at must mean a later run for the ordering to be faithful.
    created_at: str
    completed_at: str | None = None
    messages: list[SeedMessage]
    status: str = "success"
    terminal_reason: str | None = None


class SeedTaskLane(BaseModel):
    run_id: str
    task_id: str
    status: str
    role: str | None = None
    description: str | None = None
    result_ref: str | None = None
    evidence_ref: str | None = None
    error: str | None = None


class SeedRunsBody(BaseModel):
    thread_id: str
    runs: list[SeedRun]
    task_lanes: list[SeedTaskLane] = Field(default_factory=list)


@router.post("/seed-runs")
async def seed_runs(body: SeedRunsBody, request: Request) -> dict:
    """Seed runs + per-run message events for the authenticated user.

    No checkpoint is written: that is the whole point — it forces the frontend's
    reload path to rebuild history from the per-run endpoints (the #3352 bug
    site) instead of the (correctly ordered) checkpoint snapshot.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    run_store = request.app.state.run_store
    event_store = request.app.state.run_event_store
    round_store = getattr(request.app.state, "round_state_store", None)
    thread_store = request.app.state.thread_store
    user_id = get_request_storage_user_id(request)

    if await thread_store.get(body.thread_id, user_id=user_id) is None:
        await thread_store.create(
            body.thread_id,
            assistant_id="lead_agent",
            user_id=user_id,
            metadata={},
        )

    for run in body.runs:
        # Scope seeded rows to the same storage user that the browser session
        # will use when it reads them back via GET /runs and /messages.
        await run_store.put(
            run.run_id,
            thread_id=body.thread_id,
            assistant_id="lead_agent",
            user_id=user_id,
            status=run.status,
            created_at=run.created_at,
            metadata={"completed_at": run.completed_at or run.created_at},
        )
        if run.terminal_reason is not None:
            await run_store.update_status(run.run_id, run.status, terminal_reason=run.terminal_reason)
        events = []
        for m in run.messages:
            if m.role == "human":
                msg = HumanMessage(content=m.content, id=m.id)
            elif m.role == "ai":
                msg = AIMessage(content=m.content, id=m.id, tool_calls=m.tool_calls)
            else:
                msg = ToolMessage(content=m.content, id=m.id, tool_call_id=m.tool_call_id or m.id)
            events.append(
                {
                    "thread_id": body.thread_id,
                    "run_id": run.run_id,
                    "event_type": _EVENT_TYPE[m.role],
                    "category": "message",
                    "content": msg.model_dump(),
                    "metadata": {"caller": m.caller},
                    "created_at": run.created_at,
                    "user_id": user_id,
                }
            )
        # One batch per run so seq is monotonic and run1's messages precede
        # run2's; the gateway reads them back per-run anyway.
        await event_store.put_batch(events)

    if round_store is not None and hasattr(round_store, "bind_run") and hasattr(round_store, "record_task_events"):
        run_ids = {lane.run_id for lane in body.task_lanes}
        for run_id in run_ids:
            await round_store.bind_run(thread_id=body.thread_id, run_id=run_id, user_id=user_id)
        await round_store.record_task_events(
            [
                {
                    "type": "task_completed" if lane.status == "completed" else "task_started" if lane.status == "in_progress" else "task_failed",
                    "thread_id": body.thread_id,
                    "run_id": lane.run_id,
                    "task_id": lane.task_id,
                    "status": lane.status,
                    "subagent_type": lane.role,
                    "description": lane.description,
                    "error_preview": lane.error,
                    "action_result": {
                        "output_ref": lane.result_ref,
                        "evidence_refs": [lane.evidence_ref] if lane.evidence_ref else [],
                    },
                }
                for lane in body.task_lanes
            ]
        )

    return {"ok": True, "thread_id": body.thread_id, "runs": len(body.runs)}
