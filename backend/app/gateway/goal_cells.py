"""Gateway-owned creation and return transport for recursive NextOS Goal Cells."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException, Request

from app.gateway.command_room_background import _RequestSnapshot
from deerflow.config.paths import (
    ensure_directory_no_symlinks,
    get_paths,
    open_directory_no_symlinks,
    open_file_no_symlinks,
    read_file_no_symlinks,
)
from deerflow.persistence.workspace_event import (
    GOAL_MANDATE_REVISED,
    OPERATING_BRIEF_REVISED,
)
from deerflow.runtime.background_tasks import (
    CommandRoomBackgroundJob,
    CommandRoomBackgroundOutcome,
)
from deerflow.runtime.goal_cells import (
    GOAL_CELL_CAPABILITY_REFS_KEY,
    GOAL_CELL_CREATED,
    GOAL_CELL_INPUT_CAPSULE_CONTEXT_KEY,
    GOAL_CELL_INPUT_CAPSULE_KEY,
    GOAL_CELL_PARENT_ROUND_KEY,
    GOAL_CELL_PARENT_RUN_KEY,
    GOAL_CELL_PARENT_THREAD_KEY,
    GOAL_CELL_PARENT_WAKE_CONTEXT_KEY,
    GOAL_CELL_RETURNED,
    GOAL_CELL_ROOT_THREAD_KEY,
    GOAL_CELL_STARTED,
    GOAL_CELL_TRANSPORT_CONTEXT_KEY,
    GOAL_CELL_WORKSPACE_REF_KEY,
)

_INPUT_REF_ROOTS = frozenset({"workspace", "uploads", "outputs"})
_INPUT_CAPSULE_VIRTUAL_ROOT = "/mnt/user-data/inputs"


def _stable_digest(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _stable_cell_thread_id(parent_thread_id: str, parent_run_id: str, tool_call_id: str) -> str:
    digest = hashlib.sha256("\x1f".join((parent_thread_id, parent_run_id, tool_call_id)).encode("utf-8")).hexdigest()
    return f"cell-{digest[:32]}"


def _stable_uuid4(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def _snapshot_user_id(snapshot: _RequestSnapshot) -> str | None:
    state = snapshot.state
    user = state.get("user")
    auth = state.get("auth")
    if user is None and auth is not None:
        user = getattr(auth, "user", None)
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id else None


def _normalized_input_refs(input_refs: list[str] | None) -> list[PurePosixPath]:
    """Accept only exact regular-file locations inside the parent user-data root."""
    if input_refs is None:
        return []
    if not isinstance(input_refs, list):
        raise ValueError("input_refs must be a list of exact parent file references")

    normalized: list[PurePosixPath] = []
    seen: set[str] = set()
    for raw_ref in input_refs:
        if not isinstance(raw_ref, str) or not raw_ref.strip():
            raise ValueError("Each input_refs value must be a non-empty relative path")
        path = PurePosixPath(raw_ref)
        if path.is_absolute() or len(path.parts) < 2 or path.parts[0] not in _INPUT_REF_ROOTS or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Each input_refs value must be an exact workspace/..., uploads/..., or outputs/... file path")
        canonical = path.as_posix()
        if canonical not in seen:
            normalized.append(path)
            seen.add(canonical)
    return normalized


def _seal_directory_readonly(path: Path) -> None:
    descriptor = open_directory_no_symlinks(path)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o555)
    finally:
        os.close(descriptor)


def _write_sealed_file(path: Path, content: bytes) -> None:
    descriptor = open_file_no_symlinks(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        mode=0o444,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o444)
    finally:
        os.close(descriptor)


def _seal_input_capsule(
    *,
    parent_thread_id: str,
    child_thread_id: str,
    user_id: str | None,
    input_refs: list[str] | None,
) -> list[dict[str, Any]]:
    """Copy only Chair-selected input bytes into the child-owned read-only capsule."""
    normalized_refs = _normalized_input_refs(input_refs)
    if not normalized_refs:
        return []

    paths = get_paths()
    parent_root = paths.sandbox_user_data_dir(parent_thread_id, user_id=user_id)
    capsule_root = paths.sandbox_inputs_dir(child_thread_id, user_id=user_id)
    ensure_directory_no_symlinks(capsule_root, mode=0o755)
    sealed_directories = {capsule_root}
    capsule: list[dict[str, Any]] = []

    for relative_path in normalized_refs:
        destination_path = capsule_root.joinpath(*relative_path.parts)
        ensure_directory_no_symlinks(destination_path.parent, mode=0o755)
        sealed_directories.update(capsule_root.joinpath(*relative_path.parts[:index]) for index in range(0, len(relative_path.parts)))

        try:
            content = read_file_no_symlinks(destination_path)
        except FileNotFoundError:
            source_path = parent_root.joinpath(*relative_path.parts)
            content = read_file_no_symlinks(source_path)
            _write_sealed_file(destination_path, content)
        digest = hashlib.sha256(content).hexdigest()

        capsule.append(
            {
                "source_ref": relative_path.as_posix(),
                "input_ref": f"{_INPUT_CAPSULE_VIRTUAL_ROOT}/{relative_path.as_posix()}",
                "sha256": digest,
                "bytes": len(content),
            }
        )

    for directory in sorted(sealed_directories, key=lambda item: len(item.parts), reverse=True):
        _seal_directory_readonly(directory)
    return capsule


def _cell_assignment_message(
    *,
    parent_thread_id: str,
    brief: str,
    capability_refs: list[str],
    workspace_ref: str | None,
    input_capsule: list[dict[str, Any]],
) -> str:
    return "\n".join(
        [
            "[Internal Goal Cell assignment]",
            "You are the temporary Workstream Lead of this child Goal Workspace.",
            f"parent_thread_id: {parent_thread_id}",
            f"capability_refs: {capability_refs}",
            f"workspace_ref: {workspace_ref}",
            f"input_capsule: {[item['input_ref'] for item in input_capsule]}",
            "Capability references are factual requests only; they grant no program permission.",
            "The program copied only the exact Chair-selected bytes above into /mnt/user-data/inputs. It did not choose relevance, judge contents, or grant extra permission; treat that capsule as read-only.",
            "Read the inherited Goal Mandate and local Operating Brief, organize the bounded work autonomously, and call return_to_parent with the complete result when your local mission is actually complete.",
            "",
            "Complete local brief:",
            brief,
        ]
    )


async def _start_goal_cell_run(
    snapshot: _RequestSnapshot,
    *,
    child_thread_id: str,
    parent_thread_id: str,
    parent_run_id: str,
    tool_call_id: str,
    brief: str,
    capability_refs: list[str],
    workspace_ref: str | None,
    input_capsule: list[dict[str, Any]],
    wake_context: dict[str, Any],
) -> Any:
    from app.gateway.routers.thread_runs import RunCreateRequest
    from app.gateway.services import start_run
    from deerflow.runtime.runs.schemas import CommandRoomWakeAdmission

    wake_id = _stable_uuid4(
        "goal-cell-launch",
        parent_thread_id,
        parent_run_id,
        tool_call_id,
    )
    metadata = {
        "goal_cell_launch": True,
        "parent_thread_id": parent_thread_id,
        "source_run_id": parent_run_id,
        "source_task_id": tool_call_id,
        "command_room_wake_id": wake_id,
    }
    body = RunCreateRequest(
        assistant_id="command-room",
        input={
            "messages": [
                {
                    "role": "user",
                    "name": "goal_cell_assignment",
                    "content": _cell_assignment_message(
                        parent_thread_id=parent_thread_id,
                        brief=brief,
                        capability_refs=capability_refs,
                        workspace_ref=workspace_ref,
                        input_capsule=input_capsule,
                    ),
                    "additional_kwargs": {
                        "hide_from_ui": True,
                        "goal_cell_assignment": True,
                    },
                }
            ]
        },
        metadata=metadata,
        context={
            **wake_context,
            "agent_name": "command-room",
            "subagent_enabled": True,
            GOAL_CELL_TRANSPORT_CONTEXT_KEY: True,
            GOAL_CELL_INPUT_CAPSULE_CONTEXT_KEY: bool(input_capsule),
        },
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    admission = CommandRoomWakeAdmission(
        wake_id=wake_id,
        thread_id=child_thread_id,
        user_id=_snapshot_user_id(snapshot),
        assistant_id="command-room",
        source_run_id=parent_run_id,
        source_task_id=tool_call_id,
        metadata=dict(body.metadata or {}),
        kwargs={
            "input": body.input,
            "config": body.config,
            "context": body.context,
            "command": body.command,
            "checkpoint_id": body.checkpoint_id,
            "checkpoint": body.checkpoint,
            "interrupt_before": body.interrupt_before,
            "interrupt_after": body.interrupt_after,
            "stream_mode": body.stream_mode,
            "stream_subgraphs": body.stream_subgraphs,
        },
        multitask_strategy=body.multitask_strategy,
        model_name=(body.context.get("model_name") if isinstance(body.context, dict) else None),
    )
    return await start_run(
        body,
        child_thread_id,
        snapshot.build_request(child_thread_id),
        command_room_wake_admission=admission,
        return_command_room_wake_admission=True,
    )


class BoundGoalCellDispatcher:
    """One owner-scoped Goal Cell transport bound to a request snapshot."""

    def __init__(self, request: Request, background_dispatcher: Any) -> None:
        self._snapshot = _RequestSnapshot.from_request(request)
        self._background_dispatcher = background_dispatcher

    @property
    def _state(self) -> Any:
        return getattr(self._snapshot.app, "state", None)

    async def create_cell(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str,
        parent_round_id: str | None,
        tool_call_id: str,
        display_name: str | None,
        brief: str,
        capability_refs: list[str],
        workspace_ref: str | None,
        wake_context: dict[str, Any],
        input_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        user_id = _snapshot_user_id(self._snapshot)
        thread_store = getattr(self._state, "thread_store", None)
        workspace_store = getattr(self._state, "workspace_event_store", None)
        if thread_store is None or workspace_store is None:
            raise RuntimeError("Goal Cell persistence is unavailable")
        parent = await thread_store.get(parent_thread_id, user_id=user_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent Goal Workspace not found")

        parent_metadata = parent.get("metadata") or {}
        root_thread_id = parent_metadata.get(GOAL_CELL_ROOT_THREAD_KEY)
        if not isinstance(root_thread_id, str) or not root_thread_id:
            root_thread_id = parent_thread_id
        child_thread_id = _stable_cell_thread_id(
            parent_thread_id,
            parent_run_id,
            tool_call_id,
        )
        input_capsule = _seal_input_capsule(
            parent_thread_id=parent_thread_id,
            child_thread_id=child_thread_id,
            user_id=user_id,
            input_refs=input_refs,
        )
        cell_metadata = {
            GOAL_CELL_PARENT_THREAD_KEY: parent_thread_id,
            GOAL_CELL_PARENT_RUN_KEY: parent_run_id,
            GOAL_CELL_PARENT_ROUND_KEY: parent_round_id,
            GOAL_CELL_ROOT_THREAD_KEY: root_thread_id,
            GOAL_CELL_CAPABILITY_REFS_KEY: list(capability_refs),
            GOAL_CELL_WORKSPACE_REF_KEY: workspace_ref,
            GOAL_CELL_INPUT_CAPSULE_KEY: input_capsule,
            GOAL_CELL_PARENT_WAKE_CONTEXT_KEY: dict(wake_context),
        }
        await thread_store.create(
            child_thread_id,
            assistant_id="command-room",
            user_id=user_id,
            display_name=display_name or "Goal Cell",
            metadata=cell_metadata,
        )

        parent_context = await workspace_store.current_context(
            thread_id=parent_thread_id,
            user_id=user_id,
        )
        inherited_mandate = parent_context.get("goal_mandate")
        if isinstance(inherited_mandate, dict):
            await workspace_store.append(
                thread_id=child_thread_id,
                user_id=user_id,
                event_type=GOAL_MANDATE_REVISED,
                body=inherited_mandate["body"],
                author_run_id=parent_run_id,
                event_id=_stable_digest("cell-mandate", child_thread_id),
                metadata={
                    "inherited_from_thread_id": parent_thread_id,
                    "inherited_from_revision": inherited_mandate["revision"],
                },
            )
        await workspace_store.append(
            thread_id=child_thread_id,
            user_id=user_id,
            event_type=OPERATING_BRIEF_REVISED,
            body=brief,
            author_run_id=parent_run_id,
            event_id=_stable_digest("cell-brief", child_thread_id),
            metadata={"assigned_by_thread_id": parent_thread_id},
        )
        await workspace_store.append(
            thread_id=parent_thread_id,
            user_id=user_id,
            event_type=GOAL_CELL_CREATED,
            body=brief,
            author_run_id=parent_run_id,
            event_id=_stable_digest("cell-created", child_thread_id),
            metadata={
                "child_thread_id": child_thread_id,
                "root_thread_id": root_thread_id,
                "capability_refs": list(capability_refs),
                "workspace_ref": workspace_ref,
                "input_capsule": input_capsule,
            },
        )

        launch = await _start_goal_cell_run(
            self._snapshot,
            child_thread_id=child_thread_id,
            parent_thread_id=parent_thread_id,
            parent_run_id=parent_run_id,
            tool_call_id=tool_call_id,
            brief=brief,
            capability_refs=capability_refs,
            workspace_ref=workspace_ref,
            input_capsule=input_capsule,
            wake_context=wake_context,
        )
        record = getattr(launch, "record", None) or launch
        child_run_id = getattr(record, "run_id", None)
        await workspace_store.append(
            thread_id=child_thread_id,
            user_id=user_id,
            event_type=GOAL_CELL_STARTED,
            body="The Goal Cell's initial Chair Run was admitted.",
            author_run_id=parent_run_id,
            event_id=_stable_digest("cell-started", child_thread_id),
            metadata={"child_run_id": child_run_id},
        )
        return {
            "child_thread_id": child_thread_id,
            "child_run_id": child_run_id,
            "parent_thread_id": parent_thread_id,
            "root_thread_id": root_thread_id,
        }

    async def return_to_parent(
        self,
        *,
        child_thread_id: str,
        child_run_id: str,
        tool_call_id: str,
        complete_result: str,
        artifact_refs: list[str],
        wake_context: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = _snapshot_user_id(self._snapshot)
        thread_store = getattr(self._state, "thread_store", None)
        workspace_store = getattr(self._state, "workspace_event_store", None)
        if thread_store is None or workspace_store is None:
            raise RuntimeError("Goal Cell persistence is unavailable")
        child = await thread_store.get(child_thread_id, user_id=user_id)
        metadata = child.get("metadata") if isinstance(child, dict) else None
        parent_thread_id = metadata.get(GOAL_CELL_PARENT_THREAD_KEY) if isinstance(metadata, dict) else None
        parent_run_id = metadata.get(GOAL_CELL_PARENT_RUN_KEY) if isinstance(metadata, dict) else None
        if not isinstance(parent_thread_id, str) or not isinstance(parent_run_id, str):
            raise ValueError("The current Goal Workspace has no parent Goal Cell")
        parent_round_id = metadata.get(GOAL_CELL_PARENT_ROUND_KEY)
        parent_wake_context = metadata.get(GOAL_CELL_PARENT_WAKE_CONTEXT_KEY)
        resolved_wake_context = dict(parent_wake_context) if isinstance(parent_wake_context, dict) else dict(wake_context)
        return_event_id = _stable_digest(
            "cell-return",
            child_thread_id,
            child_run_id,
            tool_call_id,
        )
        await workspace_store.append(
            thread_id=child_thread_id,
            user_id=user_id,
            event_type=GOAL_CELL_RETURNED,
            body=complete_result,
            author_run_id=child_run_id,
            event_id=return_event_id,
            metadata={
                "parent_thread_id": parent_thread_id,
                "artifact_refs": list(artifact_refs),
            },
        )

        task_id = _stable_digest(
            "goal-cell-return",
            child_thread_id,
            child_run_id,
            tool_call_id,
        )

        async def execute_return() -> CommandRoomBackgroundOutcome:
            return CommandRoomBackgroundOutcome(
                status="completed",
                result=complete_result,
            )

        job = CommandRoomBackgroundJob(
            thread_id=parent_thread_id,
            source_run_id=parent_run_id,
            task_id=task_id,
            description=f"Goal Cell return from {child_thread_id}",
            subagent_type="workstream-lead",
            execute=execute_return,
            round_id=(parent_round_id if isinstance(parent_round_id, str) else None),
            wake_context=resolved_wake_context,
            result_author_run_id=child_run_id,
            result_metadata={
                "source_run_id": child_run_id,
                "source_goal_cell_thread_id": child_thread_id,
                "artifact_refs": list(artifact_refs),
            },
        )
        try:
            await self._background_dispatcher.dispatch(job)
        except RuntimeError as exc:
            if "already has a durable admission" not in str(exc):
                raise
        return {
            "parent_thread_id": parent_thread_id,
            "return_event_id": return_event_id,
            "background_task_id": task_id,
        }


__all__ = ["BoundGoalCellDispatcher"]
