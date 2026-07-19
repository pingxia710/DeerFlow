"""AI-readable capability snapshot API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.gateway.authz import require_auth, require_permission
from app.gateway.deps import get_config
from app.gateway.path_utils import get_request_storage_user_id
from deerflow.capabilities import build_capability_snapshot
from deerflow.config.app_config import AppConfig

router = APIRouter(prefix="/api", tags=["capabilities"])


def _initialization_snapshot(request: Request) -> Mapping[str, Any] | None:
    facts = getattr(request.app.state, "capability_initialization_snapshot", None)
    return facts if isinstance(facts, Mapping) else None


@router.get("/capabilities")
@require_auth
async def get_capabilities(request: Request, config: AppConfig = Depends(get_config)) -> dict[str, Any]:
    return build_capability_snapshot(
        config,
        user_id=get_request_storage_user_id(request),
        initialization_snapshot=_initialization_snapshot(request),
    )


@router.get("/threads/{thread_id}/capabilities")
@require_permission("threads", "read", owner_check=True)
async def get_thread_capabilities(thread_id: str, request: Request, config: AppConfig = Depends(get_config)) -> dict[str, Any]:
    return build_capability_snapshot(
        config,
        thread_id=thread_id,
        user_id=get_request_storage_user_id(request),
        initialization_snapshot=_initialization_snapshot(request),
    )
