import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_probe(filename: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_goal_first_probe_accepts_direct_discovery_without_opposition(monkeypatch):
    probe = _load_probe("command-room-ai-native-probe.py")

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def stream(self, *_args, **_kwargs):
            yield SimpleNamespace(
                type="messages-tuple",
                data={
                    "type": "ai",
                    "id": "ai-1",
                    "content": "",
                    "tool_calls": [{"name": "bash", "args": {"command": "pwd"}}],
                },
            )
            yield SimpleNamespace(type="end", data={"usage": {"total_tokens": 1}})

    monkeypatch.setattr(probe, "DeerFlowClient", FakeClient)

    result = probe._run_probe("thread-1")

    assert result["ok"] is True
    assert result["task_types"] == []
    assert result["checks"]["no_default_opposition"] is True


def test_high_risk_probe_requires_no_fixed_opposition_verdict():
    probe = _load_probe("command-room-opposition-probe.py")

    assert probe._execution_not_approved("这里仍需用户授权，不能进入真实执行。")
    assert not probe._execution_not_approved("可以进入真实执行。")
