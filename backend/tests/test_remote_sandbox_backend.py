from __future__ import annotations

import pytest
import requests

import deerflow.community.aio_sandbox.remote_backend as remote_backend_module
from deerflow.community.aio_sandbox.remote_backend import RemoteSandboxBackend
from deerflow.community.aio_sandbox.sandbox_info import SandboxInfo


class _StubResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object | None = None,
        json_exc: Exception | None = None,
    ):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self._json_exc = json_exc
        self.ok = 200 <= status_code < 400
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        if self._json_exc is not None:
            raise self._json_exc
        return self._payload


@pytest.fixture(autouse=True)
def _internal_auth_token(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", "test-control-plane-token")


def test_remote_backend_rejects_missing_internal_auth_token(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="DEER_FLOW_INTERNAL_AUTH_TOKEN"):
        RemoteSandboxBackend("http://provisioner:8002")


def test_list_running_delegates_to_provisioner_list(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")
    sandbox_info = SandboxInfo(sandbox_id="test-id", sandbox_url="http://localhost:8080")

    def mock_list():
        return [sandbox_info]

    monkeypatch.setattr(backend, "_provisioner_list", mock_list)

    assert backend.list_running() == [sandbox_info]


def test_provisioner_list_returns_sandbox_infos_and_filters_invalid_entries(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        assert url == "http://provisioner:8002/api/sandboxes"
        assert timeout == 10
        return _StubResponse(
            payload={
                "sandboxes": [
                    {
                        "sandbox_id": "abc123",
                        "sandbox_url": "http://k3s:31001",
                        "sandbox_api_key": "listed-sandbox-key",
                        "status": "Pending",
                        "ready": False,
                    },
                    {"sandbox_id": "missing-url"},
                    {"sandbox_url": "http://k3s:31002"},
                ]
            }
        )

    monkeypatch.setattr(requests, "get", mock_get)

    infos = backend._provisioner_list()
    assert len(infos) == 1
    assert infos[0].sandbox_id == "abc123"
    assert infos[0].sandbox_url == "http://k3s:31001"
    assert infos[0].sandbox_api_key == "listed-sandbox-key"
    assert infos[0].status == "Pending"
    assert infos[0].ready is False


def test_provisioner_list_raises_on_request_exception(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "get", mock_get)

    with pytest.raises(RuntimeError, match="Provisioner list failed"):
        backend._provisioner_list()


def test_provisioner_list_returns_empty_when_payload_is_not_dict(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        return _StubResponse(payload=[{"sandbox_id": "abc", "sandbox_url": "http://k3s:31001"}])

    monkeypatch.setattr(requests, "get", mock_get)

    assert backend._provisioner_list() == []


def test_provisioner_list_returns_empty_when_sandboxes_is_not_list(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        return _StubResponse(payload={"sandboxes": {"sandbox_id": "abc"}})

    monkeypatch.setattr(requests, "get", mock_get)

    assert backend._provisioner_list() == []


def test_provisioner_list_skips_non_dict_sandbox_entries(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        return _StubResponse(
            payload={
                "sandboxes": [
                    {"sandbox_id": "abc123", "sandbox_url": "http://k3s:31001"},
                    "bad-entry",
                    123,
                    None,
                ]
            }
        )

    monkeypatch.setattr(requests, "get", mock_get)

    infos = backend._provisioner_list()
    assert len(infos) == 1
    assert infos[0].sandbox_id == "abc123"
    assert infos[0].sandbox_url == "http://k3s:31001"


@pytest.mark.parametrize("expected_user_id", [None, "owner-1"])
def test_create_delegates_to_provisioner_create(monkeypatch, expected_user_id):
    backend = RemoteSandboxBackend("http://provisioner:8002")
    expected = SandboxInfo(sandbox_id="abc123", sandbox_url="http://k3s:31001")

    def mock_create(thread_id: str, sandbox_id: str, extra_mounts=None, *, user_id=None):
        assert thread_id == "thread-1"
        assert sandbox_id == "abc123"
        assert extra_mounts == [("/host", "/container", False)]
        assert user_id == expected_user_id
        return expected

    monkeypatch.setattr(backend, "_provisioner_create", mock_create)

    result = backend.create(
        "thread-1",
        "abc123",
        extra_mounts=[("/host", "/container", False)],
        user_id=expected_user_id,
    )
    assert result == expected


def test_provisioner_create_returns_sandbox_info(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", "expected-token")
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_post(url: str, json: dict, timeout: int, **kwargs):
        assert url == "http://provisioner:8002/api/sandboxes"
        assert kwargs["headers"] == {"X-DeerFlow-Internal-Token": "expected-token"}
        assert json == {
            "sandbox_id": "abc123",
            "thread_id": "thread-1",
            "user_id": "test-user-autouse",
        }
        assert timeout == 30
        return _StubResponse(
            payload={
                "sandbox_id": "abc123",
                "sandbox_url": "http://k3s:31001",
                "sandbox_api_key": "sandbox-only-key",
                "status": "Pending",
                "ready": False,
            }
        )

    monkeypatch.setattr(requests, "post", mock_post)

    info = backend._provisioner_create("thread-1", "abc123")
    assert info.sandbox_id == "abc123"
    assert info.sandbox_url == "http://k3s:31001"
    assert info.sandbox_api_key == "sandbox-only-key"
    assert info.status == "Pending"
    assert info.ready is False


def test_provisioner_create_rejects_anonymous_thread_id(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="thread_id"):
        backend.create(None, "anon123")


def test_provisioner_create_rejects_unknown_extra_mount():
    backend = RemoteSandboxBackend("http://provisioner:8002")

    with pytest.raises(ValueError, match="unsupported mount"):
        backend.create(
            "thread-1",
            "abc123",
            extra_mounts=[("/host/custom", "/mnt/custom", False)],
        )


def test_provisioner_create_raises_runtime_error_on_request_exception(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_post(url: str, json: dict, timeout: int, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "post", mock_post)

    with pytest.raises(RuntimeError, match="Provisioner create failed"):
        backend._provisioner_create("thread-1", "abc123")


def test_destroy_delegates_to_provisioner_destroy(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")
    called: list[str] = []

    def mock_destroy(sandbox_id: str):
        called.append(sandbox_id)

    monkeypatch.setattr(backend, "_provisioner_destroy", mock_destroy)

    backend.destroy(SandboxInfo(sandbox_id="abc123", sandbox_url="http://k3s:31001"))
    assert called == ["abc123"]


def test_provisioner_destroy_calls_delete(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_delete(url: str, timeout: int, **_kwargs):
        assert url == "http://provisioner:8002/api/sandboxes/abc123"
        assert timeout == 15
        return _StubResponse(status_code=200)

    monkeypatch.setattr(requests, "delete", mock_delete)

    backend._provisioner_destroy("abc123")


def test_provisioner_destroy_raises_on_request_exception(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_delete(url: str, timeout: int, **_kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "delete", mock_delete)

    with pytest.raises(RuntimeError, match="Provisioner destroy failed"):
        backend._provisioner_destroy("abc123")


def test_provisioner_destroy_raises_on_non_success(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    monkeypatch.setattr(
        requests,
        "delete",
        lambda *_args, **_kwargs: _StubResponse(status_code=503),
    )

    with pytest.raises(RuntimeError, match="HTTP 503"):
        backend._provisioner_destroy("abc123")


def test_is_alive_updates_info_from_provisioner_state(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_state(sandbox_id: str):
        assert sandbox_id == "abc123"
        return "Running", True

    monkeypatch.setattr(backend, "_provisioner_state", mock_state)
    info = SandboxInfo(sandbox_id="abc123", sandbox_url="http://k3s:31001")

    assert backend.is_alive(info) is True
    assert info.status == "Running"
    assert info.ready is True


def test_provisioner_is_alive_treats_pending_as_alive_but_not_ready(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get_pending(url: str, timeout: int, **_kwargs):
        return _StubResponse(payload={"status": "Pending", "ready": False})

    monkeypatch.setattr(requests, "get", mock_get_pending)
    info = SandboxInfo(sandbox_id="abc123", sandbox_url="http://k3s:31001")

    assert backend.is_alive(info) is True
    assert info.status == "Pending"
    assert info.ready is False


def test_provisioner_is_alive_tracks_running_not_ready(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get_running(url: str, timeout: int, **_kwargs):
        return _StubResponse(payload={"status": "Running", "ready": False})

    monkeypatch.setattr(requests, "get", mock_get_running)
    info = SandboxInfo(sandbox_id="abc123", sandbox_url="http://k3s:31001")

    assert backend.is_alive(info) is True
    assert info.status == "Running"
    assert info.ready is False


def test_provisioner_is_alive_returns_false_on_404(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        return _StubResponse(status_code=404)

    monkeypatch.setattr(requests, "get", mock_get)
    assert backend._provisioner_is_alive("abc123") is False


def test_provisioner_is_alive_raises_on_request_exception(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", mock_get)
    with pytest.raises(RuntimeError, match="Provisioner health check failed for abc123"):
        backend._provisioner_is_alive("abc123")


def test_provisioner_is_alive_raises_on_server_error(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        response = _StubResponse(status_code=503)
        response.text = "unavailable"
        return response

    monkeypatch.setattr(requests, "get", mock_get)
    with pytest.raises(RuntimeError, match="HTTP 503 unavailable"):
        backend._provisioner_is_alive("abc123")


def test_discover_delegates_to_provisioner_discover(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")
    expected = SandboxInfo(sandbox_id="abc123", sandbox_url="http://k3s:31001")

    def mock_discover(sandbox_id: str):
        assert sandbox_id == "abc123"
        return expected

    monkeypatch.setattr(backend, "_provisioner_discover", mock_discover)

    result = backend.discover("abc123")
    assert result == expected


def test_provisioner_discover_returns_none_on_404(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        return _StubResponse(status_code=404)

    monkeypatch.setattr(requests, "get", mock_get)

    assert backend._provisioner_discover("abc123") is None


def test_provisioner_discover_returns_info_on_success(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        return _StubResponse(
            payload={
                "sandbox_id": "abc123",
                "sandbox_url": "http://k3s:31001",
                "sandbox_api_key": "discovered-sandbox-key",
                "status": "Running",
                "ready": True,
            }
        )

    monkeypatch.setattr(requests, "get", mock_get)
    monkeypatch.setattr(
        remote_backend_module,
        "wait_for_sandbox_ready",
        lambda *_args, **_kwargs: True,
    )

    info = backend._provisioner_discover("abc123")
    assert info is not None
    assert info.sandbox_id == "abc123"
    assert info.sandbox_url == "http://k3s:31001"
    assert info.sandbox_api_key == "discovered-sandbox-key"
    assert info.ready is True


def test_provisioner_discover_rejects_pod_that_does_not_enforce_scoped_key(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: _StubResponse(
            payload={
                "sandbox_id": "abc123",
                "sandbox_url": "http://k3s:31001",
                "sandbox_api_key": "discovered-sandbox-key",
                "status": "Running",
                "ready": True,
            }
        ),
    )
    monkeypatch.setattr(
        remote_backend_module,
        "wait_for_sandbox_ready",
        lambda *_args, **_kwargs: False,
    )

    assert backend._provisioner_discover("abc123") is None


def test_provisioner_discover_defers_not_ready_sandbox_to_idempotent_create(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: _StubResponse(
            payload={
                "sandbox_id": "abc123",
                "sandbox_url": "http://k3s:31001",
                "sandbox_api_key": "discovered-sandbox-key",
                "status": "Running",
                "ready": False,
            }
        ),
    )

    assert backend._provisioner_discover("abc123") is None


def test_provisioner_discover_raises_on_request_exception(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", mock_get)

    with pytest.raises(RuntimeError, match="Provisioner discover failed for abc123"):
        backend._provisioner_discover("abc123")


def test_provisioner_discover_raises_on_server_error(monkeypatch):
    backend = RemoteSandboxBackend("http://provisioner:8002")

    def mock_get(url: str, timeout: int, **_kwargs):
        response = _StubResponse(status_code=503)
        response.text = "unavailable"
        return response

    monkeypatch.setattr(requests, "get", mock_get)

    with pytest.raises(RuntimeError, match="HTTP 503 unavailable"):
        backend._provisioner_discover("abc123")
