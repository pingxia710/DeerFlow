from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import memory


@pytest.mark.parametrize(
    "method, path, json_body",
    [
        ("GET", "/api/memory", None),
        ("POST", "/api/memory/reload", None),
        ("DELETE", "/api/memory", None),
        ("POST", "/api/memory/facts", {"content": "remember this"}),
        ("DELETE", "/api/memory/facts/fact-1", None),
        ("PATCH", "/api/memory/facts/fact-1", {"content": "updated"}),
        ("GET", "/api/memory/export", None),
        ("POST", "/api/memory/import", {}),
        ("GET", "/api/memory/config", None),
        ("GET", "/api/memory/status", None),
    ],
)
def test_memory_routes_require_auth_when_mounted_without_global_middleware(
    method: str,
    path: str,
    json_body: dict | None,
):
    app = FastAPI()
    app.include_router(memory.router)

    with TestClient(app) as client:
        response = client.request(method, path, json=json_body)

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
