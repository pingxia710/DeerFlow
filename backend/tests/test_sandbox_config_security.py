import pytest
from pydantic import ValidationError

from deerflow.config.sandbox_config import SandboxConfig, VolumeMountConfig, dangerous_host_mount_reason


def test_custom_mounts_default_to_read_only() -> None:
    mount = VolumeMountConfig(host_path="/tmp/project", container_path="/mnt/project")

    assert mount.read_only is True


@pytest.mark.parametrize(
    ("host_path", "reason"),
    [
        ("/", "root filesystem"),
        ("/home/user", "home directory root"),
        ("/Users/user", "home directory root"),
        ("/var/run/docker.sock", "Docker socket"),
        ("/private/var/run/docker.sock.raw", "Docker socket"),
        ("/tmp/project/.ssh", "sensitive credential"),
    ],
)
def test_sandbox_config_rejects_dangerous_host_mounts(host_path: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        SandboxConfig(
            use="deerflow.community.aio_sandbox:AioSandboxProvider",
            mounts=[VolumeMountConfig(host_path=host_path, container_path="/mnt/project")],
        )


def test_sandbox_config_allows_narrow_project_mounts() -> None:
    config = SandboxConfig(
        use="deerflow.community.aio_sandbox:AioSandboxProvider",
        mounts=[VolumeMountConfig(host_path="/home/user/project", container_path="/mnt/project")],
    )

    assert config.mounts[0].host_path == "/home/user/project"


def test_sandbox_config_dangerous_mount_escape_hatch_is_explicit() -> None:
    config = SandboxConfig(
        use="deerflow.community.aio_sandbox:AioSandboxProvider",
        allow_dangerous_host_mounts=True,
        mounts=[VolumeMountConfig(host_path="/var/run/docker.sock", container_path="/mnt/docker.sock")],
    )

    assert config.allow_dangerous_host_mounts is True


def test_dangerous_host_mount_reason_allows_normal_windows_project_path() -> None:
    assert dangerous_host_mount_reason("D:/workspace/project") is None
