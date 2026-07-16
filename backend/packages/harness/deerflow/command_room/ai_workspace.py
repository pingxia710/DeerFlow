"""Optional Markdown paths for Command Room natural-language handoffs.

The filesystem is shared AI state, not shared model context. Container, package,
and cycle values are factual labels, never task admission or sequencing rules.
The Chair alone interprets the text and chooses the next AI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from deerflow.config.paths import ensure_directory_no_symlinks, open_file_no_symlinks, read_file_no_symlinks, validate_thread_id

AI_WORKSPACE_CONTEXT_HEADER = "[Internal AI-AI Workspace]"
AI_WORKSPACE_DIRNAME = "command-room-loop"
COMMAND_ROOM_CONTAINERS = (
    "context",
    "planning",
    "technical-design",
    "execution",
    "review",
    "project-steward",
    "debt-curation",
    "learning-curation",
)
_CONTAINER_RECEIPTS_DIRNAME = "command-room-container-receipts"
_CONTAINER_RECEIPTS_LOCKS: dict[str, threading.Lock] = {}
_CONTAINER_RECEIPTS_LOCKS_GUARD = threading.Lock()
_ANY = object()
_WORK_PACKAGE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

AI_WORKSPACE_FILES = (
    "00-context/README.md",
    "00-context/context.md",
    "01-planning/README.md",
    "01-planning/forward.md",
    "01-planning/opposition.md",
    "01-planning/spec.md",
    "02-technical-design/README.md",
    "02-technical-design/forward.md",
    "02-technical-design/opposition.md",
    "02-technical-design/technical-plan.md",
    "03-delivery/README.md",
    "04-governance/README.md",
)

_FILE_PURPOSES = {
    "00-context/README.md": "Optional information discovery and a Recorder-authored factual context snapshot may be preserved here.",
    "00-context/context.md": "A Recorder preserves the Chair's factual context snapshot for the work package.",
    "01-planning/README.md": "Optional independent planning angles and a Chair-authored unified spec may be preserved here.",
    "01-planning/forward.md": "A planning AI develops the strongest forward route from the Chair brief.",
    "01-planning/opposition.md": "A separate AI independently exposes contrary angles, risks, and hidden assumptions.",
    "01-planning/spec.md": "A Recorder preserves the Chair's unified direction, goal, boundary, plan, and acceptance shape.",
    "02-technical-design/README.md": "Optional technical design uses independent forward and contrary angles.",
    "02-technical-design/forward.md": "A technical AI develops the strongest implementation route.",
    "02-technical-design/opposition.md": "A separate AI exposes technical failure modes, trade-offs, and alternatives.",
    "02-technical-design/technical-plan.md": "A Recorder preserves the Chair's unified technical decision.",
    "03-delivery/README.md": "Optional delivery-cycle labels can route execution notes and independent review findings here.",
    "04-governance/README.md": "Accepted delivery closes through Project Steward; completed projects then curate debt and learning before final governance delivery.",
}

_INITIAL_FILE_TEXT = {filename: f"# {Path(filename).stem.replace('-', ' ').title()}\n\nThis handoff surface was created by DeerFlow. It has not yet been authored by an AI.\n" for filename in AI_WORKSPACE_FILES}

CommandRoomContainer = Literal[
    "context",
    "planning",
    "technical-design",
    "execution",
    "review",
    "project-steward",
    "debt-curation",
    "learning-curation",
]
CommandRoomArtifactKind = Literal[
    "context-discovery",
    "context",
    "planning-forward",
    "planning-opposition",
    "spec",
    "technical-forward",
    "technical-opposition",
    "technical-plan",
    "execution",
    "findings",
    "project-status",
    "debt",
    "learning",
]
ContainerArtifact = Literal[
    "context-discovery",
    "context",
    "planning-forward",
    "planning-opposition",
    "spec",
    "technical-forward",
    "technical-opposition",
    "technical-plan",
]
ProjectLifecycleStatus = Literal["task_closed", "continue", "project_complete", "blocked", "closed"]
RecoverableContainerArtifact = Literal[
    "planning-forward",
    "planning-opposition",
    "technical-forward",
    "technical-opposition",
]


@dataclass(frozen=True)
class CommandRoomContainerTask:
    """Objective paths selected by one Chair-declared AI handoff."""

    container: CommandRoomContainer
    work_package_id: str | None
    delivery_cycle_index: int | None
    task_id: str
    output_path: Path
    input_paths: tuple[Path, ...]
    artifact_kind: CommandRoomArtifactKind
    receipt_path: Path
    artifact_sha256_before: str | None


def command_room_ai_workspace_dir(workspace_path: str | Path, project_key: str) -> Path:
    if not str(workspace_path):
        raise ValueError("workspace_path is required")
    return Path(workspace_path) / AI_WORKSPACE_DIRNAME / validate_thread_id(str(project_key))


def validate_work_package_id(work_package_id: str | None) -> str | None:
    """Validate an optional Chair-declared package namespace."""

    if work_package_id is None:
        return None
    if not isinstance(work_package_id, str) or not _WORK_PACKAGE_ID_RE.fullmatch(work_package_id):
        raise ValueError("work_package_id must use lowercase letters, digits, and hyphens (maximum 64 characters).")
    return work_package_id


def command_room_work_package_dir(workspace_root: str | Path, work_package_id: str | None = None) -> Path:
    """Return the isolated workspace for one explicit work package."""

    root = Path(workspace_root)
    package_id = validate_work_package_id(work_package_id)
    if package_id is None:
        return root
    if root.parent.name == "packages":
        if root.name != package_id:
            raise ValueError("work_package_id does not match the Command Room package workspace.")
        return root
    return root / "packages" / package_id


def _write_initial_file(path: Path, text: str) -> None:
    ensure_directory_no_symlinks(path.parent, mode=0o777)
    try:
        fd = open_file_no_symlinks(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o666)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)


def _read_file(path: Path) -> str | None:
    try:
        return read_file_no_symlinks(path).decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _artifact_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(read_file_no_symlinks(path)).hexdigest()
    except (OSError, ValueError):
        return None


def _task_note_path(root: Path, *, container: Literal["execution", "review"], cycle_index: int, task_id: str) -> Path:
    task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
    directory = "execution" if container == "execution" else "review"
    filename = f"task-{task_digest}.md" if container == "execution" else f"findings-{task_digest}.md"
    return root / "03-delivery" / f"cycle-{cycle_index:02d}" / directory / filename


def _context_discovery_note_path(root: Path, *, task_id: str) -> Path:
    task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
    return root / "00-context" / "discovery" / f"discovery-{task_digest}.md"


def _governance_note_path(root: Path, *, container: Literal["project-steward", "debt-curation", "learning-curation"], task_id: str, cycle_index: int | None = None) -> Path:
    task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
    if container == "project-steward":
        return root / "04-governance" / "project-steward" / f"cycle-{cycle_index:02d}-{task_digest}.md"
    directory = "debt" if container == "debt-curation" else "learning"
    return root / "04-governance" / directory / f"curation-{task_digest}.md"


def command_room_container_receipts_path(workspace_root: str | Path) -> Path:
    """Return the parent-owned factual receipt ledger for one workspace run."""

    root = Path(workspace_root)
    if root.parent.name == "packages":
        thread_root = root.parent.parent
        package_id = validate_work_package_id(root.name)
    else:
        thread_root = root
        package_id = None
    workspace = thread_root.parents[1]
    thread_dir = workspace.parent.parent if workspace.name == "workspace" and workspace.parent.name == "user-data" else workspace.parent
    filename = validate_thread_id(thread_root.name)
    if package_id is not None:
        filename = f"{filename}--{package_id}"
    return thread_dir / "audit" / _CONTAINER_RECEIPTS_DIRNAME / f"{filename}.jsonl"


def _receipt_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _CONTAINER_RECEIPTS_LOCKS_GUARD:
        return _CONTAINER_RECEIPTS_LOCKS.setdefault(key, threading.Lock())


def _lock_receipt_file(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows only.
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_receipt_file(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows only.
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _container_receipts_lock(path: Path) -> Iterator[None]:
    ensure_directory_no_symlinks(path.parent, mode=0o700)
    with _receipt_lock(path):
        lock_path = path.with_suffix(path.suffix + ".lock")
        fd = open_file_no_symlinks(lock_path, os.O_RDWR | os.O_CREAT, mode=0o600)
        try:
            _lock_receipt_file(fd)
            yield
        finally:
            _unlock_receipt_file(fd)
            os.close(fd)


def _container_receipts(receipt_path: Path) -> tuple[dict[str, object], ...]:
    text = _read_file(receipt_path)
    if text is None:
        return ()
    receipts: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("status") in {"reserved", "completed", "unwritten", "failed", "timed_out", "cancelled"}:
            receipts.append(row)
    return tuple(receipts)


def _latest_receipts_by_task(receipts: tuple[dict[str, object], ...]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for receipt in receipts:
        task_id = receipt.get("task_id")
        if isinstance(task_id, str) and task_id:
            latest[task_id] = receipt
    return latest


def _matching_receipts(
    receipts: tuple[dict[str, object], ...],
    *,
    container: str | None = None,
    cycle_index: object = _ANY,
    artifact_kind: str | None = None,
) -> tuple[dict[str, object], ...]:
    rows = _latest_receipts_by_task(receipts).values()
    return tuple(
        row for row in rows if (container is None or row.get("container") == container) and (cycle_index is _ANY or row.get("delivery_cycle_index") == cycle_index) and (artifact_kind is None or row.get("artifact_kind") == artifact_kind)
    )


def _has_any(receipts: tuple[dict[str, object], ...], **filters: object) -> bool:
    return bool(_matching_receipts(receipts, **filters))


def _has_completed(receipts: tuple[dict[str, object], ...], **filters: object) -> bool:
    return any(row.get("status") == "completed" for row in _matching_receipts(receipts, **filters))


def _has_active_or_completed(receipts: tuple[dict[str, object], ...], **filters: object) -> bool:
    return any(row.get("status") in {"reserved", "completed"} for row in _matching_receipts(receipts, **filters))


def _append_receipt(receipt_path: Path, payload: dict[str, object]) -> None:
    fd = open_file_no_symlinks(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode=0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as file:
        file.write(json.dumps(payload, sort_keys=True) + "\n")


def _project_lifecycle_path(root: Path) -> Path:
    return root / "04-governance" / "project-lifecycle.jsonl"


def _project_lifecycle_records(root: Path) -> tuple[dict[str, object], ...]:
    text = _read_file(_project_lifecycle_path(root))
    if text is None:
        return ()
    records: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("status") in {"task_closed", "continue", "project_complete", "blocked", "closed"}:
            records.append(record)
    return tuple(records)


def latest_project_lifecycle_status(workspace_root: str | Path) -> dict[str, object] | None:
    records = _project_lifecycle_records(Path(workspace_root))
    return dict(records[-1]) if records else None


def record_project_lifecycle_status(
    workspace_root: str | Path,
    *,
    status: ProjectLifecycleStatus,
    summary: str,
    review_cycle_index: int | None = None,
) -> dict[str, object]:
    """Record an explicit Chair lifecycle decision after structural checks."""

    root = Path(workspace_root)
    cleaned_summary = " ".join(str(summary).split())
    if not cleaned_summary:
        raise ValueError("Project lifecycle summary is required.")
    lifecycle_path = _project_lifecycle_path(root)
    receipt_path = command_room_container_receipts_path(root)
    with _container_receipts_lock(lifecycle_path):
        receipts = _container_receipts(receipt_path)
        latest = latest_project_lifecycle_status(root)
        cycle_index = review_cycle_index
        if status == "task_closed":
            cycle_index = _validate_cycle_index(review_cycle_index)
            if not _has_completed(receipts, container="review", cycle_index=cycle_index):
                raise ValueError(f"Task close requires completed Review cycle {cycle_index}.")
            if latest and latest.get("status") in {"task_closed", "project_complete", "blocked", "closed"}:
                raise ValueError(f"Cannot close another delivery task while project lifecycle is {latest.get('status')}.")
        elif status in {"continue", "project_complete", "blocked"}:
            if not latest or latest.get("status") != "task_closed":
                raise ValueError("Project status requires a preceding explicit task_closed decision.")
            cycle_index = latest.get("review_cycle_index") if isinstance(latest.get("review_cycle_index"), int) else None
            if cycle_index is None or not _has_completed(receipts, container="project-steward", cycle_index=cycle_index):
                raise ValueError("Project status requires the fixed Project Steward handoff to complete first.")
        elif status == "closed":
            if not latest or latest.get("status") != "project_complete":
                raise ValueError("Final close requires project_complete governance mode.")
            cycle_index = latest.get("review_cycle_index") if isinstance(latest.get("review_cycle_index"), int) else None
            if not _has_completed(receipts, container="debt-curation") or not _has_completed(receipts, container="learning-curation"):
                raise ValueError("Final close requires completed Debt and Learning Curator handoffs.")
            completed_review_cycles = [row.get("delivery_cycle_index") for row in _matching_receipts(receipts, container="review") if row.get("status") == "completed" and isinstance(row.get("delivery_cycle_index"), int)]
            if cycle_index is None or not any(index > cycle_index for index in completed_review_cycles):
                raise ValueError("Final close requires a later completed governance Review cycle.")
        else:  # pragma: no cover - Literal guard for runtime callers.
            raise ValueError(f"Unsupported project lifecycle status: {status}")

        record: dict[str, object] = {
            "status": status,
            "summary": cleaned_summary[:4000],
            "review_cycle_index": cycle_index,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _append_receipt(lifecycle_path, record)
        return record


def ensure_command_room_ai_workspace(
    workspace_path: str | Path,
    project_key: str,
    *,
    work_package_id: str | None = None,
) -> Path:
    root = ensure_directory_no_symlinks(
        command_room_work_package_dir(
            command_room_ai_workspace_dir(workspace_path, project_key),
            work_package_id,
        ),
        mode=0o777,
    )
    for filename in AI_WORKSPACE_FILES:
        _write_initial_file(root / filename, _INITIAL_FILE_TEXT[filename])
    return root


def _validate_cycle_index(delivery_cycle_index: int | None) -> int:
    if isinstance(delivery_cycle_index, bool) or not isinstance(delivery_cycle_index, int) or delivery_cycle_index < 1:
        raise ValueError("Execution and Review require delivery_cycle_index as a positive integer.")
    return delivery_cycle_index


def _canonical_inputs(root: Path, receipts: tuple[dict[str, object], ...]) -> list[Path]:
    inputs: list[Path] = []
    if _has_completed(receipts, container="context", artifact_kind="context"):
        inputs.append(root / "00-context" / "context.md")
    if _has_completed(receipts, container="planning", artifact_kind="spec"):
        inputs.append(root / "01-planning" / "spec.md")
    if _has_completed(receipts, container="technical-design", artifact_kind="technical-plan"):
        inputs.append(root / "02-technical-design" / "technical-plan.md")
    return inputs


def prepare_command_room_container_task(
    workspace_root: str | Path,
    *,
    container: str | None,
    task_id: str,
    container_artifact: str | None = None,
    delivery_cycle_index: int | None = None,
    work_package_id: str | None = None,
) -> CommandRoomContainerTask:
    """Prepare an optional labeled artifact without controlling task sequence."""

    package_id = validate_work_package_id(work_package_id)
    root = command_room_work_package_dir(workspace_root, package_id)
    for filename in AI_WORKSPACE_FILES:
        _write_initial_file(root / filename, _INITIAL_FILE_TEXT[filename])
    if container not in COMMAND_ROOM_CONTAINERS:
        raise ValueError(f"Command Room task requires container one of: {', '.join(COMMAND_ROOM_CONTAINERS)}.")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Command Room task_id is required for a container handoff.")

    receipt_path = command_room_container_receipts_path(root)
    with _container_receipts_lock(receipt_path):
        receipts = _container_receipts(receipt_path)
        if task_id in _latest_receipts_by_task(receipts):
            raise ValueError("Command Room task_id is already admitted for this run; do not launch the same child twice.")

        if container == "context":
            if container_artifact not in {"context-discovery", "context"}:
                raise ValueError("Context requires container_artifact context-discovery or context.")
            if container_artifact == "context":
                output_path = root / "00-context" / "context.md"
                input_paths = (root / "00-context" / "discovery", output_path)
            else:
                output_path = _context_discovery_note_path(root, task_id=task_id)
                _write_initial_file(output_path, "# Context discovery\n\nThis handoff has not yet been authored by an AI.\n")
                input_paths = (root / "00-context" / "README.md", output_path)
            cycle_index = None
            artifact_kind: CommandRoomArtifactKind = container_artifact

        elif container == "planning":
            if container_artifact not in {"planning-forward", "planning-opposition", "spec"}:
                raise ValueError("Planning requires container_artifact planning-forward, planning-opposition, or spec.")
            if container_artifact == "spec":
                output_path = root / "01-planning" / "spec.md"
                input_paths = tuple(
                    _canonical_inputs(root, receipts)
                    + [
                        root / "01-planning" / "forward.md",
                        root / "01-planning" / "opposition.md",
                        output_path,
                    ]
                )
            else:
                filename = "forward.md" if container_artifact == "planning-forward" else "opposition.md"
                output_path = root / "01-planning" / filename
                input_paths = tuple(_canonical_inputs(root, receipts) + [root / "01-planning" / "README.md"])
            cycle_index = None
            artifact_kind: CommandRoomArtifactKind = container_artifact

        elif container == "technical-design":
            if container_artifact not in {"technical-forward", "technical-opposition", "technical-plan"}:
                raise ValueError("Technical Design requires container_artifact technical-forward, technical-opposition, or technical-plan.")
            if container_artifact == "technical-plan":
                output_path = root / "02-technical-design" / "technical-plan.md"
                input_paths = tuple(
                    _canonical_inputs(root, receipts)
                    + [
                        root / "02-technical-design" / "forward.md",
                        root / "02-technical-design" / "opposition.md",
                        output_path,
                    ]
                )
            else:
                filename = "forward.md" if container_artifact == "technical-forward" else "opposition.md"
                output_path = root / "02-technical-design" / filename
                input_paths = tuple(_canonical_inputs(root, receipts) + [root / "02-technical-design" / "README.md"])
            cycle_index = None
            artifact_kind = container_artifact

        elif container == "execution":
            if container_artifact is not None:
                raise ValueError("Execution does not accept container_artifact; it writes an execution note.")
            cycle_index = _validate_cycle_index(delivery_cycle_index)
            output_path = _task_note_path(root, container="execution", cycle_index=cycle_index, task_id=task_id)
            _write_initial_file(output_path, f"# Execution cycle {cycle_index}\n\nThis handoff has not yet been authored by an AI.\n")
            input_paths = _canonical_inputs(root, receipts)
            if cycle_index > 1:
                input_paths.append(root / "03-delivery" / f"cycle-{cycle_index - 1:02d}" / "review")
            input_paths.extend([root / "03-delivery" / "README.md", output_path])
            input_paths = tuple(input_paths)
            artifact_kind = "execution"

        elif container == "review":
            if container_artifact is not None:
                raise ValueError("Review does not accept container_artifact; it writes findings.")
            cycle_index = _validate_cycle_index(delivery_cycle_index)
            output_path = _task_note_path(root, container="review", cycle_index=cycle_index, task_id=task_id)
            _write_initial_file(output_path, f"# Review findings for cycle {cycle_index}\n\nThis handoff has not yet been authored by an AI.\n")
            input_paths = tuple(
                _canonical_inputs(root, receipts)
                + [
                    root / "03-delivery" / f"cycle-{cycle_index:02d}" / "execution",
                    output_path,
                ]
            )
            artifact_kind = "findings"

        elif container == "project-steward":
            if container_artifact is not None:
                raise ValueError("Project Steward does not accept container_artifact.")
            cycle_index = _validate_cycle_index(delivery_cycle_index)
            output_path = _governance_note_path(root, container="project-steward", task_id=task_id, cycle_index=cycle_index)
            _write_initial_file(output_path, f"# Project Steward after cycle {cycle_index}\n\nThis handoff has not yet been authored by an AI.\n")
            input_paths = tuple(
                _canonical_inputs(root, receipts)
                + [
                    root / "03-delivery" / f"cycle-{cycle_index:02d}" / "execution",
                    root / "03-delivery" / f"cycle-{cycle_index:02d}" / "review",
                    root / "04-governance" / "README.md",
                    output_path,
                ]
            )
            artifact_kind = "project-status"

        else:
            if container_artifact is not None or delivery_cycle_index is not None:
                raise ValueError("Debt and Learning Curators do not accept container_artifact or delivery_cycle_index.")
            cycle_index = None
            output_path = _governance_note_path(root, container=container, task_id=task_id)
            heading = "Debt Curation" if container == "debt-curation" else "Learning Curation"
            _write_initial_file(output_path, f"# {heading}\n\nThis handoff has not yet been authored by an AI.\n")
            steward_paths = [Path(str(row["artifact_path"])) for row in _matching_receipts(receipts, container="project-steward") if row.get("status") == "completed" and isinstance(row.get("artifact_path"), str)]
            input_paths = tuple(_canonical_inputs(root, receipts) + steward_paths + [root / "04-governance" / "README.md", output_path])
            artifact_kind = "debt" if container == "debt-curation" else "learning"

        if any(row.get("status") == "reserved" and row.get("artifact_path") == str(output_path) for row in _latest_receipts_by_task(receipts).values()):
            raise ValueError(f"Artifact path is already being written by another task: {output_path}")

        container_task = CommandRoomContainerTask(
            container=container,
            work_package_id=package_id,
            delivery_cycle_index=cycle_index,
            task_id=task_id,
            output_path=output_path,
            input_paths=input_paths,
            artifact_kind=artifact_kind,
            receipt_path=receipt_path,
            artifact_sha256_before=_artifact_sha256(output_path),
        )
        _append_receipt(
            receipt_path,
            {
                "status": "reserved",
                "container": container_task.container,
                "work_package_id": container_task.work_package_id,
                "delivery_cycle_index": container_task.delivery_cycle_index,
                "task_id": container_task.task_id,
                "artifact_path": str(container_task.output_path),
                "artifact_kind": container_task.artifact_kind,
                "artifact_sha256_before": container_task.artifact_sha256_before,
            },
        )
        return container_task


def format_ai_workspace_for_model(workspace_root: str | Path | None) -> str | None:
    if workspace_root is None:
        return None
    root = Path(workspace_root)
    lines = [
        AI_WORKSPACE_CONTEXT_HEADER,
        "The Chair can dispatch a task without using this workspace. Work package, container, and cycle values are optional factual labels for display and optional Markdown paths.",
        "Optional labels never authorize, block, sequence, or choose a task. Context, Planning, Technical Design, Execution, and Review artifacts may be used in any Chair-selected order.",
        "Independent tasks may run in parallel when their natural-language prompts define compatible scope and owned paths.",
        "A Review label applies the short landing-check boundary. Review findings never dispatch repair or another AI automatically.",
        "The separately retained close_task lifecycle may start fixed Project Steward, Debt Curator, and Learning Curator handoffs after explicit Chair status; their prose still returns to Chair judgment.",
        "Program code records optional labels, paths, hashes, and statuses only. AI roles author and interpret all natural-language content.",
        f"Root: {root}",
    ]
    for filename in AI_WORKSPACE_FILES:
        lines.append(f"- {filename}: {root / filename} — {_FILE_PURPOSES[filename]}")
    lines.append(f"- packages/<work_package_id>/: {root / 'packages'} — isolated work-package context, plans, delivery cycles, governance, and receipts.")
    lines.append(f"- 03-delivery/cycle-NN/execution/: {root / '03-delivery'} — execution notes for one Chair-declared cycle.")
    lines.append(f"- 03-delivery/cycle-NN/review/: {root / '03-delivery'} — independent findings passed back through the Chair.")
    lines.append(f"- 04-governance/: {root / '04-governance'} — explicit project status, debt, learning, and final governance handoffs.")
    return "\n".join(lines)


def format_container_task_for_model(container_task: CommandRoomContainerTask) -> str:
    labels = {
        "context": "Information Context",
        "planning": "Optional Planning",
        "technical-design": "Optional Technical Design",
        "execution": "Execution",
        "review": "Independent Review",
        "project-steward": "Project Steward",
        "debt-curation": "Debt Curation",
        "learning-curation": "Learning Curation",
    }
    lines = [
        "[Command Room AI-AI Handoff]",
        f"Factual label: {labels[container_task.container]}",
        f"Artifact: {container_task.artifact_kind}",
        f"Write your complete natural-language handoff to: {container_task.output_path}",
        "Update that Markdown artifact before returning the same complete natural result to the Chair.",
        "Read these relevant handoff paths before working:",
    ]
    lines.extend(f"- {path}" for path in container_task.input_paths)
    guidance = {
        "context-discovery": "Collect a bounded factual discovery angle from the Chair brief. Do not plan, decide, or execute delivery.",
        "context": "Record the Chair's factual context snapshot from the completed discovery handoffs unchanged; do not make a plan or decision yourself.",
        "planning-forward": "Develop the strongest forward route from the Chair brief. Do not read or answer the other angle.",
        "planning-opposition": "Independently expose contrary angles, hidden assumptions, boundaries, and alternatives. Do not review another AI's answer.",
        "spec": "Record the Chair's already-made unified direction, goal, boundary, plan, and acceptance shape unchanged; do not make the decision yourself.",
        "technical-forward": "Develop the strongest implementation route from the accepted goal. Do not read or answer the other angle.",
        "technical-opposition": "Independently expose technical failure modes, trade-offs, complexity, compatibility, and alternatives.",
        "technical-plan": "Record the Chair's already-made unified technical decision unchanged; do not make the decision yourself.",
        "execution": "Implement the Chair's bounded objective in the real workspace. Record actual changes, evidence, checks, limits, and unresolved facts; do not self-approve.",
        "findings": "Inspect the real result independently using checks proportionate to the goal. Record observed facts, deviations, evidence, required corrected state, and preserved correct work. Do not repair or launch another AI.",
        "project-status": (
            "Assess the project after the Chair-accepted task: state whether work should continue, the project is "
            "substantively complete, or a real blocker remains. Explain the next objective or completion basis; do not dispatch it yourself."
        ),
        "debt": "Classify concrete remaining technical, governance, documentation, and skill debt. Separate required closure work from optional backlog; do not edit project governance artifacts yourself.",
        "learning": "Identify only durable lessons that merit updates to Skills, AGENTS, Progress, tests, or references. Cite the concrete failure or success pattern and avoid speculative rule growth.",
    }
    lines.append(guidance[container_task.artifact_kind])
    if container_task.work_package_id is not None:
        lines.append(f"This handoff belongs only to work package {container_task.work_package_id}; do not read, alter, or coordinate another package.")
    if container_task.delivery_cycle_index is not None:
        lines.append(f"Delivery cycle {container_task.delivery_cycle_index} is a factual artifact label for this handoff.")
    return "\n".join(lines)


def container_artifact_is_ai_authored(container_task: CommandRoomContainerTask) -> bool:
    artifact_sha256 = _artifact_sha256(container_task.output_path)
    return artifact_sha256 is not None and artifact_sha256 != container_task.artifact_sha256_before


def record_container_task_completion(container_task: CommandRoomContainerTask) -> bool:
    try:
        with _container_receipts_lock(container_task.receipt_path):
            latest = _latest_receipts_by_task(_container_receipts(container_task.receipt_path)).get(container_task.task_id)
            if latest is None or latest.get("status") != "reserved":
                return False
            try:
                content = read_file_no_symlinks(container_task.output_path)
            except (OSError, ValueError):
                content = None
            written = content is not None and hashlib.sha256(content).hexdigest() != container_task.artifact_sha256_before
            payload: dict[str, object] = {
                "status": "completed" if written else "unwritten",
                "container": container_task.container,
                "work_package_id": container_task.work_package_id,
                "delivery_cycle_index": container_task.delivery_cycle_index,
                "task_id": container_task.task_id,
                "artifact_path": str(container_task.output_path),
                "artifact_kind": container_task.artifact_kind,
            }
            if content is not None:
                payload["artifact_sha256"] = hashlib.sha256(content).hexdigest()
                payload["artifact_bytes"] = len(content)
            _append_receipt(container_task.receipt_path, payload)
            return written
    except (OSError, ValueError):
        return False


def record_container_task_terminal(
    container_task: CommandRoomContainerTask,
    *,
    status: Literal["failed", "timed_out", "cancelled"],
) -> None:
    try:
        with _container_receipts_lock(container_task.receipt_path):
            latest = _latest_receipts_by_task(_container_receipts(container_task.receipt_path)).get(container_task.task_id)
            if latest is None or latest.get("status") != "reserved":
                return
            try:
                content = read_file_no_symlinks(container_task.output_path)
            except (OSError, ValueError):
                content = None
            payload: dict[str, object] = {
                "status": status,
                "container": container_task.container,
                "work_package_id": container_task.work_package_id,
                "delivery_cycle_index": container_task.delivery_cycle_index,
                "task_id": container_task.task_id,
                "artifact_path": str(container_task.output_path),
                "artifact_kind": container_task.artifact_kind,
                "artifact_sha256_before": container_task.artifact_sha256_before,
            }
            if content is not None:
                payload["artifact_sha256"] = hashlib.sha256(content).hexdigest()
                payload["artifact_bytes"] = len(content)
            _append_receipt(container_task.receipt_path, payload)
    except (OSError, ValueError):
        return


def accept_container_artifact(
    workspace_root: str | Path,
    *,
    artifact_kind: RecoverableContainerArtifact,
) -> dict[str, object]:
    """Record the Chair's explicit use of a changed optional-stage artifact."""

    container_by_artifact = {
        "planning-forward": "planning",
        "planning-opposition": "planning",
        "technical-forward": "technical-design",
        "technical-opposition": "technical-design",
    }
    container = container_by_artifact.get(artifact_kind)
    if container is None:
        raise ValueError("Only failed Planning or Technical Design angle artifacts can be accepted.")

    root = Path(workspace_root)
    receipt_path = command_room_container_receipts_path(root)
    with _container_receipts_lock(receipt_path):
        receipts = _container_receipts(receipt_path)
        candidates = _matching_receipts(receipts, container=container, artifact_kind=artifact_kind)
        latest = candidates[-1] if candidates else None
        if latest is None or latest.get("status") not in {"failed", "timed_out", "cancelled"}:
            raise ValueError(f"Artifact {artifact_kind} has no failed terminal handoff available for Chair acceptance.")
        artifact_path = latest.get("artifact_path")
        if not isinstance(artifact_path, str):
            raise ValueError(f"Artifact {artifact_kind} has no valid handoff path.")
        try:
            content = read_file_no_symlinks(Path(artifact_path))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Artifact {artifact_kind} cannot be read for Chair acceptance.") from exc
        artifact_sha256 = hashlib.sha256(content).hexdigest()
        artifact_sha256_before = latest.get("artifact_sha256_before")
        if not isinstance(artifact_sha256_before, str) or not artifact_sha256_before:
            artifact_sha256_before = next(
                (
                    receipt.get("artifact_sha256_before")
                    for receipt in reversed(receipts)
                    if receipt.get("task_id") == latest.get("task_id") and receipt.get("status") == "reserved" and isinstance(receipt.get("artifact_sha256_before"), str) and receipt.get("artifact_sha256_before")
                ),
                None,
            )
        if not isinstance(artifact_sha256_before, str) or not artifact_sha256_before:
            raise ValueError(f"Artifact {artifact_kind} has no trustworthy pre-task hash; retry it instead.")
        if artifact_sha256 == artifact_sha256_before:
            raise ValueError(f"Artifact {artifact_kind} was not changed by the failed child task; retry it instead.")

        payload: dict[str, object] = {
            "status": "completed",
            "container": container,
            "work_package_id": latest.get("work_package_id"),
            "delivery_cycle_index": latest.get("delivery_cycle_index"),
            "task_id": latest["task_id"],
            "artifact_path": artifact_path,
            "artifact_kind": artifact_kind,
            "artifact_sha256_before": artifact_sha256_before,
            "artifact_sha256": artifact_sha256,
            "artifact_bytes": len(content),
            "accepted_by_chair": True,
            "recovered_from_status": latest["status"],
        }
        _append_receipt(receipt_path, payload)
        return payload


__all__ = [
    "AI_WORKSPACE_CONTEXT_HEADER",
    "AI_WORKSPACE_DIRNAME",
    "AI_WORKSPACE_FILES",
    "COMMAND_ROOM_CONTAINERS",
    "CommandRoomArtifactKind",
    "CommandRoomContainer",
    "CommandRoomContainerTask",
    "ContainerArtifact",
    "ProjectLifecycleStatus",
    "RecoverableContainerArtifact",
    "accept_container_artifact",
    "command_room_ai_workspace_dir",
    "command_room_work_package_dir",
    "command_room_container_receipts_path",
    "container_artifact_is_ai_authored",
    "ensure_command_room_ai_workspace",
    "format_ai_workspace_for_model",
    "format_container_task_for_model",
    "latest_project_lifecycle_status",
    "prepare_command_room_container_task",
    "record_container_task_completion",
    "record_container_task_terminal",
    "record_project_lifecycle_status",
    "validate_work_package_id",
]
