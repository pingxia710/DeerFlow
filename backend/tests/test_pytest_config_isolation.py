import os
from pathlib import Path

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.runtime_paths import project_root, runtime_home

_REPO_CONFIG_PATH = Path("/Users/pingxia/projects/deer-flow/config.yaml")


def _env_path(name: str) -> Path:
    value = os.environ.get(name)
    assert value, f"{name} should be set by tests/conftest.py"
    path = Path(value)
    assert path.exists()
    assert "pytest-" in str(path) or "pytest-of-" in str(path)
    return path.resolve()


def test_pytest_uses_isolated_config_home_and_skills_paths():
    assert os.environ.get("DEER_FLOW_ENV") == "test"

    config_path = _env_path("DEER_FLOW_CONFIG_PATH")
    extensions_path = _env_path("DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    isolated_project_root = _env_path("DEER_FLOW_PROJECT_ROOT")
    isolated_home = _env_path("DEER_FLOW_HOME")

    assert AppConfig.resolve_config_path().resolve() == config_path
    assert AppConfig.resolve_config_path().resolve() != _REPO_CONFIG_PATH

    assert project_root() == isolated_project_root
    assert runtime_home() == isolated_home

    app_config = get_app_config()
    assert app_config.sandbox.use == "deerflow.sandbox.local:LocalSandboxProvider"
    assert app_config.sandbox.mounts == []

    assert ExtensionsConfig.resolve_config_path().resolve() == extensions_path
