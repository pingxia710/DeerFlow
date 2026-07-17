"""Typed inputs and opaque handles for FENCED_STAGING authority."""

from __future__ import annotations

from dataclasses import dataclass

WRITER_MODE_FENCED_STAGING = "fenced_staging"


@dataclass(frozen=True)
class FencedArtifactRoute:
    """Service-owned fields used to select one fixed Markdown artifact."""

    work_package_id: str | None
    container: str
    artifact_kind: str
    delivery_cycle_index: int | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class FencedArtifactKey:
    """The only durable authority key for one canonical artifact."""

    user_id: str
    thread_id: str
    canonical_artifact_path: str
    route: FencedArtifactRoute


@dataclass(frozen=True)
class ReservationHandle:
    """Protected service handle; ``owner_token`` must never reach a child."""

    reservation_id: str
    key: FencedArtifactKey
    generation: int
    execution_id: str
    owner_token: str
    run_id: str
    task_id: str


@dataclass(frozen=True)
class ReservationResult:
    status: str
    handle: ReservationHandle | None = None


@dataclass(frozen=True)
class PublishIntent:
    status: str
    publish_id: str


@dataclass(frozen=True)
class SnapshotMetadata:
    """Facts produced by a protected publisher, never child-provided paths."""

    sha256: str
    size_bytes: int
    device: int
    inode: int


@dataclass(frozen=True)
class ReservationSnapshot:
    reservation_id: str
    generation: int
    state: str
    execution_id: str
    publish_id: str | None
    quarantine_reason: str | None
