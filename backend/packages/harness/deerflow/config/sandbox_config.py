import re
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_WINDOWS_DRIVE_ROOT_RE = re.compile(r"^[a-zA-Z]:[/\\]?$")
_WINDOWS_USERS_ROOT_RE = re.compile(r"^[a-z]:/users$")
_WINDOWS_USER_HOME_RE = re.compile(r"^[a-z]:/users/[^/]+$")
_SENSITIVE_PATH_PARTS = {
    ".aws",
    ".azure",
    ".docker",
    ".gnupg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
}
_SENSITIVE_EXACT_PATHS = {
    "/home",
    "/users",
}
_SENSITIVE_PREFIXES = {
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/private/etc",
    "/private/var/run/docker.sock",
    "/proc",
    "/root",
    "/run/docker.sock",
    "/sbin",
    "/sys",
    "/usr/bin",
    "/usr/sbin",
    "/var/lib/docker",
    "/var/lib/kubelet",
    "/var/run/docker.sock",
}


def _normalized_host_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/").rstrip("/")
    return normalized or "/"


def dangerous_host_mount_reason(host_path: str) -> str | None:
    """Return why a configured host mount is unsafe by default, if any."""
    raw = _normalized_host_path(host_path)
    lowered = raw.lower()

    if raw == "/" or _WINDOWS_DRIVE_ROOT_RE.match(raw):
        return "root filesystem mount"

    home = str(Path.home()).replace("\\", "/").rstrip("/")
    if home and raw == home:
        return "home directory root mount"

    if lowered == "~" or lowered.startswith("~/"):
        return "home directory mount"

    if _WINDOWS_USERS_ROOT_RE.match(lowered):
        return "home parent directory mount"
    if _WINDOWS_USER_HOME_RE.match(lowered):
        return "home directory root mount"

    if lowered in _SENSITIVE_EXACT_PATHS:
        return "home parent directory mount"
    if (lowered.startswith("/home/") or lowered.startswith("/users/")) and lowered.count("/") == 2:
        return "home directory root mount"

    parts = {part.lower() for part in lowered.split("/") if part}
    if any(part.startswith("docker.sock") for part in parts):
        return "Docker socket mount"

    if parts & _SENSITIVE_PATH_PARTS:
        return "sensitive credential/config path mount"

    for prefix in _SENSITIVE_PREFIXES:
        if lowered == prefix or lowered.startswith(prefix + "/"):
            return f"sensitive system path mount: {prefix}"

    return None


class VolumeMountConfig(BaseModel):
    """Configuration for a volume mount."""

    host_path: str = Field(
        ...,
        description=(
            "Source path for the mount. Resolution depends on the active provider: "
            "``LocalSandboxProvider`` checks this path from the gateway process — in "
            "``make dev`` that is the host machine, but in Docker deployments "
            "(``make up`` / docker-compose) it is the path *inside* the "
            "``deer-flow-gateway`` container, so the host directory must also be "
            "bind-mounted into the gateway service for the mount to take effect. "
            "``AioSandboxProvider`` (DooD) passes this value straight to ``docker -v`` "
            "for the sandbox container, where it is resolved by the host Docker daemon "
            "from the host machine's perspective."
        ),
    )
    container_path: str = Field(..., description="Path inside the container")
    read_only: bool = Field(default=True, description="Whether the mount is read-only")


class SandboxConfig(BaseModel):
    """Config section for a sandbox.

    Common options:
        use: Class path of the sandbox provider (required)
        allow_host_bash: Enable host-side bash execution for LocalSandboxProvider.
            Dangerous and intended only for fully trusted local workflows.

    AioSandboxProvider specific options:
        image: Docker image to use (default: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest)
        port: Base port for sandbox containers (default: 8080)
        replicas: Maximum number of concurrent sandbox containers (default: 3). When the limit is reached the least-recently-used sandbox is evicted to make room.
        container_prefix: Prefix for container names (default: deer-flow-sandbox)
        idle_timeout: Idle timeout in seconds before sandbox is released (default: 600 = 10 minutes). Set to 0 to disable.
        mounts: List of volume mounts to share directories with the container
        environment: Environment variables to inject into the container (values starting with $ are resolved from host env)
    """

    use: str = Field(
        ...,
        description="Class path of the sandbox provider (e.g. deerflow.sandbox.local:LocalSandboxProvider)",
    )
    allow_host_bash: bool = Field(
        default=False,
        description="Allow the bash tool to execute directly on the host when using LocalSandboxProvider. Dangerous; intended only for fully trusted local environments.",
    )
    default_cwd: str | None = Field(
        default=None,
        description=(
            "Default working directory for LocalSandboxProvider bash commands. If unset, bash starts in the per-thread workspace. "
            "Use an allowed virtual path in scoped mode; direct host paths are accepted only when unrestricted_host_access is enabled."
        ),
    )
    unrestricted_host_access: bool = Field(
        default=False,
        description="Allow LocalSandboxProvider tools to access arbitrary host paths directly. Dangerous; use only in fully trusted single-user local workflows.",
    )
    image: str | None = Field(
        default=None,
        description="Docker image to use for the sandbox container",
    )
    port: int | None = Field(
        default=None,
        description="Base port for sandbox containers",
    )
    replicas: int | None = Field(
        default=None,
        description="Maximum number of concurrent sandbox containers (default: 3). When the limit is reached the least-recently-used sandbox is evicted to make room.",
    )
    container_prefix: str | None = Field(
        default=None,
        description="Prefix for container names",
    )
    idle_timeout: int | None = Field(
        default=None,
        description="Idle timeout in seconds before sandbox is released (default: 600 = 10 minutes). Set to 0 to disable.",
    )
    mounts: list[VolumeMountConfig] = Field(
        default_factory=list,
        description="List of volume mounts to share directories between host and container",
    )
    allow_dangerous_host_mounts: bool = Field(
        default=False,
        description="Allow sandbox.mounts host paths that expose root, home roots, Docker sockets, or credential directories. Dangerous; use only for fully trusted local debugging.",
    )
    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to inject into the sandbox container. Values starting with $ will be resolved from host environment variables.",
    )
    seccomp_unconfined: bool = Field(
        default=False,
        description="Pass --security-opt seccomp=unconfined to Docker-based AIO sandbox containers. Dangerous; keep disabled unless a custom sandbox image explicitly requires it.",
    )

    bash_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="Maximum characters to keep from bash tool output. Output exceeding this limit is middle-truncated (head + tail), preserving the first and last half. Set to 0 to disable truncation.",
    )
    read_file_output_max_chars: int = Field(
        default=50000,
        ge=0,
        description="Maximum characters to keep from read_file tool output. Output exceeding this limit is head-truncated. Set to 0 to disable truncation.",
    )
    ls_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="Maximum characters to keep from ls tool output. Output exceeding this limit is head-truncated. Set to 0 to disable truncation.",
    )

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _reject_dangerous_host_mounts(self) -> Self:
        if self.allow_dangerous_host_mounts:
            return self

        for mount in self.mounts:
            reason = dangerous_host_mount_reason(mount.host_path)
            if reason:
                raise ValueError(
                    f"sandbox.mounts host_path {mount.host_path!r} is unsafe by default ({reason}); mount a narrower project directory or set sandbox.allow_dangerous_host_mounts: true only in a fully trusted local environment."
                )
        return self
