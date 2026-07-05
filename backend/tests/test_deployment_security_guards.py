import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from app.gateway.app import _assert_run_event_store_config_for_environment, _assert_safe_sandbox_config_for_environment
from app.gateway.routers.auth import InitializeAdminRequest, _validate_first_boot_setup_access
from app.gateway.routers.runs import stateless_wait
from app.gateway.routers.thread_runs import run_error_for_response
from deerflow.config.app_config import AppConfig
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.run_events_config import RunEventsConfig
from deerflow.config.sandbox_config import SandboxConfig

_ENV_KEYS = ("DEER_FLOW_ENV", "ENVIRONMENT", "APP_ENV", "NODE_ENV")


class DummyClient:
    def __init__(self, host: str):
        self.host = host


class DummyRequest:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.client = DummyClient(host)
        self.headers = Headers(headers or {})


def _init_body(token: str | None = None) -> InitializeAdminRequest:
    return InitializeAdminRequest(email="admin@example.com", password="StrongPassword123", setup_token=token)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _config(
    *,
    database_backend: str = "memory",
    run_events_backend: str = "memory",
    **sandbox_overrides,
) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(backend=database_backend),
        run_events=RunEventsConfig(backend=run_events_backend),
        sandbox=SandboxConfig(
            use="deerflow.sandbox.local:LocalSandboxProvider",
            **sandbox_overrides,
        ),
    )


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
        async def get(self, run_id, *, user_id=None):
            return None

    monkeypatch.setattr("app.gateway.routers.runs.get_run_manager", lambda request: FakeRunManager())

    result = await stateless_wait.__wrapped__(SimpleNamespace(config={}), object())

    assert result == {"status": "error", "error": "Run failed"}


def test_run_error_response_extracts_traceback_message():
    error = """Traceback (most recent call last):
  File "/private/app.py", line 1, in <module>
RuntimeError: boom"""

    assert run_error_for_response(error) == "boom"


def test_run_error_response_hides_traceback_without_public_message():
    error = """Traceback (most recent call last):
  File "/private/app.py", line 1, in <module>
    raise RuntimeError("hidden")"""

    assert run_error_for_response(error) == "Run failed"


def test_dangerous_sandbox_settings_allowed_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "development")

    _assert_safe_sandbox_config_for_environment(
        _config(
            allow_host_bash=True,
            unrestricted_host_access=True,
            allow_dangerous_host_mounts=True,
            seccomp_unconfined=True,
        )
    )


def test_dangerous_sandbox_settings_allowed_when_environment_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    _assert_safe_sandbox_config_for_environment(_config(allow_host_bash=True))


@pytest.mark.parametrize(
    "field",
    [
        "allow_host_bash",
        "unrestricted_host_access",
        "allow_dangerous_host_mounts",
        "seccomp_unconfined",
    ],
)
def test_dangerous_sandbox_settings_fail_fast_in_production(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match=field):
        _assert_safe_sandbox_config_for_environment(_config(**{field: True}))


def test_safe_sandbox_settings_allowed_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    _assert_safe_sandbox_config_for_environment(_config())


@pytest.mark.parametrize("backend", ["memory", "jsonl"])
def test_run_event_store_non_db_fails_fast_in_production(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="run_events.backend='db'"):
        _assert_run_event_store_config_for_environment(_config(run_events_backend=backend))


def test_run_event_store_db_requires_persistent_database_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="database.backend"):
        _assert_run_event_store_config_for_environment(_config(run_events_backend="db"))


def test_run_event_store_db_allowed_with_sqlite_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    _assert_run_event_store_config_for_environment(_config(database_backend="sqlite", run_events_backend="db"))


def test_run_event_store_jsonl_allowed_when_environment_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    _assert_run_event_store_config_for_environment(_config(run_events_backend="jsonl"))
