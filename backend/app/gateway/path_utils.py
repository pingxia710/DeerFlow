"""Shared path resolution for thread virtual paths (e.g. mnt/user-data/outputs/...)."""

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.gateway.internal_auth import get_trusted_internal_owner_user_id
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id


def get_request_storage_user_id(request: Any | None) -> str:
    """Resolve the user bucket for request-scoped filesystem paths."""
    if request is not None:
        owner_user_id = get_trusted_internal_owner_user_id(request)
        if owner_user_id:
            return owner_user_id

        state = getattr(request, "state", None)
        user = getattr(getattr(state, "auth", None), "user", None) or getattr(
            state,
            "user",
            None,
        )
        user_id = getattr(user, "id", None)
        if isinstance(user_id, UUID):
            return str(user_id)
        if isinstance(user_id, str) and user_id:
            return user_id

    return get_effective_user_id()


def resolve_thread_virtual_path(thread_id: str, virtual_path: str, *, user_id: str | None = None) -> Path:
    """Resolve a virtual path to the actual filesystem path under thread user-data.

    Args:
        thread_id: The thread ID.
        virtual_path: The virtual path as seen inside the sandbox
                      (e.g., /mnt/user-data/outputs/file.txt).

    Returns:
        The resolved filesystem path.

    Raises:
        HTTPException: If the path is invalid or outside allowed directories.
    """
    try:
        return get_paths().resolve_virtual_path(thread_id, virtual_path, user_id=user_id or get_effective_user_id())
    except ValueError as e:
        status = 403 if "traversal" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
