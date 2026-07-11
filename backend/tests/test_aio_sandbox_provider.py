"""Tests for AioSandboxProvider mount helpers."""

import asyncio
import gc
import importlib
import weakref
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.config.paths import Paths, join_host_path
from deerflow.runtime.user_context import reset_current_user, set_current_user

# ── ensure_thread_dirs ───────────────────────────────────────────────────────


def test_ensure_thread_dirs_creates_acp_workspace(tmp_path):
    """ACP workspace directory must be created alongside user-data dirs."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-1")

    assert (tmp_path / "threads" / "thread-1" / "user-data" / "workspace").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "uploads").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "outputs").exists()
    assert (tmp_path / "threads" / "thread-1" / "acp-workspace").exists()


def test_ensure_thread_dirs_acp_workspace_is_world_writable(tmp_path):
    """ACP workspace must be chmod 0o777 so the ACP subprocess can write into it."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-2")

    acp_dir = tmp_path / "threads" / "thread-2" / "acp-workspace"
    mode = oct(acp_dir.stat().st_mode & 0o777)
    assert mode == oct(0o777)


def test_host_thread_dir_rejects_invalid_thread_id(tmp_path):
    paths = Paths(base_dir=tmp_path)

    with pytest.raises(ValueError, match="Invalid thread_id"):
        paths.host_thread_dir("../escape")


# ── _get_thread_mounts ───────────────────────────────────────────────────────


def _make_provider(tmp_path):
    """Build a minimal AioSandboxProvider instance without starting the idle checker."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    with patch.object(aio_mod.AioSandboxProvider, "_start_idle_checker"):
        provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
        provider._config = {}
        provider._sandboxes = {}
        provider._lock = MagicMock()
        provider._idle_checker_stop = MagicMock()
    return provider


def test_load_config_defaults_seccomp_unconfined_to_false(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    sandbox_config = SimpleNamespace(
        image=None,
        port=None,
        container_prefix=None,
        idle_timeout=None,
        replicas=None,
        mounts=[],
        environment={},
        provisioner_url=None,
    )
    monkeypatch.setattr(aio_mod, "get_app_config", lambda: SimpleNamespace(sandbox=sandbox_config))

    loaded = provider._load_config()

    assert loaded["seccomp_unconfined"] is False


def test_load_config_allows_explicit_seccomp_unconfined(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    sandbox_config = SimpleNamespace(
        image=None,
        port=None,
        container_prefix=None,
        idle_timeout=None,
        replicas=None,
        mounts=[],
        environment={},
        provisioner_url=None,
        seccomp_unconfined=True,
    )
    monkeypatch.setattr(aio_mod, "get_app_config", lambda: SimpleNamespace(sandbox=sandbox_config))

    loaded = provider._load_config()

    assert loaded["seccomp_unconfined"] is True


def test_provisioner_backend_requires_internal_auth_token(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._config = {
        "provisioner_url": "http://provisioner:8002",
        "mounts": [],
        "environment": {},
    }
    monkeypatch.delenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="DEER_FLOW_INTERNAL_AUTH_TOKEN"):
        provider._create_backend()


@pytest.mark.parametrize(
    ("config_key", "config_value"),
    [
        ("mounts", [{"host_path": "/host", "container_path": "/container"}]),
        ("environment", {"CUSTOM_VALUE": "configured"}),
    ],
)
def test_provisioner_backend_rejects_unsupported_container_customization(
    monkeypatch,
    config_key,
    config_value,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._config = {
        "provisioner_url": "http://provisioner:8002",
        "mounts": [],
        "environment": {},
        config_key: config_value,
    }
    monkeypatch.setenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", "control-plane-token")

    with pytest.raises(ValueError, match=config_key):
        provider._create_backend()


def test_deterministic_sandbox_ids_do_not_use_collision_prone_32_bit_prefix():
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")

    first = aio_mod.AioSandboxProvider._deterministic_sandbox_id("t141760", "user-a")
    second = aio_mod.AioSandboxProvider._deterministic_sandbox_id("t1076", "user-b")

    # These scopes share the same first 8 SHA-256 hex characters. A sandbox ID
    # must keep enough digest entropy that one scope cannot adopt the other's
    # container through an ordinary 32-bit prefix collision.
    assert first != second
    assert len(first) == len(second) == 32


def test_lifecycle_lock_registries_do_not_retain_completed_ids(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    backend = SimpleNamespace(list_running=lambda: [])
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_load_config",
        lambda _self: {"idle_timeout": 0},
    )
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_create_backend",
        lambda _self: backend,
    )
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_register_signal_handlers",
        lambda _self: None,
    )
    monkeypatch.setattr(aio_mod.atexit, "register", lambda *_args, **_kwargs: None)
    provider = aio_mod.AioSandboxProvider()

    thread_lock = provider._get_thread_lock("thread-finished", "user-finished")
    sandbox_lock = provider._get_sandbox_lock("sandbox-finished")
    thread_lock_ref = weakref.ref(thread_lock)
    sandbox_lock_ref = weakref.ref(sandbox_lock)
    del thread_lock, sandbox_lock
    gc.collect()

    assert thread_lock_ref() is None
    assert sandbox_lock_ref() is None
    assert len(provider._thread_locks) == 0
    assert len(provider._sandbox_locks) == 0


def test_register_created_sandbox_uses_scoped_api_key(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = aio_mod.threading.Lock()
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    sandbox_client = MagicMock()
    sandbox_factory = MagicMock(return_value=sandbox_client)
    monkeypatch.setattr(aio_mod, "AioSandbox", sandbox_factory)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-scoped",
        sandbox_url="http://sandbox",
        sandbox_api_key="sandbox-only-key",
    )

    provider._register_created_sandbox(
        "thread-1",
        "sandbox-scoped",
        info,
        user_id="user-1",
    )

    sandbox_factory.assert_called_once_with(
        id="sandbox-scoped",
        base_url="http://sandbox",
        api_key="sandbox-only-key",
    )


def test_get_thread_mounts_includes_acp_workspace(tmp_path, monkeypatch):
    """_get_thread_mounts must include /mnt/acp-workspace (read-only) for docker sandbox."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-3")

    container_paths = {m[1]: (m[0], m[2]) for m in mounts}

    assert "/mnt/acp-workspace" in container_paths, "ACP workspace mount is missing"
    expected_host = str(tmp_path / "threads" / "thread-3" / "acp-workspace")
    actual_host, read_only = container_paths["/mnt/acp-workspace"]
    assert actual_host == expected_host
    assert read_only is True, "ACP workspace should be read-only inside the sandbox"


def test_get_thread_mounts_includes_user_data_dirs(tmp_path, monkeypatch):
    """Baseline: user-data mounts must still be present after the ACP workspace change."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-4")
    container_paths = {m[1] for m in mounts}

    assert "/mnt/user-data/workspace" in container_paths
    assert "/mnt/user-data/uploads" in container_paths
    assert "/mnt/user-data/outputs" in container_paths


def test_get_thread_mounts_uses_explicit_user_id(tmp_path, monkeypatch):
    """Channel runs must mount the same user bucket used for artifact delivery."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: "default")

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-4", user_id="ou-user")
    container_paths = {container_path: host_path for host_path, container_path, _ in mounts}

    assert container_paths["/mnt/user-data/workspace"] == str(tmp_path / "users" / "ou-user" / "threads" / "thread-4" / "user-data" / "workspace")
    assert container_paths["/mnt/user-data/uploads"] == str(tmp_path / "users" / "ou-user" / "threads" / "thread-4" / "user-data" / "uploads")
    assert container_paths["/mnt/user-data/outputs"] == str(tmp_path / "users" / "ou-user" / "threads" / "thread-4" / "user-data" / "outputs")


def test_join_host_path_preserves_windows_drive_letter_style():
    base = r"C:\Users\demo\deer-flow\backend\.deer-flow"

    joined = join_host_path(base, "threads", "thread-9", "user-data", "outputs")

    assert joined == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-9\user-data\outputs"


def test_get_thread_mounts_preserves_windows_host_path_style(tmp_path, monkeypatch):
    """Docker bind mount sources must keep Windows-style paths intact."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setenv("DEER_FLOW_HOST_BASE_DIR", r"C:\Users\demo\deer-flow\backend\.deer-flow")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-10")

    container_paths = {container_path: host_path for host_path, container_path, _ in mounts}

    assert container_paths["/mnt/user-data/workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\workspace"
    assert container_paths["/mnt/user-data/uploads"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\uploads"
    assert container_paths["/mnt/user-data/outputs"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\outputs"
    assert container_paths["/mnt/acp-workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\acp-workspace"


def test_discover_or_create_only_unlocks_when_lock_succeeds(tmp_path, monkeypatch):
    """Unlock should not run if exclusive locking itself fails."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock = aio_mod.AioSandboxProvider._discover_or_create_with_lock.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(
        aio_mod,
        "_lock_file_exclusive",
        lambda _lock_file: (_ for _ in ()).throw(RuntimeError("lock failed")),
    )

    unlock_calls: list[object] = []
    monkeypatch.setattr(
        aio_mod,
        "_unlock_file",
        lambda lock_file: unlock_calls.append(lock_file),
    )

    with patch.object(provider, "_create_sandbox", return_value="sandbox-id"):
        with pytest.raises(RuntimeError, match="lock failed"):
            provider._discover_or_create_with_lock("thread-5", "sandbox-5")

    assert unlock_calls == []


@pytest.mark.anyio
async def test_acquire_async_uses_async_readiness_polling(monkeypatch):
    """AioSandboxProvider async creation must not use sync readiness polling."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(None)
    provider._config = {"replicas": 3}
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(
            return_value=aio_mod.SandboxInfo(
                sandbox_id="sandbox-async",
                sandbox_url="http://sandbox",
                sandbox_api_key="sandbox-async-key",
            )
        ),
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
    )

    async_readiness_calls: list[tuple[str, int]] = []

    async def fake_wait_for_sandbox_ready_async(
        sandbox_url: str,
        timeout: int = 30,
        poll_interval: float = 1.0,
        *,
        api_key: str | None = None,
    ) -> bool:
        assert api_key == "sandbox-async-key"
        async_readiness_calls.append((sandbox_url, timeout))
        return True

    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", fake_wait_for_sandbox_ready_async)
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync readiness should not be used")),
    )

    sandbox_id = await provider._create_sandbox_async("thread-async", "sandbox-async", user_id="user-async")

    assert sandbox_id == "sandbox-async"
    assert async_readiness_calls == [("http://sandbox", 60)]
    assert provider._backend.destroy.call_count == 0
    assert provider._thread_sandboxes[("user-async", "thread-async")] == "sandbox-async"


@pytest.mark.anyio
async def test_async_create_compensates_when_readiness_wait_is_cancelled(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(None)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-cancelled",
        sandbox_url="http://sandbox",
        sandbox_api_key="sandbox-key",
    )
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=info),
        destroy=MagicMock(),
    )

    async def cancelled_wait(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", cancelled_wait)

    with pytest.raises(asyncio.CancelledError):
        await provider._create_sandbox_async(
            "thread-cancelled",
            "sandbox-cancelled",
            user_id="default",
        )

    provider._backend.destroy.assert_called_once_with(info)


@pytest.mark.anyio
async def test_async_create_cancellation_waits_for_backend_then_compensates(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(None)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-create-cancelled",
        sandbox_url="http://sandbox",
        sandbox_api_key="sandbox-key",
    )
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=info),
        destroy=MagicMock(),
    )
    create_started = asyncio.Event()
    allow_create_to_finish = asyncio.Event()

    async def controlled_to_thread(func, /, *args, **kwargs):
        if func is provider._backend.create:
            create_started.set()
            await allow_create_to_finish.wait()
        return func(*args, **kwargs)

    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(aio_mod.asyncio, "to_thread", controlled_to_thread)
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled create must not reach readiness")),
    )

    task = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-create-cancelled",
            "sandbox-create-cancelled",
            user_id="default",
        )
    )
    await create_started.wait()
    task.cancel()
    allow_create_to_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    provider._backend.destroy.assert_called_once_with(info)


@pytest.mark.anyio
async def test_async_create_repeated_cancellation_still_compensates(monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(None)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-create-cancelled-twice",
        sandbox_url="http://sandbox",
        sandbox_api_key="sandbox-key",
    )
    create_started = aio_mod.threading.Event()
    allow_create_to_finish = aio_mod.threading.Event()
    create_finished = aio_mod.threading.Event()

    def blocking_create(*_args, **_kwargs):
        create_started.set()
        assert allow_create_to_finish.wait(timeout=2)
        create_finished.set()
        return info

    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=blocking_create,
        destroy=MagicMock(),
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled create must not reach readiness")),
    )

    task = asyncio.create_task(
        provider._create_sandbox_async(
            "thread-create-cancelled-twice",
            "sandbox-create-cancelled-twice",
            user_id="default",
        )
    )
    assert await asyncio.to_thread(create_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    allow_create_to_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(create_finished.wait, 1)
    await asyncio.sleep(0)

    provider._backend.destroy.assert_called_once_with(info)


@pytest.mark.parametrize(
    "failure_mode",
    ["readiness-cancelled", "missing-api-key", "not-ready"],
)
@pytest.mark.anyio
async def test_async_post_create_compensation_finishes_before_cancel_propagates(
    failure_mode,
    monkeypatch,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(None)
    info = aio_mod.SandboxInfo(
        sandbox_id=f"sandbox-{failure_mode}",
        sandbox_url="http://sandbox",
        sandbox_api_key=None if failure_mode == "missing-api-key" else "sandbox-key",
    )
    destroy_started = aio_mod.threading.Event()
    allow_destroy = aio_mod.threading.Event()
    destroy_finished = aio_mod.threading.Event()
    readiness_started = asyncio.Event()

    def blocking_destroy(_info):
        assert _info is info
        destroy_started.set()
        assert allow_destroy.wait(timeout=2)
        destroy_finished.set()

    async def readiness(*_args, **_kwargs):
        if failure_mode == "readiness-cancelled":
            readiness_started.set()
            await asyncio.Event().wait()
        return failure_mode != "not-ready"

    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=info),
        destroy=blocking_destroy,
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", readiness)

    task = asyncio.create_task(
        provider._create_sandbox_async(
            f"thread-{failure_mode}",
            info.sandbox_id,
            user_id="default",
        )
    )
    if failure_mode == "readiness-cancelled":
        await readiness_started.wait()
        task.cancel()
    assert await asyncio.to_thread(destroy_started.wait, 1)

    task.cancel()
    await asyncio.sleep(0)
    completed_before_cleanup = task.done()
    allow_destroy.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(destroy_finished.wait, 1)
    assert not completed_before_cleanup


def test_create_compensates_when_auth_probe_is_unavailable(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-probe-error",
        sandbox_url="http://sandbox",
        sandbox_api_key="sandbox-key",
    )
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(
        create=MagicMock(return_value=info),
        destroy=MagicMock(),
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        MagicMock(side_effect=RuntimeError("authentication probe unavailable")),
    )

    with pytest.raises(RuntimeError, match="authentication probe unavailable"):
        provider._create_sandbox(
            "thread-probe-error",
            "sandbox-probe-error",
            user_id="default",
        )

    provider._backend.destroy.assert_called_once_with(info)
    assert provider._sandboxes == {}
    assert provider._warm_pool == {}


@pytest.mark.anyio
async def test_discover_or_create_with_lock_async_offloads_lock_file_open_and_close(tmp_path, monkeypatch):
    """Async lock path must not open or close lock files on the event loop."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock_async = aio_mod.AioSandboxProvider._discover_or_create_with_lock_async.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {("default", "thread-async-lock"): "sandbox-async-lock"}
    provider._sandboxes = {"sandbox-async-lock": aio_mod.AioSandbox(id="sandbox-async-lock", base_url="http://sandbox")}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()
    provider._backend = SimpleNamespace(discover=MagicMock(return_value=None))

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    to_thread_calls: list[object] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fake_to_thread)

    sandbox_id = await provider._discover_or_create_with_lock_async("thread-async-lock", "sandbox-async-lock", user_id="default")

    assert sandbox_id == "sandbox-async-lock"
    assert aio_mod._open_lock_file in to_thread_calls
    assert any(getattr(func, "__name__", "") == "close" for func in to_thread_calls)


@pytest.mark.anyio
async def test_acquire_thread_lock_async_uses_dedicated_executor(monkeypatch):
    """Per-thread lock waits should not consume the default asyncio.to_thread pool."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    lock = aio_mod.threading.Lock()

    async def fail_to_thread(*_args, **_kwargs):
        raise AssertionError("thread-lock acquisition must not use asyncio.to_thread")

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fail_to_thread)

    await aio_mod._acquire_thread_lock_async(lock)
    try:
        assert not lock.acquire(blocking=False)
    finally:
        lock.release()


@pytest.mark.anyio
async def test_acquire_async_cancellation_does_not_leak_thread_lock(tmp_path):
    """Cancelled async lock waiters must not leave the per-thread lock held."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()

    thread_id = "thread-cancel-lock"
    thread_lock = provider._get_thread_lock(thread_id, "default")
    thread_lock.acquire()

    task = asyncio.create_task(provider.acquire_async(thread_id, user_id="default"))
    await asyncio.sleep(0.05)
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    thread_lock.release()
    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        acquired = thread_lock.acquire(blocking=False)
        if acquired:
            thread_lock.release()
            return
        await asyncio.sleep(0.01)

    pytest.fail("provider thread lock was leaked after cancelling acquire_async")


@pytest.mark.anyio
async def test_acquire_async_cancelled_waiter_does_not_block_successor(tmp_path, monkeypatch):
    """A cancelled waiter must not prevent the next live waiter from acquiring."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._thread_locks = {}
    provider._warm_pool = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._lock = aio_mod.threading.Lock()

    async def fake_acquire_internal_async(thread_id: str | None, *, user_id: str) -> str:
        assert thread_id == "thread-successor-lock"
        assert user_id == "default"
        await asyncio.sleep(0)
        return "sandbox-successor"

    monkeypatch.setattr(provider, "_acquire_internal_async", fake_acquire_internal_async)

    thread_id = "thread-successor-lock"
    thread_lock = provider._get_thread_lock(thread_id, "default")
    thread_lock.acquire()

    cancelled_waiter = asyncio.create_task(provider.acquire_async(thread_id, user_id="default"))
    await asyncio.sleep(0.05)
    cancelled_waiter.cancel()
    try:
        await cancelled_waiter
    except asyncio.CancelledError:
        pass

    live_waiter = asyncio.create_task(provider.acquire_async(thread_id, user_id="default"))
    thread_lock.release()

    assert await asyncio.wait_for(live_waiter, timeout=1) == "sandbox-successor"

    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        acquired = thread_lock.acquire(blocking=False)
        if acquired:
            thread_lock.release()
            return
        await asyncio.sleep(0.01)

    pytest.fail("provider thread lock was not released after successor acquire_async")


@pytest.mark.anyio
async def test_acquire_internal_async_offloads_cached_reuse_health_check(tmp_path, monkeypatch):
    """Async cached reuse must keep backend health checks off the event loop."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider, _sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-cached-async")
    provider._thread_sandboxes = {("default", "thread-cached-async"): "sandbox-cached-async"}
    provider._backend.is_alive = MagicMock(return_value=True)

    to_thread_calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append((func, args))
        return func(*args, **kwargs)

    monkeypatch.setattr(aio_mod.asyncio, "to_thread", fake_to_thread)

    sandbox_id = await provider._acquire_internal_async("thread-cached-async", user_id="default")

    assert sandbox_id == "sandbox-cached-async"
    assert to_thread_calls == [(provider._reuse_in_process_sandbox, ("thread-cached-async",))]


def test_remote_backend_create_forwards_effective_user_id(monkeypatch):
    """Provisioner mode must receive user_id so PVC subPath matches user isolation."""
    remote_mod = importlib.import_module("deerflow.community.aio_sandbox.remote_backend")
    monkeypatch.setenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", "control-plane-token")
    backend = remote_mod.RemoteSandboxBackend("http://provisioner:8002")
    token = set_current_user(SimpleNamespace(id="user-7"))
    posted: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sandbox_url": "http://sandbox.local"}

    def _post(url, json, timeout, **_kwargs):  # noqa: A002 - mirrors requests.post kwarg
        posted.update({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(remote_mod.requests, "post", _post)

    try:
        backend.create("thread-42", "sandbox-42")
    finally:
        reset_current_user(token)

    assert posted["url"] == "http://provisioner:8002/api/sandboxes"
    assert posted["json"] == {
        "sandbox_id": "sandbox-42",
        "thread_id": "thread-42",
        "user_id": "user-7",
    }


def test_remote_backend_create_prefers_explicit_user_id(monkeypatch):
    """Provisioner mode must not fall back to the ambient default for channel runs."""
    remote_mod = importlib.import_module("deerflow.community.aio_sandbox.remote_backend")
    monkeypatch.setenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", "control-plane-token")
    backend = remote_mod.RemoteSandboxBackend("http://provisioner:8002")
    posted: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sandbox_url": "http://sandbox.local"}

    def _post(url, json, timeout, **_kwargs):  # noqa: A002 - mirrors requests.post kwarg
        posted.update({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(remote_mod.requests, "post", _post)
    monkeypatch.setattr(remote_mod, "get_effective_user_id", lambda: "default")

    backend.create("thread-42", "sandbox-42", user_id="ou-user")

    assert posted["json"]["user_id"] == "ou-user"


# ── Sandbox client teardown (#2872) ──────────────────────────────────────────


def _make_provider_with_active_sandbox(tmp_path, sandbox_id: str):
    """Build a provider with one active sandbox suitable for release/destroy/shutdown tests."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._warm_pool = {}
    provider._sandbox_infos = {
        sandbox_id: aio_mod.SandboxInfo(sandbox_id=sandbox_id, sandbox_url="http://sandbox-host"),
    }
    provider._thread_sandboxes = {}
    provider._last_activity = {sandbox_id: 0.0}
    provider._shutdown_called = False
    provider._idle_checker_thread = None
    provider._backend = SimpleNamespace(destroy=MagicMock())

    sandbox = MagicMock()
    sandbox.id = sandbox_id
    sandbox.close = MagicMock()
    provider._sandboxes = {sandbox_id: sandbox}
    return provider, sandbox, aio_mod


def test_release_closes_cached_sandbox_client(tmp_path):
    """release() must close the host-side client owned by the cached AioSandbox (#2872)."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-rel")

    provider.release("sandbox-rel")

    sandbox.close.assert_called_once_with()
    # And the sandbox is parked in the warm pool (container still running).
    assert "sandbox-rel" in provider._warm_pool
    assert "sandbox-rel" not in provider._sandboxes


def test_destroy_closes_cached_sandbox_client(tmp_path):
    """destroy() must close the host-side client before backend container teardown (#2872)."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-destroy")
    backend_destroy = provider._backend.destroy

    provider.destroy("sandbox-destroy")

    sandbox.close.assert_called_once_with()
    backend_destroy.assert_called_once()
    assert "sandbox-destroy" not in provider._sandboxes
    assert "sandbox-destroy" not in provider._sandbox_infos


def test_destroy_failure_restores_sandbox_for_retry(tmp_path):
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-retry")
    info = provider._sandbox_infos["sandbox-retry"]
    provider._backend.destroy.side_effect = RuntimeError("provisioner unavailable")

    with pytest.raises(RuntimeError, match="provisioner unavailable"):
        provider.destroy("sandbox-retry")

    sandbox.close.assert_called_once_with()
    assert "sandbox-retry" in provider._warm_pool
    assert provider._warm_pool["sandbox-retry"][0] is info

    provider._backend.destroy.side_effect = None
    provider.destroy("sandbox-retry")
    assert provider._backend.destroy.call_count == 2
    assert provider._backend.destroy.call_args.args == (info,)
    assert "sandbox-retry" not in provider._warm_pool


def test_warm_pool_eviction_failure_restores_original_entry(tmp_path):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-evict-retry",
        sandbox_url="http://sandbox",
        sandbox_api_key="sandbox-key",
    )
    provider._lock = aio_mod.threading.Lock()
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._warm_pool = {"sandbox-evict-retry": (info, 123.0)}
    provider._backend = SimpleNamespace(
        destroy=MagicMock(side_effect=RuntimeError("provisioner unavailable")),
    )

    assert provider._evict_oldest_warm() is None
    assert provider._warm_pool["sandbox-evict-retry"] == (info, 123.0)

    provider._backend.destroy.side_effect = None
    assert provider._evict_oldest_warm() == "sandbox-evict-retry"
    assert "sandbox-evict-retry" not in provider._warm_pool


def test_shutdown_closes_all_active_sandbox_clients(tmp_path):
    """shutdown() must close every cached AioSandbox client during teardown (#2872)."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-shut")

    provider.shutdown()

    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()
    assert provider._sandboxes == {}


def test_release_swallows_close_errors(tmp_path, caplog):
    """A failure inside sandbox.close() must not break provider release()."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-rel-err")
    sandbox.close.side_effect = RuntimeError("boom")

    with caplog.at_level("WARNING"):
        provider.release("sandbox-rel-err")

    assert "Error closing sandbox sandbox-rel-err during release" in caplog.text
    # Still moved to warm pool: client teardown failure must not block lifecycle.
    assert "sandbox-rel-err" in provider._warm_pool


def test_get_uses_in_memory_registry_only(tmp_path):
    """get() must stay event-loop safe by avoiding backend health checks."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-dead")
    provider._backend.is_alive = MagicMock(side_effect=AssertionError("get must not call backend health checks"))

    assert provider.get("sandbox-dead") is sandbox


def test_acquire_drops_dead_cached_sandbox(tmp_path, monkeypatch):
    """acquire() must replace a stale active cache entry after its container dies."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-dead")
    provider._thread_locks = {}
    provider._thread_sandboxes = {("default", "thread-dead"): "sandbox-dead"}
    provider._config = {"replicas": 3}
    provider._backend.is_alive = MagicMock(return_value=False)
    provider._backend.discover = MagicMock(return_value=None)
    provider._backend.create = MagicMock(
        return_value=aio_mod.SandboxInfo(
            sandbox_id="sandbox-dead",
            sandbox_url="http://fresh-sandbox",
            container_name="deer-flow-sandbox-sandbox-dead",
            sandbox_api_key="fresh-sandbox-key",
        )
    )

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_sandbox_id_for_thread", lambda _self, _thread_id, _user_id: "sandbox-dead")
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_get_extra_mounts", lambda _self, _thread_id, *, user_id=None: [])
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        lambda _url, timeout=60, **_kwargs: True,
    )

    sandbox_id = provider.acquire("thread-dead", user_id="default")

    assert sandbox_id == "sandbox-dead"
    sandbox.close.assert_called_once_with()
    provider._backend.destroy.assert_called_once()
    provider._backend.create.assert_called_once()
    assert provider._thread_sandboxes[("default", "thread-dead")] == "sandbox-dead"
    assert provider._sandboxes["sandbox-dead"].base_url == "http://fresh-sandbox"


def test_acquire_keeps_cached_sandbox_when_health_check_errors(tmp_path):
    """Transient backend health-check errors must not destroy a tracked sandbox."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-transient")
    provider._thread_locks = {}
    provider._thread_sandboxes = {("default", "thread-transient"): "sandbox-transient"}
    provider._backend.is_alive = MagicMock(side_effect=OSError("docker daemon busy"))

    sandbox_id = provider.acquire("thread-transient", user_id="default")

    assert sandbox_id == "sandbox-transient"
    sandbox.close.assert_not_called()
    provider._backend.destroy.assert_not_called()
    assert provider._sandboxes["sandbox-transient"] is sandbox


def test_acquire_waits_for_running_but_not_ready_cached_sandbox(tmp_path, monkeypatch):
    provider, sandbox, aio_mod = _make_provider_with_active_sandbox(tmp_path, "sandbox-starting")
    provider._thread_locks = {}
    provider._thread_sandboxes = {("default", "thread-starting"): "sandbox-starting"}
    info = provider._sandbox_infos["sandbox-starting"]
    info.status = "Running"
    info.ready = False
    provider._backend.is_alive = MagicMock(return_value=True)
    readiness = MagicMock(return_value=True)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", readiness)

    sandbox_id = provider.acquire("thread-starting", user_id="default")

    assert sandbox_id == "sandbox-starting"
    readiness.assert_called_once_with(
        "http://sandbox-host",
        timeout=60,
        api_key=info.sandbox_api_key,
    )
    assert info.ready is True
    sandbox.close.assert_not_called()


def test_reclaim_waits_for_reconciled_sandbox_readiness(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-reconciled",
        sandbox_url="http://sandbox-host",
        sandbox_api_key="sandbox-key",
        status="Pending",
        ready=False,
    )
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {"sandbox-reconciled": (info, 0.0)}
    provider._backend = SimpleNamespace(
        is_alive=MagicMock(return_value=True),
        destroy=MagicMock(),
    )
    readiness = MagicMock(return_value=True)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", readiness)

    sandbox_id = provider._reclaim_warm_pool_sandbox(
        "thread-reconciled",
        "sandbox-reconciled",
        user_id="default",
    )

    assert sandbox_id == "sandbox-reconciled"
    readiness.assert_called_once_with(
        "http://sandbox-host",
        timeout=60,
        api_key="sandbox-key",
    )
    assert info.ready is True


def test_reclaim_rechecks_auth_even_when_pod_reports_ready(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-unverified",
        sandbox_url="http://sandbox-host",
        sandbox_api_key="sandbox-key",
        status="Running",
        ready=True,
    )
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {"sandbox-unverified": (info, 0.0)}
    provider._backend = SimpleNamespace(
        is_alive=MagicMock(return_value=True),
        destroy=MagicMock(),
    )
    readiness = MagicMock(return_value=False)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", readiness)

    assert (
        provider._reclaim_warm_pool_sandbox(
            "thread-unverified",
            "sandbox-unverified",
            user_id="default",
        )
        is None
    )
    readiness.assert_called_once_with(
        "http://sandbox-host",
        timeout=60,
        api_key="sandbox-key",
    )
    provider._backend.destroy.assert_called_once_with(info)


def test_reclaim_keeps_warm_entry_when_auth_probe_is_unavailable(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    info = aio_mod.SandboxInfo(
        sandbox_id="sandbox-probe-unknown",
        sandbox_url="http://sandbox-host",
        sandbox_api_key="sandbox-key",
        status="Running",
        ready=True,
    )
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {"sandbox-probe-unknown": (info, 0.0)}
    provider._backend = SimpleNamespace(
        is_alive=MagicMock(return_value=True),
        destroy=MagicMock(),
    )
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        MagicMock(side_effect=RuntimeError("authentication probe unavailable")),
    )

    with pytest.raises(RuntimeError, match="authentication probe unavailable"):
        provider._reclaim_warm_pool_sandbox(
            "thread-probe-unknown",
            "sandbox-probe-unknown",
            user_id="default",
        )

    assert provider._warm_pool["sandbox-probe-unknown"][0] is info
    provider._backend.destroy.assert_not_called()


def test_drop_unhealthy_sandbox_skips_recreated_entry(tmp_path):
    """A stale health-check result must not delete a newly registered sandbox."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._warm_pool = {}
    provider._last_activity = {"sandbox-toctou": 1.0}
    provider._thread_sandboxes = {("default", "thread-toctou"): "sandbox-toctou"}
    old_info = aio_mod.SandboxInfo(sandbox_id="sandbox-toctou", sandbox_url="http://old-sandbox")
    new_info = aio_mod.SandboxInfo(sandbox_id="sandbox-toctou", sandbox_url="http://new-sandbox")
    new_sandbox = MagicMock()
    provider._sandbox_infos = {"sandbox-toctou": new_info}
    provider._sandboxes = {"sandbox-toctou": new_sandbox}
    provider._backend = SimpleNamespace(destroy=MagicMock())

    provider._drop_unhealthy_sandbox("sandbox-toctou", "stale health check", expected_info=old_info)

    new_sandbox.close.assert_not_called()
    provider._backend.destroy.assert_not_called()
    assert provider._sandbox_infos["sandbox-toctou"] is new_info
    assert provider._sandboxes["sandbox-toctou"] is new_sandbox
    assert provider._thread_sandboxes == {("default", "thread-toctou"): "sandbox-toctou"}


def test_acquire_skips_dead_warm_pool_sandbox(tmp_path, monkeypatch):
    """acquire() must create a fresh sandbox when the warm-pool entry died."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._lock = aio_mod.threading.Lock()
    provider._thread_locks = {}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._last_activity = {}
    provider._warm_pool = {
        "sandbox-warm-dead": (
            aio_mod.SandboxInfo(
                sandbox_id="sandbox-warm-dead",
                sandbox_url="http://stale-sandbox",
                container_name="deer-flow-sandbox-sandbox-warm-dead",
            ),
            0.0,
        )
    }
    provider._config = {"replicas": 3}
    provider._backend = SimpleNamespace(
        is_alive=MagicMock(return_value=False),
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
        create=MagicMock(
            return_value=aio_mod.SandboxInfo(
                sandbox_id="sandbox-warm-dead",
                sandbox_url="http://fresh-sandbox",
                container_name="deer-flow-sandbox-sandbox-warm-dead",
                sandbox_api_key="fresh-sandbox-key",
            )
        ),
    )

    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_sandbox_id_for_thread", lambda _self, _thread_id, _user_id: "sandbox-warm-dead")
    monkeypatch.setattr(aio_mod.AioSandboxProvider, "_get_extra_mounts", lambda _self, _thread_id, *, user_id=None: [])
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        lambda _url, timeout=60, **_kwargs: True,
    )

    sandbox_id = provider.acquire("thread-warm-dead", user_id="default")

    assert sandbox_id == "sandbox-warm-dead"
    provider._backend.destroy.assert_called_once()
    provider._backend.create.assert_called_once()
    assert provider._warm_pool == {}
    assert provider._thread_sandboxes[("default", "thread-warm-dead")] == "sandbox-warm-dead"
    assert provider._sandboxes["sandbox-warm-dead"].base_url == "http://fresh-sandbox"


def test_destroy_swallows_close_errors_and_still_destroys_backend(tmp_path, caplog):
    """A failure in sandbox.close() must not skip backend container destruction."""
    provider, sandbox, _ = _make_provider_with_active_sandbox(tmp_path, "sandbox-dest-err")
    sandbox.close.side_effect = RuntimeError("boom")

    with caplog.at_level("WARNING"):
        provider.destroy("sandbox-dest-err")

    assert "Error closing sandbox sandbox-dest-err during destroy" in caplog.text
    provider._backend.destroy.assert_called_once()


def test_idle_warm_destroy_serializes_reacquire(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    sandbox_id = "sandbox-idle-race"
    old_info = aio_mod.SandboxInfo(
        sandbox_id=sandbox_id,
        sandbox_url="http://old-sandbox",
        sandbox_api_key="old-key",
        ready=True,
    )
    fresh_info = aio_mod.SandboxInfo(
        sandbox_id=sandbox_id,
        sandbox_url="http://fresh-sandbox",
        sandbox_api_key="fresh-key",
    )
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._warm_pool = {sandbox_id: (old_info, 0.0)}
    provider._lock = aio_mod.threading.Lock()

    destroy_started = aio_mod.threading.Event()
    allow_destroy = aio_mod.threading.Event()
    destroy_finished = aio_mod.threading.Event()
    acquire_finished = aio_mod.threading.Event()
    result: list[str] = []
    errors: list[BaseException] = []

    def destroy(_info):
        destroy_started.set()
        assert allow_destroy.wait(timeout=2)
        destroy_finished.set()

    def discover(_sandbox_id):
        return None if destroy_finished.is_set() else old_info

    provider._backend = SimpleNamespace(
        destroy=destroy,
        discover=discover,
        create=MagicMock(return_value=fresh_info),
        is_alive=MagicMock(return_value=True),
    )
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_sandbox_id_for_thread",
        lambda _self, _thread_id, _user_id: sandbox_id,
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda *_args, **_kwargs: True)

    cleanup_thread = aio_mod.threading.Thread(
        target=provider._cleanup_idle_sandboxes,
        args=(1,),
    )
    cleanup_thread.start()
    assert destroy_started.wait(timeout=1)

    def acquire():
        try:
            result.append(provider.acquire("thread-idle-race", user_id="default"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            acquire_finished.set()

    acquire_thread = aio_mod.threading.Thread(target=acquire)
    acquire_thread.start()
    try:
        assert not acquire_finished.wait(timeout=0.1)
    finally:
        allow_destroy.set()
        cleanup_thread.join(timeout=2)
        acquire_thread.join(timeout=2)

    assert errors == []
    assert result == [sandbox_id]
    provider._backend.create.assert_called_once()
    assert provider._sandboxes[sandbox_id].base_url == "http://fresh-sandbox"


def test_blocked_destroy_for_one_thread_does_not_block_another_thread(tmp_path, monkeypatch):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    sandbox_a = "sandbox-thread-a"
    sandbox_b = "sandbox-thread-b"
    info_a = aio_mod.SandboxInfo(
        sandbox_id=sandbox_a,
        sandbox_url="http://sandbox-a",
        sandbox_api_key="key-a",
        ready=True,
    )
    info_b = aio_mod.SandboxInfo(
        sandbox_id=sandbox_b,
        sandbox_url="http://sandbox-b",
        sandbox_api_key="key-b",
    )
    provider._config = {"replicas": 3}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._warm_pool = {sandbox_a: (info_a, 0.0)}
    provider._lock = aio_mod.threading.Lock()

    destroy_started = aio_mod.threading.Event()
    allow_destroy = aio_mod.threading.Event()
    acquire_b_finished = aio_mod.threading.Event()
    result: list[str] = []

    def destroy(_info):
        destroy_started.set()
        assert allow_destroy.wait(timeout=2)

    provider._backend = SimpleNamespace(
        destroy=destroy,
        discover=MagicMock(return_value=None),
        create=MagicMock(return_value=info_b),
        is_alive=MagicMock(return_value=True),
    )
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_sandbox_id_for_thread",
        lambda _self, thread_id, _user_id: sandbox_a if thread_id == "thread-a" else sandbox_b,
    )
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda *_args, **_kwargs: True)

    cleanup_thread = aio_mod.threading.Thread(
        target=provider._cleanup_idle_sandboxes,
        args=(1,),
    )
    cleanup_thread.start()
    assert destroy_started.wait(timeout=1)

    def acquire_b():
        result.append(provider.acquire("thread-b", user_id="default"))
        acquire_b_finished.set()

    acquire_thread = aio_mod.threading.Thread(target=acquire_b)
    acquire_thread.start()
    try:
        assert acquire_b_finished.wait(timeout=1)
    finally:
        allow_destroy.set()
        cleanup_thread.join(timeout=2)
        acquire_thread.join(timeout=2)

    assert result == [sandbox_b]
    assert provider._thread_sandboxes[("default", "thread-b")] == sandbox_b
    assert provider._sandboxes[sandbox_b].base_url == "http://sandbox-b"
