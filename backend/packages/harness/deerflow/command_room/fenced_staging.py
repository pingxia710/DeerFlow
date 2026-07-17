"""Fail-closed R0-E2 interfaces for FENCED_STAGING.

These interfaces are intentionally not wired into the current task launcher.
They neither create namespaces nor claim that a real OS fence exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from deerflow.persistence.artifact_reservation import (
    ArtifactReservationRepository,
    FencedArtifactKey,
    PublishIntent,
    ReservationHandle,
    SnapshotMetadata,
)


class FencedExecutorUnavailable(RuntimeError):
    """No production-equivalent isolated executor has been evidenced."""


class SecureDirfdCapability(Protocol):
    def require_secure_dirfd(self) -> None: ...


class ExecutorCapability(Protocol):
    def require_real_fence(self) -> None: ...


class SecureDirfdGate:
    """Reject platforms without the dirfd operations FENCED data paths require."""

    def require_secure_dirfd(self) -> None:
        required = (
            hasattr(os, "O_NOFOLLOW"),
            hasattr(os, "O_DIRECTORY"),
            os.open in os.supports_dir_fd,
            os.stat in os.supports_dir_fd,
            os.rename in os.supports_dir_fd,
        )
        if not all(required):
            raise FencedExecutorUnavailable("fenced_executor_unavailable")

        # This proves only that the Python process exposes dirfd primitives;
        # it is not evidence of child isolation or durable publish semantics.
        fd = os.open(os.curdir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise FencedExecutorUnavailable("fenced_executor_unavailable")
        finally:
            os.close(fd)


class UnavailableExecutorCapability:
    """R0 default: E3 has not supplied a production-equivalent proof."""

    def require_real_fence(self) -> None:
        raise FencedExecutorUnavailable("fenced_executor_unavailable")


@dataclass(frozen=True)
class ControlledTestExecutorCapability:
    """Explicit test double; never evidence that an OS fence is present."""

    available: bool = False

    def require_real_fence(self) -> None:
        if not self.available:
            raise FencedExecutorUnavailable("fenced_executor_unavailable")


@dataclass(frozen=True)
class FencedAdmission:
    status: str
    handle: ReservationHandle | None = None
    staging_locator: str | None = None


@dataclass(frozen=True)
class FencedPublishResult:
    status: str
    intent: PublishIntent | None = None


def staging_locator(handle: ReservationHandle) -> str:
    """Return an opaque service locator, never an openable host path."""

    digest = hashlib.sha256(handle.key.canonical_artifact_path.encode("utf-8")).hexdigest()[:16]
    return "/".join(
        (
            "fenced-staging",
            handle.key.thread_id,
            digest,
            f"g-{handle.generation}",
            handle.execution_id,
            "out",
            "artifact.md",
        )
    )


def legacy_preflight(key: FencedArtifactKey, audit_bytes: bytes) -> str | None:
    """Return the required quarantine reason without ever opening an audit path.

    A legacy projection cannot prove that its writer lost a canonical FD.  It
    can only identify an unverified conflict, including malformed bytes and an
    unterminated tail in the owner/thread audit scope.
    """

    if not audit_bytes:
        return None
    if not audit_bytes.endswith(b"\n"):
        return "legacy_unverified"
    for line in audit_bytes.splitlines():
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "legacy_unverified"
        if not isinstance(record, dict):
            return "legacy_unverified"
        path = record.get("canonical_artifact_path")
        if not isinstance(path, str):
            return "legacy_unverified"
        if path != key.canonical_artifact_path:
            continue
        # Even a line claiming fenced_staging is only a projection.  It is not
        # durable authority/proof and therefore cannot authorize this key.
        return "legacy_unverified"
    return None


async def _quarantine_status(
    repository: ArtifactReservationRepository,
    handle: ReservationHandle,
    *,
    reason_code: str,
) -> str:
    status = await repository.quarantine(handle, reason_code=reason_code)
    return reason_code if status == "quarantined" else status


class FencedStagingAdapter:
    """Admission boundary that is closed until a real executor is proven."""

    def __init__(
        self,
        repository: ArtifactReservationRepository,
        *,
        enabled: bool = False,
        secure_dirfd: SecureDirfdCapability | None = None,
        executor: ExecutorCapability | None = None,
    ) -> None:
        self._repository = repository
        self._enabled = enabled
        self._secure_dirfd = secure_dirfd or SecureDirfdGate()
        self._executor = executor or UnavailableExecutorCapability()

    async def admit(
        self,
        key: FencedArtifactKey,
        *,
        run_id: str,
        task_id: str,
        legacy_audit: bytes = b"",
    ) -> FencedAdmission:
        reserved = await self._repository.reserve(key, run_id=run_id, task_id=task_id)
        if reserved.handle is None:
            return FencedAdmission(status=reserved.status)
        if reason_code := legacy_preflight(key, legacy_audit):
            return FencedAdmission(status=await _quarantine_status(self._repository, reserved.handle, reason_code=reason_code))
        try:
            self._secure_dirfd.require_secure_dirfd()
            if not self._enabled:
                raise FencedExecutorUnavailable("fenced_executor_unavailable")
            self._executor.require_real_fence()
        except FencedExecutorUnavailable:
            return FencedAdmission(
                status=await _quarantine_status(
                    self._repository,
                    reserved.handle,
                    reason_code="fenced_executor_unavailable",
                )
            )

        locator = staging_locator(reserved.handle)
        status = await self._repository.activate(reserved.handle, staging_locator=locator)
        if status != "active":
            return FencedAdmission(
                status=await _quarantine_status(
                    self._repository,
                    reserved.handle,
                    reason_code="fenced_executor_unavailable",
                )
            )
        return FencedAdmission(status="active", handle=reserved.handle, staging_locator=locator)

    async def recover_unknown_execution(self, handle: ReservationHandle) -> str:
        """Crash recovery fences/quarantines; it never retries or takes over."""

        return await self._repository.quarantine(handle, reason_code="publisher_recovery_unknown")


class ProtectedPublisher:
    """Narrow CAS publisher interface; actual filesystem publication is E3-blocked."""

    def __init__(
        self,
        repository: ArtifactReservationRepository,
        *,
        enabled: bool = False,
        secure_dirfd: SecureDirfdCapability | None = None,
        executor: ExecutorCapability | None = None,
    ) -> None:
        self._repository = repository
        self._enabled = enabled
        self._secure_dirfd = secure_dirfd or SecureDirfdGate()
        self._executor = executor or UnavailableExecutorCapability()

    async def publish(self, handle: ReservationHandle, *, publish_id: str, snapshot: SnapshotMetadata) -> FencedPublishResult:
        try:
            self._secure_dirfd.require_secure_dirfd()
            if not self._enabled:
                raise FencedExecutorUnavailable("fenced_executor_unavailable")
            self._executor.require_real_fence()
        except FencedExecutorUnavailable:
            return FencedPublishResult(
                status=await _quarantine_status(
                    self._repository,
                    handle,
                    reason_code="fenced_executor_unavailable",
                )
            )

        try:
            intent = await self._repository.begin_publish(handle, publish_id=publish_id)
        except SQLAlchemyError:
            return FencedPublishResult(status="quarantine_required")
        if intent.status not in {"publishing", "publish_intent_replayed", "published"}:
            return FencedPublishResult(status=intent.status, intent=intent)
        return FencedPublishResult(
            status=await self._repository.complete_publish(handle, publish_id=publish_id, snapshot=snapshot),
            intent=intent,
        )
