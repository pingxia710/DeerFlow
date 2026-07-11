import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers


def test_provisioner_api_requires_internal_token(provisioner_module):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "expected-token"
    guard = getattr(provisioner_module, "_require_internal_token", None)
    assert callable(guard), "provisioner must expose an internal auth guard"

    with pytest.raises(HTTPException) as exc:
        guard(SimpleNamespace(headers=Headers()))

    assert exc.value.status_code == 401


def test_provisioner_api_accepts_internal_token(provisioner_module):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "expected-token"
    guard = getattr(provisioner_module, "_require_internal_token", None)
    assert callable(guard), "provisioner must expose an internal auth guard"

    guard(SimpleNamespace(headers=Headers({"x-deerflow-internal-token": "expected-token"})))


def test_all_provisioner_control_routes_use_internal_auth(provisioner_module):
    guard = getattr(provisioner_module, "_require_internal_token", None)
    assert callable(guard), "provisioner must expose an internal auth guard"

    control_routes = [route for route in provisioner_module.app.routes if getattr(route, "path", "").startswith("/api/sandboxes")]
    assert control_routes
    assert all(guard in [dependency.call for dependency in route.dependant.dependencies] for route in control_routes)


def test_provisioner_control_routes_run_in_fastapi_threadpool(provisioner_module):
    """Sync route handlers keep blocking Kubernetes client calls off the event loop."""
    for handler in (
        provisioner_module.create_sandbox,
        provisioner_module.destroy_sandbox,
        provisioner_module.get_sandbox,
        provisioner_module.list_sandboxes,
    ):
        assert not inspect.iscoroutinefunction(handler)


def test_provisioner_kubernetes_reads_have_bounded_timeout(provisioner_module, monkeypatch):
    pod = SimpleNamespace(
        status=SimpleNamespace(
            phase="Running",
            container_statuses=[SimpleNamespace(name="sandbox", ready=True)],
        ),
        spec=SimpleNamespace(containers=[]),
    )
    core = SimpleNamespace(read_namespaced_pod=MagicMock(return_value=pod))
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    assert provisioner_module._get_pod_state("sandbox-1") == ("Running", True)
    core.read_namespaced_pod.assert_called_once_with(
        "sandbox-sandbox-1",
        provisioner_module.K8S_NAMESPACE,
        _request_timeout=provisioner_module.K8S_REQUEST_TIMEOUT,
    )


@pytest.mark.parametrize(
    "helper_name",
    ["_get_node_port", "_get_pod_state", "_get_sandbox_api_key"],
)
def test_provisioner_non_not_found_kubernetes_reads_surface_503(
    provisioner_module,
    monkeypatch,
    helper_name,
):
    failure = provisioner_module.ApiException(status=500, reason="kubernetes unavailable")
    core = SimpleNamespace(
        read_namespaced_service=MagicMock(side_effect=failure),
        read_namespaced_pod=MagicMock(side_effect=failure),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    with pytest.raises(HTTPException) as exc:
        getattr(provisioner_module, helper_name)("sandbox-1")

    assert exc.value.status_code == 503


def test_existing_sandbox_reads_one_pod_snapshot(provisioner_module, monkeypatch):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "control-token"
    service = SimpleNamespace(
        spec=SimpleNamespace(
            ports=[SimpleNamespace(name="http", node_port=31001)],
        )
    )
    pod = SimpleNamespace(
        status=SimpleNamespace(
            phase="Running",
            container_statuses=[SimpleNamespace(name="sandbox", ready=True)],
        ),
        spec=SimpleNamespace(
            containers=[
                SimpleNamespace(
                    name="sandbox",
                    env=[SimpleNamespace(name="SANDBOX_API_KEY", value="sandbox-key")],
                )
            ]
        ),
    )
    core = SimpleNamespace(
        read_namespaced_service=MagicMock(return_value=service),
        read_namespaced_pod=MagicMock(return_value=pod),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    response = provisioner_module.create_sandbox(
        provisioner_module.CreateSandboxRequest(
            sandbox_id="sandbox-existing",
            thread_id="thread-1",
            user_id="user-1",
        )
    )

    assert response.ready is True
    assert response.sandbox_api_key == "sandbox-key"
    assert core.read_namespaced_pod.call_count == 1


def test_existing_service_without_pod_recreates_missing_pod(provisioner_module, monkeypatch):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "control-token"
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: 31001)
    snapshots = iter(
        [
            None,
            provisioner_module.SandboxPodSnapshot(
                status="Pending",
                ready=False,
                sandbox_api_key="new-pod-key",
            ),
        ]
    )
    monkeypatch.setattr(
        provisioner_module,
        "_get_pod_snapshot",
        lambda _sandbox_id: next(snapshots),
    )
    monkeypatch.setattr(
        provisioner_module.secrets,
        "token_urlsafe",
        lambda _size: "new-pod-key",
    )
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        create_namespaced_service=MagicMock(
            side_effect=provisioner_module.ApiException(status=409),
        ),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    response = provisioner_module.create_sandbox(
        provisioner_module.CreateSandboxRequest(
            sandbox_id="sandbox-service-only",
            thread_id="thread-1",
            user_id="user-1",
        )
    )

    assert response.sandbox_api_key == "new-pod-key"
    core.create_namespaced_pod.assert_called_once()


def test_create_waits_for_delayed_node_port_without_rolling_back(
    provisioner_module,
    monkeypatch,
):
    node_ports = iter([None, None, 31001])
    monkeypatch.setattr(
        provisioner_module,
        "_get_node_port",
        lambda _sandbox_id: next(node_ports),
    )
    sleep = MagicMock()
    monkeypatch.setattr(provisioner_module.time, "sleep", sleep)
    monkeypatch.setattr(
        provisioner_module,
        "_get_pod_snapshot",
        lambda _sandbox_id: provisioner_module.SandboxPodSnapshot(
            status="Pending",
            ready=False,
            sandbox_api_key="sandbox-key",
        ),
    )
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        create_namespaced_service=MagicMock(),
        delete_namespaced_service=MagicMock(),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    response = provisioner_module.create_sandbox(
        provisioner_module.CreateSandboxRequest(
            sandbox_id="sandbox-delayed-node-port",
            thread_id="thread-1",
            user_id="user-1",
        )
    )

    assert response.sandbox_url.endswith(":31001")
    sleep.assert_called_once_with(0.5)
    core.delete_namespaced_service.assert_not_called()
    core.delete_namespaced_pod.assert_not_called()


def test_node_port_timeout_compensates_created_service_and_pod(provisioner_module, monkeypatch):
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: None)
    monkeypatch.setattr(provisioner_module.time, "sleep", lambda _delay: None)
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        create_namespaced_service=MagicMock(),
        delete_namespaced_service=MagicMock(),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    with pytest.raises(HTTPException, match="NodePort was not allocated"):
        provisioner_module.create_sandbox(
            provisioner_module.CreateSandboxRequest(
                sandbox_id="sandbox-timeout",
                thread_id="thread-1",
                user_id="user-1",
            )
        )

    core.delete_namespaced_service.assert_called_once()
    core.delete_namespaced_pod.assert_called_once()


def test_node_port_timeout_removes_preexisting_broken_service(provisioner_module, monkeypatch):
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: None)
    monkeypatch.setattr(provisioner_module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        provisioner_module,
        "_get_pod_snapshot",
        lambda _sandbox_id: provisioner_module.SandboxPodSnapshot(
            status="Running",
            ready=True,
            sandbox_api_key="existing-pod-key",
        ),
    )
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(
            side_effect=provisioner_module.ApiException(status=409),
        ),
        create_namespaced_service=MagicMock(
            side_effect=provisioner_module.ApiException(status=409),
        ),
        delete_namespaced_service=MagicMock(),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    with pytest.raises(HTTPException, match="NodePort was not allocated"):
        provisioner_module.create_sandbox(
            provisioner_module.CreateSandboxRequest(
                sandbox_id="sandbox-broken-service",
                thread_id="thread-1",
                user_id="user-1",
            )
        )

    core.delete_namespaced_service.assert_called_once()
    core.delete_namespaced_pod.assert_not_called()


def test_service_creation_failure_reports_failed_pod_rollback(provisioner_module, monkeypatch):
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: None)
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        create_namespaced_service=MagicMock(side_effect=provisioner_module.ApiException(status=500, reason="service failed")),
        delete_namespaced_service=MagicMock(),
        delete_namespaced_pod=MagicMock(side_effect=provisioner_module.ApiException(status=503, reason="rollback failed")),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    with pytest.raises(HTTPException) as exc:
        provisioner_module.create_sandbox(
            provisioner_module.CreateSandboxRequest(
                sandbox_id="sandbox-rollback",
                thread_id="thread-1",
                user_id="user-1",
            )
        )

    assert exc.value.status_code == 500
    assert "rollback failed" in exc.value.detail


def test_service_transport_failure_compensates_created_resources(
    provisioner_module,
    monkeypatch,
):
    monkeypatch.setattr(provisioner_module, "_get_node_port", lambda _sandbox_id: None)
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        create_namespaced_service=MagicMock(
            side_effect=RuntimeError("service request lost"),
        ),
        delete_namespaced_service=MagicMock(),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    with pytest.raises(HTTPException, match="Service creation failed"):
        provisioner_module.create_sandbox(
            provisioner_module.CreateSandboxRequest(
                sandbox_id="sandbox-service-transport",
                thread_id="thread-1",
                user_id="user-1",
            )
        )

    core.delete_namespaced_service.assert_called_once()
    core.delete_namespaced_pod.assert_called_once()


def test_pod_create_transport_failure_recovers_committed_pod(
    provisioner_module,
    monkeypatch,
):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "control-token"
    monkeypatch.setattr(
        provisioner_module.secrets,
        "token_urlsafe",
        lambda _size: "committed-pod-key",
    )
    node_ports = iter([None, 31001])
    monkeypatch.setattr(
        provisioner_module,
        "_get_node_port",
        lambda _sandbox_id: next(node_ports),
    )
    monkeypatch.setattr(
        provisioner_module,
        "_get_pod_snapshot",
        lambda _sandbox_id: provisioner_module.SandboxPodSnapshot(
            status="Pending",
            ready=False,
            sandbox_api_key="committed-pod-key",
        ),
    )
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(
            side_effect=RuntimeError("response lost after pod commit"),
        ),
        create_namespaced_service=MagicMock(),
        delete_namespaced_service=MagicMock(),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    response = provisioner_module.create_sandbox(
        provisioner_module.CreateSandboxRequest(
            sandbox_id="sandbox-response-lost",
            thread_id="thread-1",
            user_id="user-1",
        )
    )

    assert response.sandbox_url.endswith(":31001")
    assert response.sandbox_api_key == "committed-pod-key"
    core.create_namespaced_service.assert_called_once()
    core.delete_namespaced_pod.assert_not_called()


def test_destroy_attempts_both_resources_when_one_delete_has_transport_error(
    provisioner_module,
    monkeypatch,
):
    core = SimpleNamespace(
        delete_namespaced_service=MagicMock(
            side_effect=RuntimeError("service delete timed out"),
        ),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    with pytest.raises(HTTPException) as exc:
        provisioner_module.destroy_sandbox("sandbox-partial-delete")

    assert exc.value.status_code == 500
    assert "service delete timed out" in exc.value.detail
    core.delete_namespaced_pod.assert_called_once()


def test_create_and_destroy_same_sandbox_are_serialized(provisioner_module, monkeypatch):
    create_entered = threading.Event()
    allow_create = threading.Event()
    delete_started = threading.Event()
    node_port_calls = 0

    def get_node_port(_sandbox_id: str):
        nonlocal node_port_calls
        node_port_calls += 1
        if node_port_calls == 1:
            create_entered.set()
            assert allow_create.wait(timeout=2)
            return None
        return 31001

    def mark_delete(*_args, **_kwargs):
        delete_started.set()

    monkeypatch.setattr(provisioner_module, "_get_node_port", get_node_port)
    monkeypatch.setattr(
        provisioner_module,
        "_get_pod_snapshot",
        lambda _sandbox_id: provisioner_module.SandboxPodSnapshot(
            status="Running",
            ready=True,
            sandbox_api_key="key",
        ),
    )
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(),
        create_namespaced_service=MagicMock(),
        delete_namespaced_service=MagicMock(side_effect=mark_delete),
        delete_namespaced_pod=MagicMock(side_effect=mark_delete),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)
    request = provisioner_module.CreateSandboxRequest(
        sandbox_id="sandbox-serialized",
        thread_id="thread-1",
        user_id="user-1",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(provisioner_module.create_sandbox, request)
        assert create_entered.wait(timeout=1)
        destroy_future = executor.submit(
            provisioner_module.destroy_sandbox,
            "sandbox-serialized",
        )
        try:
            assert not delete_started.wait(timeout=0.1)
        finally:
            allow_create.set()
        create_future.result(timeout=2)
        destroy_future.result(timeout=2)

    assert delete_started.is_set()


def test_provisioned_sandbox_uses_scoped_key_not_internal_control_token(provisioner_module):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "expected-token"

    pod = provisioner_module._build_pod(
        "sandbox-1",
        "thread-1",
        user_id="user-1",
        sandbox_api_key="sandbox-only-key",
    )

    container = pod.spec.containers[0]
    environment = {item.name: item.value for item in container.env or []}
    assert environment["SANDBOX_API_KEY"] == "sandbox-only-key"
    assert environment["SANDBOX_API_KEY"] != provisioner_module.PROVISIONER_AUTH_TOKEN
    readiness_headers = {item.name: item.value for item in container.readiness_probe.http_get.http_headers or []}
    assert readiness_headers["X-AIO-API-Key"] == "sandbox-only-key"


def test_concurrent_pod_create_uses_existing_pod_key(provisioner_module, monkeypatch):
    provisioner_module.PROVISIONER_AUTH_TOKEN = "control-token"
    monkeypatch.setattr(
        provisioner_module.secrets,
        "token_urlsafe",
        lambda _size: "new-request-key",
    )
    node_ports = iter([None, 31001])
    monkeypatch.setattr(
        provisioner_module,
        "_get_node_port",
        lambda _sandbox_id: next(node_ports),
    )
    monkeypatch.setattr(
        provisioner_module,
        "_get_pod_snapshot",
        lambda _sandbox_id: provisioner_module.SandboxPodSnapshot(
            status="Running",
            ready=True,
            sandbox_api_key="existing-pod-key",
        ),
    )
    core = SimpleNamespace(
        create_namespaced_pod=MagicMock(side_effect=provisioner_module.ApiException(status=409)),
        create_namespaced_service=MagicMock(side_effect=provisioner_module.ApiException(status=409)),
        delete_namespaced_pod=MagicMock(),
    )
    monkeypatch.setattr(provisioner_module, "core_v1", core)

    response = provisioner_module.create_sandbox(
        provisioner_module.CreateSandboxRequest(
            sandbox_id="sandbox-race",
            thread_id="thread-1",
            user_id="user-1",
        )
    )

    assert response.sandbox_api_key == "existing-pod-key"
    core.delete_namespaced_pod.assert_not_called()
