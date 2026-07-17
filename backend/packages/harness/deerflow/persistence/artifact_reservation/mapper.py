"""Closed typed mapper for Command Room fenced artifact logical paths."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from deerflow.config.paths import validate_thread_id, validate_user_id
from deerflow.persistence.artifact_reservation.types import FencedArtifactKey, FencedArtifactRoute
from deerflow.runtime.user_context import DEFAULT_USER_ID

_WORK_PACKAGE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class FencedArtifactPathError(ValueError):
    """A typed artifact route does not select one canonical Markdown path."""


def _package_prefix(work_package_id: str | None) -> tuple[str, ...]:
    if work_package_id is None:
        return ()
    if not _WORK_PACKAGE_ID_RE.fullmatch(work_package_id):
        raise FencedArtifactPathError("invalid work_package_id")
    return ("packages", work_package_id)


def _task_digest(task_id: str | None) -> str:
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise FencedArtifactPathError("task_id is required for this artifact")
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]


def _cycle(cycle_index: int | None) -> str:
    if isinstance(cycle_index, bool) or not isinstance(cycle_index, int) or cycle_index < 1:
        raise FencedArtifactPathError("positive delivery_cycle_index is required for this artifact")
    return f"cycle-{cycle_index:02d}"


class CanonicalArtifactMapper:
    """Maps only validated typed fields; it deliberately accepts no path string."""

    def map(
        self,
        *,
        user_id: str | None,
        thread_id: str,
        route: FencedArtifactRoute,
    ) -> FencedArtifactKey:
        owner = validate_user_id(DEFAULT_USER_ID if user_id is None else user_id)
        thread = validate_thread_id(thread_id)
        prefix = _package_prefix(route.work_package_id)
        container, kind = route.container, route.artifact_kind

        if (container, kind) == ("context", "context"):
            parts = (*prefix, "00-context", "context.md")
        elif (container, kind) == ("context", "context-discovery"):
            parts = (*prefix, "00-context", "discovery", f"discovery-{_task_digest(route.task_id)}.md")
        elif (container, kind) == ("planning", "planning-forward"):
            parts = (*prefix, "01-planning", "forward.md")
        elif (container, kind) == ("planning", "planning-opposition"):
            parts = (*prefix, "01-planning", "opposition.md")
        elif (container, kind) == ("planning", "spec"):
            parts = (*prefix, "01-planning", "spec.md")
        elif (container, kind) == ("technical-design", "technical-forward"):
            parts = (*prefix, "02-technical-design", "forward.md")
        elif (container, kind) == ("technical-design", "technical-opposition"):
            parts = (*prefix, "02-technical-design", "opposition.md")
        elif (container, kind) == ("technical-design", "technical-plan"):
            parts = (*prefix, "02-technical-design", "technical-plan.md")
        elif (container, kind) == ("execution", "execution"):
            parts = (*prefix, "03-delivery", _cycle(route.delivery_cycle_index), "execution", f"task-{_task_digest(route.task_id)}.md")
        elif (container, kind) == ("review", "findings"):
            parts = (*prefix, "03-delivery", _cycle(route.delivery_cycle_index), "review", f"findings-{_task_digest(route.task_id)}.md")
        elif (container, kind) == ("project-steward", "project-status"):
            parts = (*prefix, "04-governance", "project-steward", f"{_cycle(route.delivery_cycle_index)}-{_task_digest(route.task_id)}.md")
        elif (container, kind) == ("debt-curation", "debt"):
            parts = (*prefix, "04-governance", "debt", f"curation-{_task_digest(route.task_id)}.md")
        elif (container, kind) == ("learning-curation", "learning"):
            parts = (*prefix, "04-governance", "learning", f"curation-{_task_digest(route.task_id)}.md")
        else:
            raise FencedArtifactPathError("unknown fenced Command Room artifact route")

        path = PurePosixPath(*parts)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise FencedArtifactPathError("invalid canonical artifact path")
        return FencedArtifactKey(
            user_id=owner,
            thread_id=thread,
            canonical_artifact_path=path.as_posix(),
            route=route,
        )
