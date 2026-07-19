from __future__ import annotations

import pytest

from deerflow.persistence.round_state import MemoryRoundStateStore

pytestmark = pytest.mark.anyio


async def test_run_group_records_identity_and_current_user_text() -> None:
    store = MemoryRoundStateStore()

    first = await store.bind_run(
        thread_id="thread-1",
        run_id="run-1",
        user_id="owner-1",
        current_intent="Inspect the actual repository.",
    )
    second = await store.bind_run(
        thread_id="thread-1",
        run_id="run-2",
        user_id="owner-1",
        current_intent="Continue from the returned facts.",
    )

    assert first["round_id"] != second["round_id"]
    assert second["parent_round_id"] == first["round_id"]
    assert second["current_run_id"] == "run-2"
    assert second["current_intent"] == "Continue from the returned facts."
    assert set(second) >= {
        "round_id",
        "thread_id",
        "user_id",
        "parent_round_id",
        "current_run_id",
        "source_goal_run_id",
        "current_intent",
        "created_at",
        "updated_at",
    }


async def test_task_lane_preserves_terminal_transport_facts_and_preview() -> None:
    store = MemoryRoundStateStore()
    bound = await store.bind_run(
        thread_id="thread-1",
        run_id="run-1",
        user_id="owner-1",
    )

    await store.record_task_events(
        [
            {
                "type": "task_completed",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "round_id": bound["round_id"],
                "task_id": "task-1",
                "subagent_type": "fact-finder",
                "description": "Read one file",
                "status": "completed",
                "result_preview": "Factual result preview.",
                "handoff_envelope": {
                    "background_recovery": {"wake": {"state": "pending"}},
                },
            }
        ]
    )

    lane = await store.get_task_lane(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        user_id="owner-1",
    )
    assert lane is not None
    assert lane["status"] == "completed"
    assert lane["result"] == "Factual result preview."
    assert lane["handoff"] == {
        "background_recovery": {"wake": {"state": "pending"}},
    }
