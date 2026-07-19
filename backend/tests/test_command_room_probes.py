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


class FakeClient:
    def __init__(self, **_kwargs):
        pass

    def stream(self, *_args, **_kwargs):
        yield SimpleNamespace(
            type="messages-tuple",
            data={
                "type": "ai",
                "id": "ai-1",
                "content": "Natural Command Room result.",
                "tool_calls": [
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": "opposition",
                            "description": "Challenge from the other direction",
                        },
                    }
                ],
            },
        )
        yield SimpleNamespace(type="end", data={"usage": {"total_tokens": 1}})


def _assert_fact_only_capture(probe, monkeypatch):
    monkeypatch.setattr(probe, "DeerFlowClient", FakeClient)

    result = probe._run_probe("thread-1")

    assert result["tool_names"] == ["task"]
    assert result["task_types"] == ["opposition"]
    assert result["final_text"] == "Natural Command Room result."
    assert "ok" not in result
    assert "checks" not in result


def test_ai_native_probe_captures_facts_without_program_verdict(monkeypatch):
    _assert_fact_only_capture(_load_probe("command-room-ai-native-probe.py"), monkeypatch)


def test_opposition_probe_captures_facts_without_program_verdict(monkeypatch):
    _assert_fact_only_capture(_load_probe("command-room-opposition-probe.py"), monkeypatch)


def test_command_room_probes_request_plan_then_opposition_then_chair_synthesis():
    for filename in (
        "command-room-ai-native-probe.py",
        "command-room-opposition-probe.py",
    ):
        prompt = _load_probe(filename).PROMPT
        assert "planner 先形成完整方案" in prompt
        assert "方案返回后" in prompt
        assert "opposition 跑一轮" in prompt
        assert "指挥室合成定案" in prompt
        assert "记录为 Goal Mandate" in prompt
        assert "不等待逐计划人工确认" in prompt
        assert "人明确确认执行后" not in prompt
