"""Per-user model assignments for reusable professional roles."""

import json
import logging
import tempfile
import threading
from pathlib import Path
from weakref import WeakValueDictionary

from pydantic import BaseModel, Field, ValidationError

from deerflow.config.model_config import ReasoningEffort
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

_ROLE_ASSIGNMENT_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_ROLE_ASSIGNMENT_LOCKS_GUARD = threading.Lock()


class RoleAssignment(BaseModel):
    """Model settings applied when the lead AI selects a professional role."""

    model: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort | None = None


class RoleAssignments(BaseModel):
    """User-owned role assignments keyed by role name."""

    roles: dict[str, RoleAssignment] = Field(default_factory=dict)


def load_role_assignments(user_id: str) -> RoleAssignments:
    """Load a user's role assignments, returning an empty config when absent."""
    path = get_paths().user_role_assignments_file(user_id)
    if not path.exists():
        return RoleAssignments()
    try:
        return RoleAssignments.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        logger.error("Ignoring invalid role assignments at %s", path, exc_info=True)
        return RoleAssignments()


def save_role_assignments(user_id: str, assignments: RoleAssignments) -> None:
    """Atomically replace a user's role-assignment file."""
    path = get_paths().user_role_assignments_file(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(assignments.model_dump(mode="json"), temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        temp_path.replace(path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _role_assignment_lock(user_id: str) -> threading.Lock:
    with _ROLE_ASSIGNMENT_LOCKS_GUARD:
        lock = _ROLE_ASSIGNMENT_LOCKS.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _ROLE_ASSIGNMENT_LOCKS[user_id] = lock
        return lock


def update_role_assignment(user_id: str, name: str, assignment: RoleAssignment) -> RoleAssignments:
    """Update one role without losing concurrent updates for another role."""
    with _role_assignment_lock(user_id):
        assignments = load_role_assignments(user_id)
        assignments.roles[name] = assignment
        save_role_assignments(user_id, assignments)
        return assignments
