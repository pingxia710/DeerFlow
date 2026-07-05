import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.auth_disabled import warn_if_auth_disabled_enabled
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.config import get_gateway_config
from app.gateway.csrf_middleware import CSRFMiddleware, get_configured_cors_origins
from app.gateway.deps import langgraph_runtime
from app.gateway.routers import (
    agents,
    artifacts,
    assistants_compat,
    auth,
    capabilities,
    channel_connections,
    channels,
    feedback,
    mcp,
    memory,
    models,
    runs,
    skills,
    suggestions,
    thread_runs,
    threads,
    uploads,
)
from deerflow.config import app_config as deerflow_app_config
from deerflow.config.app_config import apply_logging_level

AppConfig = deerflow_app_config.AppConfig
get_app_config = deerflow_app_config.get_app_config

# Default logging; lifespan overrides from config.yaml log_level.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-ID"
_METRICS_STARTED_AT = time.time()
_REQUEST_COUNTS: dict[tuple[str, str, str], int] = defaultdict(int)
_REQUEST_DURATION_SECONDS: dict[tuple[str, str, str], float] = defaultdict(float)

# Upper bound (seconds) each lifespan shutdown hook is allowed to run.
# Bounds worker exit time so uvicorn's reload supervisor does not keep
# firing signals into a worker that is stuck waiting for shutdown cleanup.
_SHUTDOWN_HOOK_TIMEOUT_SECONDS = 5.0
_ENV_KEYS = ("DEER_FLOW_ENV", "ENVIRONMENT", "APP_ENV", "NODE_ENV")
_SHARED_ENV_VALUES = {"prod", "production", "staging", "stage", "shared"}
_DEV_ENV_VALUES = {"dev", "development", "local", "test", "testing"}


def _current_environment_values() -> set[str]:
    return {value.strip().lower() for key in _ENV_KEYS if (value := os.getenv(key)) and value.strip()}


def _is_development_environment() -> bool:
    env_values = _current_environment_values()
    return bool(env_values) and not env_values & _SHARED_ENV_VALUES and env_values <= _DEV_ENV_VALUES


def _is_shared_environment() -> bool:
    return bool(_current_environment_values() & _SHARED_ENV_VALUES)


def _assert_single_gateway_worker() -> None:
    """Fail fast outside development when in-process runtime would be split."""
    worker_settings = []
    for key in ("GATEWAY_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = os.getenv(key)
        if not raw:
            continue
        try:
            worker_settings.append((key, int(raw)))
        except ValueError:
            logger.warning("Ignoring non-integer gateway worker setting %s=%r", key, raw)

    if not worker_settings:
        return
    if all(workers <= 1 for _, workers in worker_settings):
        return

    if not _is_development_environment():
        raise RuntimeError("DeerFlow gateway runtime is process-local; production/non-development deployments must run exactly one gateway worker until shared runtime is enabled.")


def _assert_safe_sandbox_config_for_environment(config: AppConfig) -> None:
    if not _is_shared_environment():
        return

    sandbox = config.sandbox
    unsafe = [
        name
        for name in (
            "allow_host_bash",
            "unrestricted_host_access",
            "allow_dangerous_host_mounts",
            "seccomp_unconfined",
        )
        if bool(getattr(sandbox, name, False))
    ]
    if unsafe:
        raise RuntimeError(f"Unsafe sandbox configuration is forbidden in staging/shared/production: sandbox.{', sandbox.'.join(unsafe)}")


def _assert_run_event_store_config_for_environment(config: AppConfig) -> None:
    if not _is_shared_environment():
        return

    run_events_backend = getattr(getattr(config, "run_events", None), "backend", "memory")
    if run_events_backend != "db":
        raise RuntimeError("run_events.backend='db' is required in staging/shared/production; memory/jsonl run-event stores are single-process only.")

    database_backend = getattr(getattr(config, "database", None), "backend", "memory")
    if database_backend == "memory":
        raise RuntimeError("database.backend must be sqlite or postgres when run_events.backend='db' in staging/shared/production.")


def _metric_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _route_template(request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)


def _record_request_metric(request, *, status_code: int, duration_seconds: float) -> None:
    key = (request.method, _route_template(request), f"{status_code // 100}xx")
    _REQUEST_COUNTS[key] += 1
    _REQUEST_DURATION_SECONDS[key] += duration_seconds


def _render_metrics() -> str:
    lines = [
        "# HELP deerflow_gateway_uptime_seconds Gateway process uptime in seconds.",
        "# TYPE deerflow_gateway_uptime_seconds gauge",
        f"deerflow_gateway_uptime_seconds {time.time() - _METRICS_STARTED_AT:.6f}",
        "# HELP deerflow_gateway_http_requests_total HTTP requests handled by Gateway.",
        "# TYPE deerflow_gateway_http_requests_total counter",
    ]
    for (method, route, status_class), count in sorted(_REQUEST_COUNTS.items()):
        labels = f'method="{_metric_label(method)}",route="{_metric_label(route)}",status_class="{_metric_label(status_class)}"'
        lines.append(f"deerflow_gateway_http_requests_total{{{labels}}} {count}")
    lines.extend(
        [
            "# HELP deerflow_gateway_http_request_duration_seconds_sum Total Gateway HTTP request duration in seconds.",
            "# TYPE deerflow_gateway_http_request_duration_seconds_sum counter",
        ]
    )
    for (method, route, status_class), total in sorted(_REQUEST_DURATION_SECONDS.items()):
        labels = f'method="{_metric_label(method)}",route="{_metric_label(route)}",status_class="{_metric_label(status_class)}"'
        lines.append(f"deerflow_gateway_http_request_duration_seconds_sum{{{labels}}} {total:.6f}")
    return "\n".join(lines) + "\n"


async def _ensure_admin_user(app: FastAPI) -> None:
    """Startup hook: handle first boot and migrate orphan threads otherwise.

    After admin creation, migrate orphan threads from the LangGraph
    store (metadata.user_id unset) to the admin account. This is the
    "no-auth → with-auth" upgrade path: users who ran DeerFlow without
    authentication have existing LangGraph thread data that needs an
    owner assigned.
        First boot (no admin exists):
            - Does NOT create any user accounts automatically.
            - The operator must visit ``/setup`` to create the first admin.

    Subsequent boots (admin already exists):
      - Runs the one-time "no-auth → with-auth" orphan thread migration for
        existing LangGraph thread metadata that has no user_id.

    No SQL persistence migration is needed: the four user_id columns
    (threads_meta, runs, run_events, feedback) only come into existence
    alongside the auth module via create_all, so freshly created tables
    never contain NULL-owner rows.
    """
    from sqlalchemy import select

    from app.gateway.deps import get_local_provider
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.user.model import UserRow

    try:
        provider = get_local_provider()
    except RuntimeError:
        # Auth persistence may not be initialized in some test/boot paths.
        # Skip admin migration work rather than failing gateway startup.
        logger.warning("Auth persistence not ready; skipping admin bootstrap check")
        return

    sf = get_session_factory()
    if sf is None:
        return

    admin_count = await provider.count_admin_users()

    if admin_count == 0:
        logger.info("=" * 60)
        logger.info("  First boot detected — no admin account exists.")
        logger.info("  Visit /setup to complete admin account creation.")
        logger.info("=" * 60)
        return

    # Admin already exists — run orphan thread migration for any
    # LangGraph thread metadata that pre-dates the auth module.
    async with sf() as session:
        stmt = select(UserRow).where(UserRow.system_role == "admin").limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None:
        return  # Should not happen (admin_count > 0 above), but be safe.

    admin_id = str(row.id)

    # LangGraph store orphan migration — non-fatal.
    # This covers the "no-auth → with-auth" upgrade path for users
    # whose existing LangGraph thread metadata has no user_id set.
    store = getattr(app.state, "store", None)
    if store is not None:
        try:
            migrated = await _migrate_orphaned_threads(store, admin_id)
            if migrated:
                logger.info("Migrated %d orphan LangGraph thread(s) to admin", migrated)
        except Exception:
            logger.exception("LangGraph thread migration failed (non-fatal)")


async def _iter_store_items(store, namespace, *, page_size: int = 500):
    """Paginated async iterator over a LangGraph store namespace.

    Replaces the old hardcoded ``limit=1000`` call with a cursor-style
    loop so that environments with more than one page of orphans do
    not silently lose data. Terminates when a page is empty OR when a
    short page arrives (indicating the last page).
    """
    offset = 0
    while True:
        batch = await store.asearch(namespace, limit=page_size, offset=offset)
        if not batch:
            return
        for item in batch:
            yield item
        if len(batch) < page_size:
            return
        offset += page_size


async def _migrate_orphaned_threads(store, admin_user_id: str) -> int:
    """Migrate LangGraph store threads with no user_id to the given admin.

    Uses cursor pagination so all orphans are migrated regardless of
    count. Returns the number of rows migrated.
    """
    migrated = 0
    async for item in _iter_store_items(store, ("threads",)):
        metadata = item.value.get("metadata", {})
        if not metadata.get("user_id"):
            metadata["user_id"] = admin_user_id
            item.value["metadata"] = metadata
            await store.aput(("threads",), item.key, item.value)
            migrated += 1
    return migrated


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""

    _assert_single_gateway_worker()

    # Load config and check necessary environment variables at startup.
    # `startup_config` is a local snapshot used only for one-shot bootstrap
    # work (logging level, langgraph_runtime engines, channels). Request-time
    # config resolution always routes through `get_app_config()` in
    # `app/gateway/deps.py::get_config()` so `config.yaml` edits become
    # visible without a process restart. We deliberately do NOT cache this
    # snapshot on `app.state` to keep that contract enforceable.
    try:
        startup_config = get_app_config()
        apply_logging_level(startup_config.log_level)
        _assert_safe_sandbox_config_for_environment(startup_config)
        _assert_run_event_store_config_for_environment(startup_config)
        logger.info("Configuration loaded successfully")
        warn_if_auth_disabled_enabled()
    except Exception as e:
        error_msg = f"Failed to load configuration during gateway startup: {e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    config = get_gateway_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # Pre-warm tiktoken encoding cache so the first memory-injection request
    # never blocks on the BPE data download (which hits an OpenAI/Azure URL
    # that may be unreachable in restricted networks — see issue #3402).
    # When memory.token_counting is "char", token counting never touches
    # tiktoken, so skip the warm-up entirely (avoids even the 5s probe in
    # network-restricted deployments — see issue #3429).
    if startup_config.memory.token_counting == "char":
        logger.info("memory.token_counting='char'; skipping tiktoken warm-up (network-free token estimation)")
    else:
        try:
            from deerflow.agents.memory.prompt import warm_tiktoken_cache

            warmed = await asyncio.wait_for(
                asyncio.to_thread(warm_tiktoken_cache),
                timeout=5,
            )
            if warmed:
                logger.info("tiktoken encoding cache warmed successfully")
            else:
                logger.warning("tiktoken encoding cache warm-up failed; token counting will use character-based fallback until tiktoken loads successfully")
        except TimeoutError:
            logger.warning("tiktoken encoding cache warm-up timed out; token counting will use character-based fallback until tiktoken loads successfully")
        except Exception:
            logger.warning("tiktoken warm-up skipped", exc_info=True)

    # Initialize LangGraph runtime components (StreamBridge, RunManager, checkpointer, store)
    async with langgraph_runtime(app, startup_config):
        logger.info("LangGraph runtime initialised")

        # Check admin bootstrap state and migrate orphan threads after admin exists.
        # Must run AFTER langgraph_runtime so app.state.store is available for thread migration
        await _ensure_admin_user(app)

        # Start IM channel service if any channels are configured
        try:
            from app.channels.service import start_channel_service

            channel_service = await start_channel_service(startup_config)
            logger.info("Channel service started: %s", channel_service.get_status())
        except Exception:
            logger.exception("No IM channels configured or channel service failed to start")

        yield

        try:
            await auth.close_oidc_service()
        except Exception:
            logger.exception("Failed to close OIDC service")

        # Stop channel service on shutdown (bounded to prevent worker hang)
        try:
            from app.channels.service import stop_channel_service

            await asyncio.wait_for(
                stop_channel_service(),
                timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Channel service shutdown exceeded %.1fs; proceeding with worker exit.",
                _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to stop channel service")

    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    config = get_gateway_config()
    docs_url = "/docs" if config.enable_docs else None
    redoc_url = "/redoc" if config.enable_docs else None
    openapi_url = "/openapi.json" if config.enable_docs else None

    app = FastAPI(
        title="DeerFlow API Gateway",
        description="""
## DeerFlow API Gateway

API Gateway for DeerFlow - A LangGraph-based AI agent backend with sandbox execution capabilities.

### Features

- **Models Management**: Query and retrieve available AI models
- **MCP Configuration**: Manage Model Context Protocol (MCP) server configurations
- **Memory Management**: Access and manage global memory data for personalized conversations
- **Skills Management**: Query and manage skills and their enabled status
- **Artifacts**: Access thread artifacts and generated files
- **Health Monitoring**: System health check endpoints

### Architecture

LangGraph-compatible requests are routed through nginx to this gateway.
This gateway provides runtime endpoints for agent runs plus custom endpoints for models, MCP configuration, skills, and artifacts.
        """,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        openapi_tags=[
            {
                "name": "models",
                "description": "Operations for querying available AI models and their configurations",
            },
            {
                "name": "mcp",
                "description": "Manage Model Context Protocol (MCP) server configurations",
            },
            {
                "name": "memory",
                "description": "Access and manage global memory data for personalized conversations",
            },
            {
                "name": "skills",
                "description": "Manage skills and their configurations",
            },
            {
                "name": "artifacts",
                "description": "Access and download thread artifacts and generated files",
            },
            {
                "name": "uploads",
                "description": "Upload and manage user files for threads",
            },
            {
                "name": "threads",
                "description": "Manage DeerFlow thread-local filesystem data",
            },
            {
                "name": "agents",
                "description": "Create and manage custom agents with per-agent config and prompts",
            },
            {
                "name": "suggestions",
                "description": "Generate follow-up question suggestions for conversations",
            },
            {
                "name": "channels",
                "description": "Manage IM channel integrations (Feishu, Slack, Telegram)",
            },
            {
                "name": "assistants-compat",
                "description": "LangGraph Platform-compatible assistants API (stub)",
            },
            {
                "name": "runs",
                "description": "LangGraph Platform-compatible runs lifecycle (create, stream, cancel)",
            },
            {
                "name": "health",
                "description": "Health check and system status endpoints",
            },
        ],
    )

    @app.middleware("http")
    async def request_id_middleware(request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = request_id
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_seconds = time.perf_counter() - started
            _record_request_metric(request, status_code=status_code, duration_seconds=duration_seconds)
            logger.info(
                json.dumps(
                    {
                        "event": "gateway.request",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "duration_ms": round(duration_seconds * 1000, 2),
                    },
                    separators=(",", ":"),
                )
            )

    # Auth: reject unauthenticated requests to non-public paths (fail-closed safety net)
    app.add_middleware(AuthMiddleware)

    # CSRF: Double Submit Cookie pattern for state-changing requests
    app.add_middleware(CSRFMiddleware)

    # CORS: the unified nginx endpoint is same-origin by default. Split-origin
    # browser clients must opt in with this explicit Gateway allowlist so CORS
    # and CSRF origin checks share the same source of truth.
    cors_origins = sorted(get_configured_cors_origins())
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include routers
    # Models API is mounted at /api/models
    app.include_router(models.router)

    # MCP API is mounted at /api/mcp
    app.include_router(mcp.router)

    # Memory API is mounted at /api/memory
    app.include_router(memory.router)

    # Skills API is mounted at /api/skills
    app.include_router(skills.router)

    # Capability Snapshot API is mounted at /api/capabilities
    app.include_router(capabilities.router)

    # Artifacts API is mounted at /api/threads/{thread_id}/artifacts
    app.include_router(artifacts.router)

    # Uploads API is mounted at /api/threads/{thread_id}/uploads
    app.include_router(uploads.router)

    # Thread cleanup API is mounted at /api/threads/{thread_id}
    app.include_router(threads.router)

    # Agents API is mounted at /api/agents
    app.include_router(agents.router)

    # Suggestions API is mounted at /api/threads/{thread_id}/suggestions
    app.include_router(suggestions.router)

    # User-facing IM channel connection API is mounted at /api/channels
    app.include_router(channel_connections.router)

    # Channels API is mounted at /api/channels
    app.include_router(channels.router)

    # Assistants compatibility API (LangGraph Platform stub)
    app.include_router(assistants_compat.router)

    # Auth API is mounted at /api/v1/auth
    app.include_router(auth.router)

    # Feedback API is mounted at /api/threads/{thread_id}/runs/{run_id}/feedback
    app.include_router(feedback.router)

    # Thread Runs API (LangGraph Platform-compatible runs lifecycle)
    app.include_router(thread_runs.router)

    # Stateless Runs API (stream/wait without a pre-existing thread)
    app.include_router(runs.router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint.

        Returns:
            Service health status information.
        """
        return {"status": "healthy", "service": "deer-flow-gateway"}

    @app.get("/metrics", tags=["health"])
    async def metrics() -> Response:
        return Response(_render_metrics(), media_type="text/plain; version=0.0.4")

    return app


# Create app instance for uvicorn
app = create_app()
