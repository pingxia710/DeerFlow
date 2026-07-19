"""Tests for deerflow.models.openai_codex_provider.CodexChatModel.

Covers:
- LangChain serialization: is_lc_serializable, to_json kwargs, no token leakage
- _parse_response: text content, tool calls, reasoning_content
- _convert_messages: SystemMessage, HumanMessage, AIMessage, ToolMessage
- _parse_sse_data_line: valid data, [DONE], non-JSON, non-data lines
- _parse_tool_call_arguments: valid JSON, invalid JSON, non-dict JSON
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.models import openai_codex_provider as codex_provider_module
from deerflow.models.credential_loader import CodexCliCredential


def _make_model(**kwargs):
    from deerflow.models.openai_codex_provider import CodexChatModel

    cred = CodexCliCredential(access_token="tok-test", account_id="acc-test")
    with patch("deerflow.models.openai_codex_provider.load_codex_cli_credential", return_value=cred):
        return CodexChatModel(model="gpt-5.4", reasoning_effort="medium", **kwargs)


# ---------------------------------------------------------------------------
# Serialization protocol
# ---------------------------------------------------------------------------


def test_is_lc_serializable_returns_true():
    from deerflow.models.openai_codex_provider import CodexChatModel

    assert CodexChatModel.is_lc_serializable() is True


def test_to_json_produces_constructor_type():
    model = _make_model()
    result = model.to_json()
    assert result["type"] == "constructor"
    assert "kwargs" in result


def test_to_json_contains_model_and_reasoning_effort():
    model = _make_model()
    result = model.to_json()
    assert result["kwargs"]["model"] == "gpt-5.4"
    assert result["kwargs"]["reasoning_effort"] == "medium"


def test_to_json_does_not_leak_access_token():
    """_access_token is not a Pydantic field and must not appear in serialized kwargs."""
    model = _make_model()
    result = model.to_json()
    kwargs_str = json.dumps(result["kwargs"])
    assert "tok-test" not in kwargs_str
    assert "_access_token" not in kwargs_str
    assert "_account_id" not in kwargs_str


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


def test_parse_response_text_content():
    model = _make_model()
    response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello world"}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "model": "gpt-5.4",
    }
    result = model._parse_response(response)
    assert result.generations[0].message.content == "Hello world"


def test_parse_response_populates_usage_metadata():
    model = _make_model()
    response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello world"}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
        "model": "gpt-5.4",
    }

    result = model._parse_response(response)

    meta = result.generations[0].message.usage_metadata
    assert meta is not None
    assert meta["input_tokens"] == 10
    assert meta["output_tokens"] == 5
    assert meta["total_tokens"] == 15
    assert meta["input_token_details"]["cache_read"] == 3
    assert meta["output_token_details"]["reasoning"] == 2


def test_parse_response_reasoning_content():
    model = _make_model()
    response = {
        "output": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "I reasoned about this."}],
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Answer"}],
            },
        ],
        "usage": {},
    }
    result = model._parse_response(response)
    msg = result.generations[0].message
    assert msg.content == "Answer"
    assert msg.additional_kwargs["reasoning_content"] == "I reasoned about this."


def test_parse_response_tool_call():
    model = _make_model()
    response = {
        "output": [
            {
                "type": "function_call",
                "name": "web_search",
                "arguments": '{"query": "test"}',
                "call_id": "call_abc",
            }
        ],
        "usage": {},
    }
    result = model._parse_response(response)
    tool_calls = result.generations[0].message.tool_calls
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "web_search"
    assert tool_calls[0]["args"] == {"query": "test"}
    assert tool_calls[0]["id"] == "call_abc"


def test_parse_response_invalid_tool_call_arguments():
    model = _make_model()
    response = {
        "output": [
            {
                "type": "function_call",
                "name": "bad_tool",
                "arguments": "not-json",
                "call_id": "call_bad",
            }
        ],
        "usage": {},
    }
    result = model._parse_response(response)
    msg = result.generations[0].message
    assert len(msg.tool_calls) == 0
    assert len(msg.invalid_tool_calls) == 1
    assert msg.invalid_tool_calls[0]["name"] == "bad_tool"


# ---------------------------------------------------------------------------
# _convert_messages
# ---------------------------------------------------------------------------


def test_convert_messages_human():
    model = _make_model()
    _, items = model._convert_messages([HumanMessage(content="Hello")])
    assert items == [{"role": "user", "content": "Hello"}]


def test_convert_messages_system_becomes_instructions():
    model = _make_model()
    instructions, items = model._convert_messages([SystemMessage(content="You are helpful.")])
    assert "You are helpful." in instructions
    assert items == []


def test_convert_messages_ai_with_tool_calls():
    model = _make_model()
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "search", "args": {"q": "foo"}, "id": "tc1", "type": "tool_call"}],
    )
    _, items = model._convert_messages([ai])
    assert any(item.get("type") == "function_call" and item["name"] == "search" for item in items)


def test_convert_messages_preserves_completed_task_prompt_replay():
    model = _make_model()
    original_prompt = "inspect the frontend"
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": "Audit frontend", "prompt": original_prompt, "subagent_type": "executor"},
                "id": "tc1",
                "type": "tool_call",
            }
        ],
    )
    tool = ToolMessage(content="Task result", tool_call_id="tc1")

    _, items = model._convert_messages([ai, tool])

    arguments = json.loads(items[0]["arguments"])
    assert arguments["description"] == "Audit frontend"
    assert arguments["subagent_type"] == "executor"
    assert arguments["prompt"] == original_prompt


def test_convert_messages_keeps_pending_task_prompt_before_tool_output():
    model = _make_model()
    long_prompt = "x" * 5000
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": "Audit backend", "prompt": long_prompt, "subagent_type": "executor"},
                "id": "tc1",
                "type": "tool_call",
            }
        ],
    )

    _, items = model._convert_messages([ai])

    arguments = json.loads(items[0]["arguments"])
    assert arguments["prompt"] == long_prompt


def test_convert_messages_keeps_completed_non_task_arguments():
    model = _make_model()
    long_query = "x" * 5000
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search",
                "args": {"q": long_query},
                "id": "tc1",
                "type": "tool_call",
            }
        ],
    )
    tool = ToolMessage(content="Search result", tool_call_id="tc1")

    _, items = model._convert_messages([ai, tool])

    arguments = json.loads(items[0]["arguments"])
    assert arguments["q"] == long_query


def test_convert_messages_tool_message():
    model = _make_model()
    tool_msg = ToolMessage(content="result data", tool_call_id="tc1")
    _, items = model._convert_messages([tool_msg])
    assert items[0]["type"] == "function_call_output"
    assert items[0]["call_id"] == "tc1"
    assert items[0]["output"] == "result data"


def test_call_codex_api_includes_response_controls(monkeypatch):
    model = _make_model(reasoning_summary="concise", text_verbosity="high")
    captured: dict = {}

    def fake_stream_response(headers, payload):
        captured["payload"] = payload
        return {"output": [], "usage": {}}

    monkeypatch.setattr(model, "_refresh_codex_auth", lambda: None)
    monkeypatch.setattr(model, "_stream_response", fake_stream_response)

    model._call_codex_api([HumanMessage(content="Hello")])

    assert captured["payload"]["reasoning"] == {
        "effort": "medium",
        "summary": "concise",
    }
    assert captured["payload"]["text"] == {"verbosity": "high"}


@pytest.mark.parametrize(
    ("reasoning_effort", "expected_effort"),
    [("max", "max"), ("ultra", "xhigh")],
)
def test_call_codex_api_preserves_max_and_blocks_raw_ultra(monkeypatch, reasoning_effort, expected_effort):
    model = _make_model()
    model.reasoning_effort = reasoning_effort
    captured: dict = {}

    def fake_stream_response(headers, payload):
        captured["payload"] = payload
        return {"output": [], "usage": {}}

    monkeypatch.setattr(model, "_refresh_codex_auth", lambda: None)
    monkeypatch.setattr(model, "_stream_response", fake_stream_response)

    model._call_codex_api([HumanMessage(content="Hello")])

    assert captured["payload"]["reasoning"]["effort"] == expected_effort


def test_call_codex_api_retries_transient_connection_errors(monkeypatch):
    model = _make_model()
    attempts = 0
    sleeps = []

    def flaky_stream_response(headers, payload):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("Connection refused")
        return {"output": [], "usage": {}}

    monkeypatch.setattr(model, "_refresh_codex_auth", lambda: None)
    monkeypatch.setattr(model, "_stream_response", flaky_stream_response)
    monkeypatch.setattr("deerflow.models.openai_codex_provider.time.sleep", lambda seconds: sleeps.append(seconds))

    assert model._call_codex_api([HumanMessage(content="Hello")]) == {"output": [], "usage": {}}
    assert attempts == 3
    assert sleeps == [1, 2]


def test_call_codex_api_marks_exhausted_provider_retry_budget(monkeypatch):
    from deerflow.models.openai_codex_provider import CodexRetryExhaustedError

    model = _make_model(retry_max_attempts=2)
    attempts = 0

    def timed_out_stream_response(headers, payload):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("The read operation timed out")

    monkeypatch.setattr(model, "_refresh_codex_auth", lambda: None)
    monkeypatch.setattr(model, "_stream_response", timed_out_stream_response)
    monkeypatch.setattr("deerflow.models.openai_codex_provider.time.sleep", lambda _seconds: None)

    with pytest.raises(CodexRetryExhaustedError) as caught:
        model._call_codex_api([HumanMessage(content="Hello")])

    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
    assert attempts == 2


def test_call_codex_api_retries_incomplete_stream(monkeypatch):
    from deerflow.models.openai_codex_provider import CodexStreamIncompleteError

    model = _make_model()
    attempts = 0
    sleeps = []

    def flaky_stream_response(headers, payload):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise CodexStreamIncompleteError("Codex API stream ended without response.completed event")
        return {"output": [], "usage": {}}

    monkeypatch.setattr(model, "_refresh_codex_auth", lambda: None)
    monkeypatch.setattr(model, "_stream_response", flaky_stream_response)
    monkeypatch.setattr("deerflow.models.openai_codex_provider.time.sleep", lambda seconds: sleeps.append(seconds))

    assert model._call_codex_api([HumanMessage(content="Hello")]) == {"output": [], "usage": {}}
    assert attempts == 2
    assert sleeps == [1]


def test_stream_response_logs_bounded_redacted_http_error(monkeypatch, caplog):
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported reasoning effort\nforged-log-line; api_key=secret-value",
                    "type": "invalid_request_error",
                    "param": "reasoning.effort",
                    "code": "unsupported_value",
                }
            },
            headers={"x-request-id": "req-test-123"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(codex_provider_module.httpx, "Client", lambda **kwargs: client)
    model = _make_model()

    with caplog.at_level("ERROR", logger="deerflow.models.openai_codex_provider"):
        with pytest.raises(httpx.HTTPStatusError):
            model._stream_response({}, {"model": "gpt-5.4"})

    assert "req-test-123" in caplog.text
    assert "unsupported_value" in caplog.text
    assert "reasoning.effort" in caplog.text
    assert "secret-value" not in caplog.text
    assert "\n" not in caplog.messages[0]


# ---------------------------------------------------------------------------
# _parse_sse_data_line
# ---------------------------------------------------------------------------


def test_parse_sse_data_line_valid():
    from deerflow.models.openai_codex_provider import CodexChatModel

    data = {"type": "response.completed", "response": {}}
    line = "data: " + json.dumps(data)
    assert CodexChatModel._parse_sse_data_line(line) == data


def test_parse_sse_data_line_done_returns_none():
    from deerflow.models.openai_codex_provider import CodexChatModel

    assert CodexChatModel._parse_sse_data_line("data: [DONE]") is None


def test_parse_sse_data_line_non_data_returns_none():
    from deerflow.models.openai_codex_provider import CodexChatModel

    assert CodexChatModel._parse_sse_data_line("event: ping") is None


def test_parse_sse_data_line_invalid_json_returns_none():
    from deerflow.models.openai_codex_provider import CodexChatModel

    assert CodexChatModel._parse_sse_data_line("data: {bad json}") is None


# ---------------------------------------------------------------------------
# _parse_tool_call_arguments
# ---------------------------------------------------------------------------


def test_parse_tool_call_arguments_valid_string():
    model = _make_model()
    parsed, err = model._parse_tool_call_arguments({"arguments": '{"key": "val"}', "name": "t", "call_id": "c"})
    assert parsed == {"key": "val"}
    assert err is None


def test_parse_tool_call_arguments_already_dict():
    model = _make_model()
    parsed, err = model._parse_tool_call_arguments({"arguments": {"key": "val"}, "name": "t", "call_id": "c"})
    assert parsed == {"key": "val"}
    assert err is None


def test_parse_tool_call_arguments_invalid_json():
    model = _make_model()
    parsed, err = model._parse_tool_call_arguments({"arguments": "not-json", "name": "t", "call_id": "c"})
    assert parsed is None
    assert err is not None
    assert "Failed to parse" in err["error"]


def test_parse_tool_call_arguments_non_dict_json():
    model = _make_model()
    parsed, err = model._parse_tool_call_arguments({"arguments": '["list", "not", "dict"]', "name": "t", "call_id": "c"})
    assert parsed is None
    assert err is not None
