import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.worker import RunContext, run_agent


def test_same_tool_call_id_is_scoped_by_run() -> None:
    task_tool = importlib.import_module("deerflow.tools.builtins.task_tool")
    task_tool._subagent_usage_cache.clear()
    usage_a = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
    usage_b = {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}
    task_tool._cache_subagent_usage("call-1", usage_a, run_id="run-a")
    task_tool._cache_subagent_usage("call-1", usage_b, run_id="run-b")

    messages = [
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "task", "args": {}}]),
        ToolMessage(content="done", tool_call_id="call-1"),
        AIMessage(content="next"),
    ]
    runtime = SimpleNamespace(context={"run_id": "run-a"}, config={})

    result = TokenUsageMiddleware().after_model({"messages": messages}, runtime)

    assert result is not None
    [dispatch_update] = [message for message in result["messages"] if getattr(message, "tool_calls", None)]
    assert dispatch_update.usage_metadata == usage_a
    assert task_tool.pop_cached_subagent_usage("call-1", run_id="run-b") == usage_b
    assert task_tool.pop_cached_subagent_usage("call-1", run_id="run-a") is None


@pytest.mark.anyio
async def test_run_terminal_clears_only_its_cached_subagent_usage() -> None:
    task_tool = importlib.import_module("deerflow.tools.builtins.task_tool")
    task_tool._subagent_usage_cache.clear()
    run_manager = RunManager()
    record = await run_manager.create("thread-usage-cleanup")
    own_usage = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
    other_usage = {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}
    task_tool._cache_subagent_usage("own-call", own_usage, run_id=record.run_id)
    task_tool._cache_subagent_usage("other-call", other_usage, run_id="other-run")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )

    class DummyAgent:
        async def astream(self, *args, **kwargs):
            yield {"messages": []}

    try:
        await run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None),
            agent_factory=lambda *, config: DummyAgent(),
            graph_input={},
            config={},
        )

        assert task_tool.pop_cached_subagent_usage("own-call", run_id=record.run_id) is None
        assert task_tool.pop_cached_subagent_usage("other-call", run_id="other-run") == other_usage
    finally:
        task_tool._subagent_usage_cache.clear()
