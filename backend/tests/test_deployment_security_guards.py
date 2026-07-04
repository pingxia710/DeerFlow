import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from app.gateway.routers.auth import InitializeAdminRequest, _validate_first_boot_setup_access
from app.gateway.routers.runs import stateless_wait


class DummyClient:
    def __init__(self, host: str):
        self.host = host


class DummyRequest:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.client = DummyClient(host)
        self.headers = Headers(headers or {})


def _init_body(token: str | None = None) -> InitializeAdminRequest:
    return InitializeAdminRequest(email="admin@example.com", password="StrongPassword123", setup_token=token)


def test_initialize_allows_loopback_without_setup_token(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    _validate_first_boot_setup_access(DummyRequest("127.0.0.1"), _init_body())


def test_initialize_rejects_remote_without_token_or_explicit_allow(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REMOTE_INITIALIZE", raising=False)

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(DummyRequest("203.0.113.10"), _init_body())

    assert exc.value.status_code == 403
    assert "setup token" in exc.value.detail


def test_initialize_accepts_setup_token_header(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SETUP_TOKEN", "expected-token")
    _validate_first_boot_setup_access(
        DummyRequest("203.0.113.10", {"x-deer-flow-setup-token": "expected-token"}),
        _init_body(),
    )


def test_initialize_rejects_bad_setup_token(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SETUP_TOKEN", "expected-token")

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(DummyRequest("127.0.0.1"), _init_body("wrong"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_stateless_wait_sanitizes_failed_run_error(monkeypatch):
    from types import SimpleNamespace

    from deerflow.runtime import RunStatus

    async def fake_start_run(body, thread_id, request):
        return SimpleNamespace(run_id="run-1", task=None, status=RunStatus.error, error="secret raw stack trace")

    monkeypatch.setattr("app.gateway.routers.runs.start_run", fake_start_run)
    monkeypatch.setattr("app.gateway.routers.runs.get_stream_bridge", lambda request: object())

    class FakeRunManager:
        async def get(self, run_id):
            return None

    monkeypatch.setattr("app.gateway.routers.runs.get_run_manager", lambda request: FakeRunManager())

    result = await stateless_wait.__wrapped__(SimpleNamespace(config={}), object())

    assert result == {"status": "error", "error": "Run failed"}
