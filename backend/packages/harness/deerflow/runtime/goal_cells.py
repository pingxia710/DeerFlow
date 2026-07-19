"""Runtime-neutral contracts and durable metadata keys for NextOS Goal Cells."""

from __future__ import annotations

from typing import Any, Protocol

GOAL_CELL_CREATED = "goal_cell.created"
GOAL_CELL_STARTED = "goal_cell.started"
GOAL_CELL_RETURNED = "goal_cell.returned"

GOAL_CELL_PARENT_THREAD_KEY = "_nextos_parent_thread_id"
GOAL_CELL_PARENT_RUN_KEY = "_nextos_parent_run_id"
GOAL_CELL_PARENT_ROUND_KEY = "_nextos_parent_round_id"
GOAL_CELL_ROOT_THREAD_KEY = "_nextos_root_thread_id"
GOAL_CELL_CAPABILITY_REFS_KEY = "_nextos_capability_refs"
GOAL_CELL_WORKSPACE_REF_KEY = "_nextos_workspace_ref"
GOAL_CELL_INPUT_CAPSULE_KEY = "_nextos_input_capsule"
GOAL_CELL_PARENT_WAKE_CONTEXT_KEY = "_nextos_parent_wake_context"
GOAL_CELL_TRANSPORT_CONTEXT_KEY = "__nextos_goal_cell_transport"
GOAL_CELL_INPUT_CAPSULE_CONTEXT_KEY = "__nextos_goal_cell_input_capsule"

GOAL_CELL_THREAD_METADATA_KEYS = frozenset(
    {
        GOAL_CELL_PARENT_THREAD_KEY,
        GOAL_CELL_PARENT_RUN_KEY,
        GOAL_CELL_PARENT_ROUND_KEY,
        GOAL_CELL_ROOT_THREAD_KEY,
        GOAL_CELL_CAPABILITY_REFS_KEY,
        GOAL_CELL_WORKSPACE_REF_KEY,
        GOAL_CELL_INPUT_CAPSULE_KEY,
        GOAL_CELL_PARENT_WAKE_CONTEXT_KEY,
    }
)


class GoalCellDispatcher(Protocol):
    """Gateway-owned Goal Cell operations injected into a Chair runtime."""

    async def create_cell(self, **kwargs: Any) -> dict[str, Any]:
        """Create or recover one idempotent child Goal Workspace and launch it."""

    async def return_to_parent(self, **kwargs: Any) -> dict[str, Any]:
        """Persist one complete child return and schedule its parent wake."""


__all__ = [
    "GOAL_CELL_CAPABILITY_REFS_KEY",
    "GOAL_CELL_CREATED",
    "GOAL_CELL_INPUT_CAPSULE_CONTEXT_KEY",
    "GOAL_CELL_INPUT_CAPSULE_KEY",
    "GOAL_CELL_PARENT_ROUND_KEY",
    "GOAL_CELL_PARENT_RUN_KEY",
    "GOAL_CELL_PARENT_THREAD_KEY",
    "GOAL_CELL_PARENT_WAKE_CONTEXT_KEY",
    "GOAL_CELL_RETURNED",
    "GOAL_CELL_ROOT_THREAD_KEY",
    "GOAL_CELL_STARTED",
    "GOAL_CELL_THREAD_METADATA_KEYS",
    "GOAL_CELL_TRANSPORT_CONTEXT_KEY",
    "GOAL_CELL_WORKSPACE_REF_KEY",
    "GoalCellDispatcher",
]
