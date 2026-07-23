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


def test_readonly_probe_captures_tool_trace_facts(monkeypatch):
    probe = _load_probe("command-room-readonly-probe.py")
    monkeypatch.setattr(probe, "DeerFlowClient", FakeClient)

    result = probe._run_probe("thread-1")

    assert result["tool_names"] == ["task"]
    assert result["task_calls"] == 1
    assert result["record_goal_workspace_calls"] == 0
    assert result["read_tool_calls"] == 0
    assert result["final_text"] == "Natural Command Room result."


def test_readonly_probe_prompt_requests_direct_readonly_answer():
    prompt = _load_probe("command-room-readonly-probe.py").PROMPT
    assert "只读" in prompt
    assert "直接用你的只读工具" in prompt
    assert "不要记录 Goal Mandate" in prompt
    assert "不要派发任何子任务" in prompt
    assert "planner 先形成完整方案" not in prompt
    assert "opposition 跑一轮" not in prompt


def test_command_room_probes_request_plan_then_opposition_then_chair_synthesis():
    for filename in (
        "command-room-ai-native-probe.py",
        "command-room-opposition-probe.py",
    ):
        prompt = _load_probe(filename).PROMPT
        assert "指挥室自己形成完整方案" in prompt
        assert "方案形成后" in prompt
        assert "opposition 跑一轮" in prompt
        assert "指挥室合成定案" in prompt
        assert "记录为 Goal Mandate" in prompt
        assert "不等待逐计划人工确认" in prompt
        assert "人明确确认执行后" not in prompt
