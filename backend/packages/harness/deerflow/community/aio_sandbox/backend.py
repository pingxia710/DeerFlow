"""Abstract base class for sandbox provisioning backends."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

import httpx
import requests

from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)
_AUTH_REJECTION_STATUS_CODES = frozenset({401, 403})


class SandboxAuthProbeError(RuntimeError):
    """The sandbox auth probe could not determine whether auth is enforced."""


def _invalid_api_key(api_key: str) -> str:
    return f"{api_key}-invalid"


def _sandbox_auth_is_enforced(sandbox_url: str, api_key: str) -> bool:
    probes = (
        None,
        {"X-AIO-API-Key": _invalid_api_key(api_key)},
    )
    for headers in probes:
        try:
            response = requests.get(
                f"{sandbox_url}/v1/sandbox",
                timeout=5,
                headers=headers,
            )
        except requests.exceptions.RequestException as exc:
            raise SandboxAuthProbeError(f"Sandbox authentication probe failed for {sandbox_url}: {exc}") from exc
        if response.status_code not in _AUTH_REJECTION_STATUS_CODES:
            logger.error(
                "Sandbox %s did not reject an unauthorized readiness probe (status=%s)",
                sandbox_url,
                response.status_code,
            )
            return False
    return True


def wait_for_sandbox_ready(sandbox_url: str, timeout: int = 30, *, api_key: str | None = None) -> bool:
    """Poll sandbox health endpoint until ready or timeout.

    Args:
        sandbox_url: URL of the sandbox (e.g. http://k3s:30001).
        timeout: Maximum time to wait in seconds.

    Returns:
        True if sandbox is ready, False otherwise.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            headers = {"X-AIO-API-Key": api_key} if api_key else None
            response = requests.get(f"{sandbox_url}/v1/sandbox", timeout=5, headers=headers)
            if response.status_code == 200:
                return api_key is None or _sandbox_auth_is_enforced(sandbox_url, api_key)
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False


async def wait_for_sandbox_ready_async(
    sandbox_url: str,
    timeout: int = 30,
    poll_interval: float = 1.0,
    *,
    api_key: str | None = None,
) -> bool:
    """Async variant of sandbox readiness polling.

    Use this from async runtime paths so sandbox startup waits do not block the
    event loop. The synchronous ``wait_for_sandbox_ready`` function remains for
    existing synchronous backend/provider call sites.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def auth_is_enforced(client: httpx.AsyncClient) -> bool:
        probes = (
            None,
            {"X-AIO-API-Key": _invalid_api_key(api_key or "")},
        )
        for headers in probes:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                response = await client.get(
                    f"{sandbox_url}/v1/sandbox",
                    timeout=min(5.0, remaining),
                    headers=headers,
                )
            except httpx.RequestError as exc:
                raise SandboxAuthProbeError(f"Sandbox authentication probe failed for {sandbox_url}: {exc}") from exc
            if response.status_code not in _AUTH_REJECTION_STATUS_CODES:
                logger.error(
                    "Sandbox %s did not reject an unauthorized readiness probe (status=%s)",
                    sandbox_url,
                    response.status_code,
                )
                return False
        return True

    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                headers = {"X-AIO-API-Key": api_key} if api_key else None
                response = await client.get(
                    f"{sandbox_url}/v1/sandbox",
                    timeout=min(5.0, remaining),
                    headers=headers,
                )
                if response.status_code == 200:
                    return api_key is None or await auth_is_enforced(client)
            except httpx.RequestError:
                pass
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))
    return False


class SandboxBackend(ABC):
    """Abstract base for sandbox provisioning backends.

    Two implementations:
    - LocalContainerBackend: starts Docker/Apple Container locally, manages ports
    - RemoteSandboxBackend: connects to a pre-existing URL (K8s service, external)
    """

    @abstractmethod
    def create(
        self,
        thread_id: str | None,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
        *,
        user_id: str | None = None,
    ) -> SandboxInfo:
        """Create/provision a new sandbox.

        Args:
            thread_id: Thread ID for which the sandbox is being created. Useful for backends that want to organize sandboxes by thread.
            sandbox_id: Deterministic sandbox identifier.
            extra_mounts: Additional volume mounts as (host_path, container_path, read_only) tuples.
                Ignored by backends that don't manage containers (e.g., remote).
            user_id: User bucket that the sandbox should mount or provision for.

        Returns:
            SandboxInfo with connection details.
        """
        ...

    @abstractmethod
    def destroy(self, info: SandboxInfo) -> None:
        """Destroy/cleanup a sandbox and release its resources.

        Args:
            info: The sandbox metadata to destroy.
        """
        ...

    @abstractmethod
    def is_alive(self, info: SandboxInfo) -> bool:
        """Quick check whether a sandbox is still alive.

        This should be a lightweight check (e.g., container inspect)
        rather than a full health check.

        Args:
            info: The sandbox metadata to check.

        Returns:
            True if the sandbox appears to be alive.
        """
        ...

    @abstractmethod
    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """Try to discover an existing sandbox by its deterministic ID.

        Used for cross-process recovery: when another process started a sandbox,
        this process can discover it by the deterministic container name or URL.

        Args:
            sandbox_id: The deterministic sandbox ID to look for.

        Returns:
            SandboxInfo if found and healthy, None otherwise.
        """
        ...

    def list_running(self) -> list[SandboxInfo]:
        """Enumerate all running sandboxes managed by this backend.

        Used for startup reconciliation: when the process restarts, it needs
        to discover containers started by previous processes so they can be
        adopted into the warm pool or destroyed if idle too long.

        The default implementation returns an empty list, which is correct
        for backends that don't manage local containers (e.g., RemoteSandboxBackend
        delegates lifecycle to the provisioner which handles its own cleanup).

        Returns:
            A list of SandboxInfo for all currently running sandboxes.
        """
        return []
