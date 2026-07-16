"""Unit tests for checkpointer config, packaging metadata, and factories."""

import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

import deerflow.config.app_config as app_config_module
from deerflow.config.checkpointer_config import (
    CheckpointerConfig,
    ensure_config_loaded,
    get_checkpointer_config,
    load_checkpointer_config_from_dict,
    set_checkpointer_config,
)
from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.checkpointer import get_checkpointer, reset_checkpointer
from deerflow.runtime.checkpointer.provider import POSTGRES_INSTALL
from deerflow.runtime.store import get_store, reset_store
from deerflow.runtime.store.provider import POSTGRES_STORE_INSTALL


@pytest.fixture(autouse=True)
def reset_state():
    """Reset singleton state before each test."""
    app_config_module._app_config = None
    set_checkpointer_config(None)
    reset_checkpointer()
    reset_store()
    yield
    app_config_module._app_config = None
    set_checkpointer_config(None)
    reset_checkpointer()
    reset_store()


class _BlockingSingletonContext:
    def __init__(self, value: object, entered: Event, release: Event, stats: dict[str, object]):
        self._value = value
        self._entered = entered
        self._release = release
        self._stats = stats

    def __enter__(self):
        with self._stats["lock"]:
            self._stats["enters"] += 1
            self._entered.set()
        assert self._release.wait(timeout=3), "timed out waiting to release singleton initialization"
        return self._value

    def __exit__(self, exc_type, exc, tb):
        with self._stats["lock"]:
            self._stats["exits"] += 1
        return False


class _BlockingSingletonFactory:
    def __init__(self):
        self.value = object()
        self.entered = Event()
        self.release = Event()
        self.stats = {"enters": 0, "exits": 0, "lock": Lock()}

    def context_manager(self, _config):
        return _BlockingSingletonContext(self.value, self.entered, self.release, self.stats)

    def enter_count(self) -> int:
        with self.stats["lock"]:
            return self.stats["enters"]

    def exit_count(self) -> int:
        with self.stats["lock"]:
            return self.stats["exits"]


class _TrackingLock:
    def __init__(self):
        self._lock = Lock()
        self.acquired = Event()

    def acquire(self, *args, **kwargs):
        acquired = self._lock.acquire(*args, **kwargs)
        if acquired:
            self.acquired.set()
        return acquired

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def locked(self) -> bool:
        return self._lock.locked()


def _call_getter_concurrently(getter, workers: int = 8) -> list[object]:
    ready = Barrier(workers + 1)

    def worker():
        ready.wait(timeout=3)
        return getter()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker) for _ in range(workers)]
        ready.wait(timeout=3)
        return [future.result(timeout=3) for future in futures]


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestCheckpointerConfig:
    def test_load_memory_config(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        config = get_checkpointer_config()
        assert config is not None
        assert config.type == "memory"
        assert config.connection_string is None

    def test_load_sqlite_config(self):
        load_checkpointer_config_from_dict({"type": "sqlite", "connection_string": "/tmp/test.db"})
        config = get_checkpointer_config()
        assert config is not None
        assert config.type == "sqlite"
        assert config.connection_string == "/tmp/test.db"

    def test_load_postgres_config(self):
        load_checkpointer_config_from_dict({"type": "postgres", "connection_string": "postgresql://localhost/db"})
        config = get_checkpointer_config()
        assert config is not None
        assert config.type == "postgres"
        assert config.connection_string == "postgresql://localhost/db"

    def test_default_connection_string_is_none(self):
        config = CheckpointerConfig(type="memory")
        assert config.connection_string is None

    def test_set_config_to_none(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        set_checkpointer_config(None)
        assert get_checkpointer_config() is None

    def test_ensure_config_loaded_loads_app_config_when_uninitialized(self):
        def fake_get_app_config():
            load_checkpointer_config_from_dict({"type": "memory"})

        with patch("deerflow.config.app_config.get_app_config", side_effect=fake_get_app_config) as mock_get_app_config:
            ensure_config_loaded()

        mock_get_app_config.assert_called_once()
        config = get_checkpointer_config()
        assert config is not None
        assert config.type == "memory"

    def test_ensure_config_loaded_skips_explicit_config(self):
        load_checkpointer_config_from_dict({"type": "memory"})

        with patch("deerflow.config.app_config.get_app_config") as mock_get_app_config:
            ensure_config_loaded()

        mock_get_app_config.assert_not_called()

    def test_invalid_type_raises(self):
        with pytest.raises(Exception):
            load_checkpointer_config_from_dict({"type": "unknown"})

    def test_connection_string_description_matches_runtime_defaults(self):
        description = CheckpointerConfig.model_fields["connection_string"].description

        assert description is not None
        assert "Optional for sqlite" in description
        assert "defaults to 'store.db'" in description
        assert "Required for postgres" in description


class TestHarnessPackaging:
    def test_pyproject_declares_postgres_extra(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "packages" / "harness" / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text())

        optional_dependencies = data["project"]["optional-dependencies"]
        assert "postgres" in optional_dependencies
        assert optional_dependencies["postgres"] == [
            "asyncpg>=0.29",
            "langgraph-checkpoint-postgres>=3.0.5",
            "psycopg[binary]>=3.3.3",
            "psycopg-pool>=3.3.0",
        ]

    def test_workspace_pyproject_forwards_postgres_extra_to_harness(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text())

        optional_dependencies = data["project"]["optional-dependencies"]
        assert optional_dependencies["postgres"] == ["deerflow-harness[postgres]"]

    def test_postgres_missing_dependency_messages_recommend_package_extra(self):
        assert "deerflow-harness[postgres]" in POSTGRES_INSTALL
        assert "deerflow-harness[postgres]" in POSTGRES_STORE_INSTALL
        assert "uv sync --all-packages --extra postgres" in POSTGRES_INSTALL
        assert "uv sync --all-packages --extra postgres" in POSTGRES_STORE_INSTALL


# ---------------------------------------------------------------------------
# Namespace contract tests
# ---------------------------------------------------------------------------


def _put_marker_checkpoint(checkpointer: InMemorySaver, *, thread_id: str, checkpoint_ns: str, marker: str) -> dict:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"marker": marker}
    checkpoint["channel_versions"] = {"marker": 1}
    return checkpointer.put(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}},
        checkpoint,
        {"source": "test", "step": 0, "writes": {}},
        {"marker": 1},
    )


def test_in_memory_checkpointer_keeps_root_and_non_root_namespaces_isolated():
    """Current contract: checkpoints are keyed by thread_id + checkpoint_ns + checkpoint_id."""
    checkpointer = InMemorySaver()

    root_config = _put_marker_checkpoint(checkpointer, thread_id="thread-1", checkpoint_ns="", marker="root")
    branch_config = _put_marker_checkpoint(checkpointer, thread_id="thread-1", checkpoint_ns="branch-a", marker="branch")

    root_latest = checkpointer.get_tuple({"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}})
    branch_latest = checkpointer.get_tuple({"configurable": {"thread_id": "thread-1", "checkpoint_ns": "branch-a"}})

    assert root_latest is not None
    assert branch_latest is not None
    assert root_latest.config == root_config
    assert branch_latest.config == branch_config
    assert root_latest.checkpoint["channel_values"]["marker"] == "root"
    assert branch_latest.checkpoint["channel_values"]["marker"] == "branch"

    assert checkpointer.get_tuple(branch_config).checkpoint["channel_values"]["marker"] == "branch"
    assert checkpointer.get_tuple(root_config).checkpoint["channel_values"]["marker"] == "root"
    assert (
        checkpointer.get_tuple(
            {
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_ns": "branch-a",
                    "checkpoint_id": root_config["configurable"]["checkpoint_id"],
                }
            }
        )
        is None
    )
    assert (
        checkpointer.get_tuple(
            {
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_ns": "",
                    "checkpoint_id": branch_config["configurable"]["checkpoint_id"],
                }
            }
        )
        is None
    )


def test_in_memory_checkpointer_does_not_key_by_run_id():
    """Current contract: run_id belongs to runs/events, not checkpoint identity."""
    checkpointer = InMemorySaver()

    root_config = _put_marker_checkpoint(checkpointer, thread_id="thread-1", checkpoint_ns="", marker="root")
    with_run_id = {
        "configurable": {
            **root_config["configurable"],
            "run_id": "unrelated-run",
        }
    }

    checkpoint = checkpointer.get_tuple(with_run_id)

    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["marker"] == "root"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestGetCheckpointer:
    def test_returns_in_memory_saver_when_not_configured(self):
        """get_checkpointer should return InMemorySaver when not configured."""
        from langgraph.checkpoint.memory import InMemorySaver

        with patch("deerflow.config.app_config.get_app_config", side_effect=FileNotFoundError):
            cp = get_checkpointer()
        assert cp is not None
        assert isinstance(cp, InMemorySaver)

    def test_memory_returns_in_memory_saver(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        from langgraph.checkpoint.memory import InMemorySaver

        cp = get_checkpointer()
        assert isinstance(cp, InMemorySaver)

    def test_memory_singleton(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        cp1 = get_checkpointer()
        cp2 = get_checkpointer()
        assert cp1 is cp2

    def test_reset_clears_singleton(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        cp1 = get_checkpointer()
        reset_checkpointer()
        cp2 = get_checkpointer()
        assert cp1 is not cp2

    def test_sqlite_raises_when_package_missing(self):
        load_checkpointer_config_from_dict({"type": "sqlite", "connection_string": "/tmp/test.db"})
        with patch.dict(sys.modules, {"langgraph.checkpoint.sqlite": None}):
            reset_checkpointer()
            with pytest.raises(ImportError, match="langgraph-checkpoint-sqlite"):
                get_checkpointer()

    def test_postgres_raises_when_package_missing(self):
        load_checkpointer_config_from_dict({"type": "postgres", "connection_string": "postgresql://localhost/db"})
        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": None}):
            reset_checkpointer()
            with pytest.raises(ImportError, match="langgraph-checkpoint-postgres"):
                get_checkpointer()

    def test_postgres_raises_when_connection_string_missing(self):
        load_checkpointer_config_from_dict({"type": "postgres"})
        mock_saver = MagicMock()
        mock_module = MagicMock()
        mock_module.PostgresSaver = mock_saver
        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": mock_module}):
            reset_checkpointer()
            with pytest.raises(ValueError, match="connection_string is required"):
                get_checkpointer()

    def test_sqlite_creates_saver(self):
        """SQLite checkpointer is created when package is available."""
        load_checkpointer_config_from_dict({"type": "sqlite", "connection_string": "/tmp/test.db"})

        mock_saver_instance = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_saver_instance)
        mock_cm.__exit__ = MagicMock(return_value=False)

        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string = MagicMock(return_value=mock_cm)

        mock_module = MagicMock()
        mock_module.SqliteSaver = mock_saver_cls

        with patch.dict(sys.modules, {"langgraph.checkpoint.sqlite": mock_module}):
            reset_checkpointer()
            cp = get_checkpointer()

        assert cp is mock_saver_instance
        mock_saver_cls.from_conn_string.assert_called_once()
        mock_saver_instance.setup.assert_called_once()

    def test_sqlite_creates_parent_dir(self):
        """Sync SQLite checkpointer should call ensure_sqlite_parent_dir before connecting.

        This mirrors the async checkpointer's behaviour and prevents
        'sqlite3.OperationalError: unable to open database file' when the
        parent directory for the database file does not yet exist (e.g. when
        using the harness package from an external virtualenv where the
        .deer-flow directory has not been created).
        """
        load_checkpointer_config_from_dict({"type": "sqlite", "connection_string": "relative/test.db"})

        mock_saver_instance = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_saver_instance)
        mock_cm.__exit__ = MagicMock(return_value=False)

        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string = MagicMock(return_value=mock_cm)

        mock_module = MagicMock()
        mock_module.SqliteSaver = mock_saver_cls

        with (
            patch.dict(sys.modules, {"langgraph.checkpoint.sqlite": mock_module}),
            patch("deerflow.runtime.checkpointer.provider.ensure_sqlite_parent_dir") as mock_ensure,
            patch(
                "deerflow.runtime.checkpointer.provider.resolve_sqlite_conn_str",
                return_value="/tmp/resolved/relative/test.db",
            ),
        ):
            reset_checkpointer()
            cp = get_checkpointer()

        assert cp is mock_saver_instance
        mock_ensure.assert_called_once_with("/tmp/resolved/relative/test.db")
        mock_saver_cls.from_conn_string.assert_called_once_with("/tmp/resolved/relative/test.db")

    def test_sqlite_ensure_parent_dir_before_connect(self):
        """ensure_sqlite_parent_dir must be called before from_conn_string."""
        load_checkpointer_config_from_dict({"type": "sqlite", "connection_string": "relative/test.db"})

        call_order = []

        mock_saver_instance = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_saver_instance)
        mock_cm.__exit__ = MagicMock(return_value=False)

        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string = MagicMock(side_effect=lambda *a, **kw: (call_order.append("connect"), mock_cm)[1])

        mock_module = MagicMock()
        mock_module.SqliteSaver = mock_saver_cls

        def record_ensure(*a, **kw):
            call_order.append("ensure")

        with (
            patch.dict(sys.modules, {"langgraph.checkpoint.sqlite": mock_module}),
            patch(
                "deerflow.runtime.checkpointer.provider.ensure_sqlite_parent_dir",
                side_effect=record_ensure,
            ),
            patch(
                "deerflow.runtime.checkpointer.provider.resolve_sqlite_conn_str",
                return_value="/tmp/resolved/relative/test.db",
            ),
        ):
            reset_checkpointer()
            get_checkpointer()

        assert call_order == ["ensure", "connect"]

    def test_postgres_creates_saver(self):
        """Postgres checkpointer is created when packages are available."""
        load_checkpointer_config_from_dict({"type": "postgres", "connection_string": "postgresql://localhost/db"})

        mock_saver_instance = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_saver_instance)
        mock_cm.__exit__ = MagicMock(return_value=False)

        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string = MagicMock(return_value=mock_cm)

        mock_pg_module = MagicMock()
        mock_pg_module.PostgresSaver = mock_saver_cls

        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": mock_pg_module}):
            reset_checkpointer()
            cp = get_checkpointer()

        assert cp is mock_saver_instance
        mock_saver_cls.from_conn_string.assert_called_once_with("postgresql://localhost/db")
        mock_saver_instance.setup.assert_called_once()


class TestSyncSingletonThreadSafety:
    def test_store_uses_unified_database_when_legacy_checkpointer_is_absent(self):
        database = MagicMock(backend="sqlite")
        app_config = MagicMock(checkpointer=None, database=database)
        store = MagicMock()
        store_context = MagicMock()
        store_context.__enter__ = MagicMock(return_value=store)
        store_context.__exit__ = MagicMock(return_value=False)

        with (
            patch("deerflow.runtime.store.provider.ensure_config_loaded"),
            patch("deerflow.runtime.store.provider.get_app_config", return_value=app_config),
            patch("deerflow.runtime.store.provider._sync_store_from_database", return_value=store_context) as make_store,
        ):
            resolved = get_store()

        assert resolved is store
        make_store.assert_called_once_with(database)

    def test_store_reset_clears_singleton(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        store1 = get_store()
        reset_store()
        store2 = get_store()
        assert store1 is not store2

    def test_concurrent_checkpointer_getter_creates_one_instance(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        factory = _BlockingSingletonFactory()

        with patch("deerflow.runtime.checkpointer.provider._sync_checkpointer_cm", side_effect=factory.context_manager):
            futures_started = ThreadPoolExecutor(max_workers=1)
            try:
                result_future = futures_started.submit(_call_getter_concurrently, get_checkpointer)
                assert factory.entered.wait(timeout=3)
                factory.release.wait(timeout=0.05)
                factory.release.set()
                results = result_future.result(timeout=3)
            finally:
                futures_started.shutdown(wait=True)

        assert all(result is factory.value for result in results)
        assert factory.enter_count() == 1

    def test_concurrent_store_getter_creates_one_instance(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        factory = _BlockingSingletonFactory()

        with patch("deerflow.runtime.store.provider._sync_store_cm", side_effect=factory.context_manager):
            futures_started = ThreadPoolExecutor(max_workers=1)
            try:
                result_future = futures_started.submit(_call_getter_concurrently, get_store)
                assert factory.entered.wait(timeout=3)
                factory.release.wait(timeout=0.05)
                factory.release.set()
                results = result_future.result(timeout=3)
            finally:
                futures_started.shutdown(wait=True)

        assert all(result is factory.value for result in results)
        assert factory.enter_count() == 1

    def test_checkpointer_loads_config_outside_singleton_lock(self):
        tracking_lock = _TrackingLock()

        def fake_ensure_config_loaded():
            assert not tracking_lock.locked()
            load_checkpointer_config_from_dict({"type": "memory"})

        with (
            patch("deerflow.runtime.checkpointer.provider._checkpointer_lock", tracking_lock),
            patch("deerflow.runtime.checkpointer.provider.ensure_config_loaded", side_effect=fake_ensure_config_loaded),
        ):
            checkpointer = get_checkpointer()

        assert checkpointer is not None
        assert tracking_lock.acquired.is_set()

    def test_store_loads_config_outside_singleton_lock(self):
        tracking_lock = _TrackingLock()

        def fake_ensure_config_loaded():
            assert not tracking_lock.locked()
            load_checkpointer_config_from_dict({"type": "memory"})

        with (
            patch("deerflow.runtime.store.provider._store_lock", tracking_lock),
            patch("deerflow.runtime.store.provider.ensure_config_loaded", side_effect=fake_ensure_config_loaded),
        ):
            store = get_store()

        assert store is not None
        assert tracking_lock.acquired.is_set()

    def test_checkpointer_reset_waits_for_initialization(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        factory = _BlockingSingletonFactory()

        with (
            patch("deerflow.runtime.checkpointer.provider._sync_checkpointer_cm", side_effect=factory.context_manager),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            get_future = executor.submit(get_checkpointer)
            assert factory.entered.wait(timeout=3)

            reset_started = Event()

            def reset_worker():
                reset_started.set()
                reset_checkpointer()

            reset_future = executor.submit(reset_worker)
            assert reset_started.wait(timeout=3)
            factory.release.wait(timeout=0.05)

            assert not reset_future.done()
            assert factory.exit_count() == 0

            factory.release.set()
            assert get_future.result(timeout=3) is factory.value
            reset_future.result(timeout=3)

        assert factory.exit_count() == 1

    def test_store_reset_waits_for_initialization(self):
        load_checkpointer_config_from_dict({"type": "memory"})
        factory = _BlockingSingletonFactory()

        with (
            patch("deerflow.runtime.store.provider._sync_store_cm", side_effect=factory.context_manager),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            get_future = executor.submit(get_store)
            assert factory.entered.wait(timeout=3)

            reset_started = Event()

            def reset_worker():
                reset_started.set()
                reset_store()

            reset_future = executor.submit(reset_worker)
            assert reset_started.wait(timeout=3)
            factory.release.wait(timeout=0.05)

            assert not reset_future.done()
            assert factory.exit_count() == 0

            factory.release.set()
            assert get_future.result(timeout=3) is factory.value
            reset_future.result(timeout=3)

        assert factory.exit_count() == 1


class TestAsyncCheckpointer:
    @pytest.mark.anyio
    async def test_async_store_persists_with_unified_database_when_legacy_checkpointer_is_absent(self, tmp_path):
        from deerflow.runtime.store.async_provider import make_store

        database = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path))
        app_config = MagicMock(checkpointer=None, database=database)

        async with make_store(app_config) as store:
            await store.aput(("threads",), "thread-1", {"status": "completed"})

        async with make_store(app_config) as store:
            item = await store.aget(("threads",), "thread-1")

        assert item is not None
        assert item.value == {"status": "completed"}

    @pytest.mark.anyio
    async def test_sqlite_creates_parent_dir_via_to_thread(self):
        """Async SQLite setup should move mkdir off the event loop."""
        from deerflow.runtime.checkpointer.async_provider import _prepare_sqlite_checkpointer_path, make_checkpointer

        mock_config = MagicMock()
        mock_config.checkpointer = CheckpointerConfig(type="sqlite", connection_string="relative/test.db")

        mock_saver = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_saver
        mock_cm.__aexit__.return_value = False

        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string.return_value = mock_cm

        mock_module = MagicMock()
        mock_module.AsyncSqliteSaver = mock_saver_cls

        with (
            patch("deerflow.runtime.checkpointer.async_provider.get_app_config", return_value=mock_config),
            patch.dict(sys.modules, {"langgraph.checkpoint.sqlite.aio": mock_module}),
            patch(
                "deerflow.runtime.checkpointer.async_provider.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value="/tmp/resolved/test.db",
            ) as mock_to_thread,
        ):
            async with make_checkpointer() as saver:
                assert saver is mock_saver

        mock_to_thread.assert_awaited_once()
        called_fn, called_path = mock_to_thread.await_args.args
        assert called_fn is _prepare_sqlite_checkpointer_path
        assert called_path == "relative/test.db"
        mock_saver_cls.from_conn_string.assert_called_once_with("/tmp/resolved/test.db")
        mock_saver.setup.assert_awaited_once()

    @pytest.mark.anyio
    async def test_postgres_uses_connection_pool(self):
        """Async postgres checkpointer should use AsyncConnectionPool, not a single connection."""
        from deerflow.runtime.checkpointer.async_provider import make_checkpointer

        mock_config = MagicMock()
        mock_config.checkpointer = CheckpointerConfig(type="postgres", connection_string="postgresql://localhost/db")

        mock_saver = AsyncMock()

        mock_saver_cls = MagicMock(return_value=mock_saver)

        mock_pool_instance = AsyncMock()
        mock_pool_instance.__aenter__.return_value = mock_pool_instance
        mock_pool_instance.__aexit__.return_value = False

        mock_pool_cls = MagicMock(return_value=mock_pool_instance)
        mock_pool_cls.check_connection = AsyncMock()
        mock_dict_row = MagicMock()

        mock_pg_module = MagicMock()
        mock_pg_module.AsyncPostgresSaver = mock_saver_cls

        mock_psycopg_rows = MagicMock()
        mock_psycopg_rows.dict_row = mock_dict_row

        with (
            patch("deerflow.runtime.checkpointer.async_provider.get_app_config", return_value=mock_config),
            patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": mock_pg_module}),
            patch.dict(sys.modules, {"psycopg.rows": mock_psycopg_rows}),
            patch.dict(sys.modules, {"psycopg_pool": MagicMock(AsyncConnectionPool=mock_pool_cls)}),
        ):
            # AsyncConnectionPool() is a callable that returns mock_pool_instance
            # We need the constructor to be an async context manager
            async with make_checkpointer() as saver:
                assert saver is mock_saver

        # Verify the pool was constructed with check Connection
        mock_pool_cls.assert_called_once()
        call_kwargs = mock_pool_cls.call_args
        assert call_kwargs[0][0] == "postgresql://localhost/db"
        assert call_kwargs[1]["check"] is mock_pool_cls.check_connection

        # Verify saver was constructed with the pool (not via from_conn_string)
        mock_saver_cls.assert_called_once_with(conn=mock_pool_instance)
        mock_saver.setup.assert_awaited_once()

    @pytest.mark.anyio
    async def test_database_postgres_uses_connection_pool(self):
        """Unified database postgres path should use AsyncConnectionPool with keepalive."""
        from deerflow.config.database_config import DatabaseConfig
        from deerflow.runtime.checkpointer.async_provider import make_checkpointer

        db_config = DatabaseConfig(backend="postgres", postgres_url="postgresql://localhost/db")
        mock_config = MagicMock()
        mock_config.checkpointer = None
        mock_config.database = db_config

        mock_saver = AsyncMock()

        mock_saver_cls = MagicMock(return_value=mock_saver)

        mock_pool_instance = AsyncMock()
        mock_pool_instance.__aenter__.return_value = mock_pool_instance
        mock_pool_instance.__aexit__.return_value = False

        mock_pool_cls = MagicMock(return_value=mock_pool_instance)
        mock_pool_cls.check_connection = AsyncMock()
        mock_dict_row = MagicMock()

        mock_pg_module = MagicMock()
        mock_pg_module.AsyncPostgresSaver = mock_saver_cls

        mock_psycopg_rows = MagicMock()
        mock_psycopg_rows.dict_row = mock_dict_row

        with (
            patch("deerflow.runtime.checkpointer.async_provider.get_app_config", return_value=mock_config),
            patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": mock_pg_module}),
            patch.dict(sys.modules, {"psycopg.rows": mock_psycopg_rows}),
            patch.dict(sys.modules, {"psycopg_pool": MagicMock(AsyncConnectionPool=mock_pool_cls)}),
        ):
            async with make_checkpointer() as saver:
                assert saver is mock_saver

        mock_pool_cls.assert_called_once()
        call_kwargs = mock_pool_cls.call_args
        assert call_kwargs[0][0] == "postgresql://localhost/db"
        assert call_kwargs[1]["check"] is mock_pool_cls.check_connection

        mock_saver_cls.assert_called_once_with(conn=mock_pool_instance)
        mock_saver.setup.assert_awaited_once()

    @pytest.mark.anyio
    async def test_database_sqlite_creates_parent_dir_via_to_thread(self):
        """Unified database SQLite setup should also move path IO off the event loop."""
        from deerflow.config.database_config import DatabaseConfig
        from deerflow.runtime.checkpointer.async_provider import _prepare_database_sqlite_checkpointer_path, make_checkpointer

        db_config = DatabaseConfig(backend="sqlite", sqlite_dir="relative-data")
        mock_config = MagicMock()
        mock_config.checkpointer = None
        mock_config.database = db_config

        mock_saver = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_saver
        mock_cm.__aexit__.return_value = False

        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string.return_value = mock_cm

        mock_module = MagicMock()
        mock_module.AsyncSqliteSaver = mock_saver_cls

        with (
            patch("deerflow.runtime.checkpointer.async_provider.get_app_config", return_value=mock_config),
            patch.dict(sys.modules, {"langgraph.checkpoint.sqlite.aio": mock_module}),
            patch(
                "deerflow.runtime.checkpointer.async_provider.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value="/tmp/data/deerflow.db",
            ) as mock_to_thread,
        ):
            async with make_checkpointer() as saver:
                assert saver is mock_saver

        mock_to_thread.assert_awaited_once()
        called_fn, called_db_config = mock_to_thread.await_args.args
        assert called_fn is _prepare_database_sqlite_checkpointer_path
        assert called_db_config is db_config
        mock_saver_cls.from_conn_string.assert_called_once_with("/tmp/data/deerflow.db")
        mock_saver.setup.assert_awaited_once()


# ---------------------------------------------------------------------------
# app_config.py integration
# ---------------------------------------------------------------------------


class TestAppConfigLoadsCheckpointer:
    def test_load_checkpointer_section(self):
        """load_checkpointer_config_from_dict populates the global config."""
        set_checkpointer_config(None)
        load_checkpointer_config_from_dict({"type": "memory"})
        cfg = get_checkpointer_config()
        assert cfg is not None
        assert cfg.type == "memory"


# ---------------------------------------------------------------------------
# DeerFlowClient falls back to config checkpointer
# ---------------------------------------------------------------------------


class TestClientCheckpointerFallback:
    def test_client_uses_config_checkpointer_when_none_provided(self):
        """DeerFlowClient._ensure_agent falls back to get_checkpointer() when checkpointer=None."""
        from langgraph.checkpoint.memory import InMemorySaver

        from deerflow.client import DeerFlowClient

        load_checkpointer_config_from_dict({"type": "memory"})

        captured_kwargs = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        model_mock = MagicMock()
        config_mock = MagicMock()
        config_mock.models = [model_mock]
        config_mock.get_model_config.return_value = MagicMock(supports_vision=False)
        config_mock.checkpointer = None

        with (
            patch("deerflow.client.get_app_config", return_value=config_mock),
            patch("deerflow.client.create_agent", side_effect=fake_create_agent),
            patch("deerflow.client.create_chat_model", return_value=MagicMock()),
            patch("deerflow.client.build_middlewares", return_value=[]),
            patch("deerflow.client.apply_prompt_template", return_value=""),
            patch("deerflow.client.DeerFlowClient._get_tools", return_value=[]),
        ):
            client = DeerFlowClient(checkpointer=None)
            config = client._get_runnable_config("test-thread")
            client._ensure_agent(config)

        assert "checkpointer" in captured_kwargs
        assert isinstance(captured_kwargs["checkpointer"], InMemorySaver)

    def test_client_explicit_checkpointer_takes_precedence(self):
        """An explicitly provided checkpointer is used even when config checkpointer is set."""
        from deerflow.client import DeerFlowClient

        load_checkpointer_config_from_dict({"type": "memory"})

        explicit_cp = MagicMock()
        captured_kwargs = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        model_mock = MagicMock()
        config_mock = MagicMock()
        config_mock.models = [model_mock]
        config_mock.get_model_config.return_value = MagicMock(supports_vision=False)
        config_mock.checkpointer = None

        with (
            patch("deerflow.client.get_app_config", return_value=config_mock),
            patch("deerflow.client.create_agent", side_effect=fake_create_agent),
            patch("deerflow.client.create_chat_model", return_value=MagicMock()),
            patch("deerflow.client.build_middlewares", return_value=[]),
            patch("deerflow.client.apply_prompt_template", return_value=""),
            patch("deerflow.client.DeerFlowClient._get_tools", return_value=[]),
        ):
            client = DeerFlowClient(checkpointer=explicit_cp)
            config = client._get_runnable_config("test-thread")
            client._ensure_agent(config)

        assert captured_kwargs["checkpointer"] is explicit_cp
