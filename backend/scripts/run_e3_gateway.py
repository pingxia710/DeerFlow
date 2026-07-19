"""Start one loopback-only Gateway process for the WP-1 E3 runner."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "tests"))


def _value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()

    from e3_control_runtime import mount_controls, validate_mount, write_control

    from deerflow.config.app_config import get_app_config
    from deerflow.tools.builtins.task_tool import _task_sandbox_mode

    root, mode = validate_mount(host=args.host)
    config = get_app_config()
    sandbox = _value(config, "sandbox", {})
    database = _value(config, "database", {})
    enterprise = _value(config, "enterprise", {})
    facts = {
        "home_within_root": Path(os.environ["DEER_FLOW_HOME"]).resolve().is_relative_to(root / "home"),
        "sqlite_within_root": str(_value(database, "backend", "")) == "sqlite" and Path(str(_value(database, "sqlite_dir", ""))).resolve().is_relative_to(root),
        "extensions_within_root": Path(os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"]).resolve().is_relative_to(root),
        "bind_loopback": True,
        "sandbox_provider_local": str(_value(sandbox, "use", "")) == "deerflow.sandbox.local:LocalSandboxProvider",
        "allow_host_bash_false": _value(sandbox, "allow_host_bash") is False,
        "unrestricted_host_access_false": _value(sandbox, "unrestricted_host_access") is False,
        "workspace_write": _task_sandbox_mode(config) == "workspace-write",
        "wake_facts_projection": _value(enterprise, "wake_facts_projection") is True if mode == "wf" else True,
    }
    write_control(root / "evidence" / "effective-config.json", facts)
    if not all(facts.values()):
        raise RuntimeError("E3 isolation preflight failed")

    from app.gateway.app import app

    mount_controls(app, root=root, mode=mode)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
