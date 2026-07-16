"""Custom OpenAI Codex provider using ChatGPT Codex Responses API.

Uses Codex CLI OAuth tokens with chatgpt.com/backend-api/codex/responses endpoint.
This is the same endpoint that the Codex CLI uses internally.

Supports:
- Auto-load credentials from ~/.codex/auth.json
- Responses API format (not Chat Completions)
- Tool calling
- Streaming (required by the endpoint)
- Retry with exponential backoff
"""

import json
import logging
import re
import time
from typing import Any

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from deerflow.models.credential_loader import CodexCliCredential, load_codex_cli_credential

logger = logging.getLogger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_SENSITIVE_ERROR_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|authorization|bearer|secret|password)\b\s*[:=]\s*[^,\s;}]+")
_TERMINAL_RESPONSE_EVENTS = frozenset({"response.failed", "response.incomplete", "response.cancelled"})


def _safe_error_detail(response: httpx.Response) -> str:
    """Return bounded structured provider diagnostics without raw response data."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "non-json response"

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return "unstructured response"
    detail = " ".join(" ".join(f"{key}={error[key]}".split()) for key in ("type", "code", "param", "message") if isinstance(error.get(key), (str, int, float)))
    return _SENSITIVE_ERROR_VALUE_RE.sub(r"\1=[REDACTED]", detail)[:1000]


def _safe_response_event_detail(event: dict[str, Any]) -> str:
    """Return bounded diagnostics for a terminal Responses stream event."""
    response = event.get("response")
    if not isinstance(response, dict):
        response = event

    parts = [f"{key}={response[key]}" for key in ("type", "status") if isinstance(response.get(key), (str, int, float))]

    incomplete_details = response.get("incomplete_details")
    if isinstance(incomplete_details, dict):
        parts.extend(f"incomplete_{key}={incomplete_details[key]}" for key in ("reason",) if isinstance(incomplete_details.get(key), (str, int, float)))

    error = response.get("error")
    if isinstance(error, dict):
        parts.extend(f"{key}={error[key]}" for key in ("type", "code", "param", "message") if isinstance(error.get(key), (str, int, float)))

    detail = " ".join(" ".join(part.split()) for part in parts)
    if not detail:
        detail = str(event.get("type") or "terminal response event")
    return _SENSITIVE_ERROR_VALUE_RE.sub(r"\1=[REDACTED]", detail)[:1000]


def _build_usage_metadata(oai_usage: dict) -> dict:
    """Convert Codex/Responses API usage dict to LangChain usage_metadata format.

    Maps OpenAI Responses API token usage fields to the dict structure that
    LangChain AIMessage.usage_metadata expects. This avoids depending on
    langchain_openai private helpers like ``_create_usage_metadata_responses``.
    """
    input_tokens = oai_usage.get("input_tokens", 0)
    output_tokens = oai_usage.get("output_tokens", 0)
    total_tokens = oai_usage.get("total_tokens", input_tokens + output_tokens)
    metadata: dict = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    input_details = oai_usage.get("input_tokens_details") or {}
    output_details = oai_usage.get("output_tokens_details") or {}
    cache_read = input_details.get("cached_tokens")
    if cache_read is not None:
        metadata["input_token_details"] = {"cache_read": cache_read}
    reasoning = output_details.get("reasoning_tokens")
    if reasoning is not None:
        metadata["output_token_details"] = {"reasoning": reasoning}
    return metadata


MAX_RETRIES = 3
_REPLAYED_TASK_PROMPT_PLACEHOLDER = "[omitted from provider replay: this completed task prompt was {char_count} characters; use the following function_call_output as the task result.]"


class CodexStreamIncompleteError(RuntimeError):
    """Codex SSE ended before the required response.completed event."""


class CodexResponseTerminalError(RuntimeError):
    """Codex SSE ended with a terminal non-completed response event."""


class CodexRetryExhaustedError(RuntimeError):
    """The Codex provider already exhausted its own retry budget."""


class CodexChatModel(BaseChatModel):
    """LangChain chat model using ChatGPT Codex Responses API.

    Config example:
        - name: gpt-5.4
          use: deerflow.models.openai_codex_provider:CodexChatModel
          model: gpt-5.4
          reasoning_effort: medium
    """

    model: str = "gpt-5.4"
    reasoning_effort: str = "medium"
    reasoning_summary: str = "detailed"
    text_verbosity: str = "medium"
    retry_max_attempts: int = MAX_RETRIES
    _access_token: str = ""
    _account_id: str = ""

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def _llm_type(self) -> str:
        return "codex-responses"

    def _validate_retry_config(self) -> None:
        if self.retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be >= 1")

    def model_post_init(self, __context: Any) -> None:
        """Auto-load Codex CLI credentials."""
        self._validate_retry_config()

        cred = self._load_codex_auth()
        if cred:
            self._access_token = cred.access_token
            self._account_id = cred.account_id
            logger.info(f"Using Codex CLI credential (account: {self._account_id[:8]}...)")
        else:
            raise ValueError("Codex CLI credential not found. Expected ~/.codex/auth.json or CODEX_AUTH_PATH.")

        super().model_post_init(__context)

    def _load_codex_auth(self) -> CodexCliCredential | None:
        """Load access_token and account_id from Codex CLI auth."""
        return load_codex_cli_credential()

    def _refresh_codex_auth(self) -> None:
        """Reload Codex CLI credentials so account switches take effect."""
        cred = self._load_codex_auth()
        if not cred:
            raise ValueError("Codex CLI credential not found. Expected ~/.codex/auth.json or CODEX_AUTH_PATH.")

        if cred.access_token == self._access_token and cred.account_id == self._account_id:
            return

        self._access_token = cred.access_token
        self._account_id = cred.account_id
        logger.info(f"Reloaded Codex CLI credential (account: {self._account_id[:8]}...)")

    @classmethod
    def _normalize_content(cls, content: Any) -> str:
        """Flatten LangChain content blocks into plain text for Codex."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = [cls._normalize_content(item) for item in content]
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            for key in ("text", "output"):
                value = content.get(key)
                if isinstance(value, str):
                    return value
            nested_content = content.get("content")
            if nested_content is not None:
                return cls._normalize_content(nested_content)
            try:
                return json.dumps(content, ensure_ascii=False)
            except TypeError:
                return str(content)

        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)

    def _convert_messages(self, messages: list[BaseMessage]) -> tuple[str, list[dict]]:
        """Convert LangChain messages to Responses API format.

        Returns (instructions, input_items).
        """
        instructions_parts: list[str] = []
        input_items = []
        completed_tool_call_ids = {msg.tool_call_id for msg in messages if isinstance(msg, ToolMessage) and isinstance(msg.tool_call_id, str) and msg.tool_call_id}

        for msg in messages:
            if isinstance(msg, SystemMessage):
                content = self._normalize_content(msg.content)
                if content:
                    instructions_parts.append(content)
            elif isinstance(msg, HumanMessage):
                content = self._normalize_content(msg.content)
                input_items.append({"role": "user", "content": content})
            elif isinstance(msg, AIMessage):
                if msg.content:
                    content = self._normalize_content(msg.content)
                    input_items.append({"role": "assistant", "content": content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        input_items.append(
                            {
                                "type": "function_call",
                                "name": tc["name"],
                                "arguments": self._serialize_tool_call_arguments(tc, completed_tool_call_ids),
                                "call_id": tc["id"],
                            }
                        )
            elif isinstance(msg, ToolMessage):
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.tool_call_id,
                        "output": self._normalize_content(msg.content),
                    }
                )

        instructions = "\n\n".join(instructions_parts) or "You are a helpful assistant."

        return instructions, input_items

    @classmethod
    def _serialize_tool_call_arguments(cls, tool_call: dict[str, Any], completed_tool_call_ids: set[str]) -> str:
        args = tool_call["args"]
        if not isinstance(args, dict):
            return args

        compacted_args = cls._compact_replayed_task_arguments(tool_call, args, completed_tool_call_ids)
        return json.dumps(compacted_args)

    @classmethod
    def _compact_replayed_task_arguments(
        cls,
        tool_call: dict[str, Any],
        args: dict[str, Any],
        completed_tool_call_ids: set[str],
    ) -> dict[str, Any]:
        """Omit completed task prompts from provider replay payloads.

        The Responses API requires replaying prior function_call items before
        their function_call_output items. For completed DeerFlow ``task`` calls,
        the long one-shot prompt is historical transport data; the model should
        reason from the tool output plus the short description on later turns.
        Keep pending tool calls untouched so execution still receives the exact
        prompt the model authored.
        """
        if tool_call.get("name") != "task" or tool_call.get("id") not in completed_tool_call_ids:
            return args

        prompt = args.get("prompt")
        if not isinstance(prompt, str):
            return args

        compacted = dict(args)
        compacted["prompt"] = _REPLAYED_TASK_PROMPT_PLACEHOLDER.format(char_count=len(prompt))
        return compacted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert LangChain tool format to Responses API format."""
        responses_tools = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                responses_tools.append(
                    {
                        "type": "function",
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    }
                )
            elif "name" in tool:
                responses_tools.append(
                    {
                        "type": "function",
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    }
                )
        return responses_tools

    def _call_codex_api(self, messages: list[BaseMessage], tools: list[dict] | None = None) -> dict:
        """Call the Codex Responses API and return the completed response."""
        self._refresh_codex_auth()
        instructions, input_items = self._convert_messages(messages)
        reasoning_effort = "xhigh" if self.reasoning_effort == "ultra" else self.reasoning_effort

        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "store": False,
            "stream": True,
            "reasoning": {"effort": reasoning_effort, "summary": self.reasoning_summary} if reasoning_effort != "none" else {"effort": "none"},
            "text": {"verbosity": self.text_verbosity},
        }

        if tools:
            payload["tools"] = self._convert_tools(tools)

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "ChatGPT-Account-ID": self._account_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "originator": "codex_cli_rs",
        }

        last_error = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                return self._stream_response(headers, payload)
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in (429, 500, 529):
                    if attempt >= self.retry_max_attempts:
                        raise CodexRetryExhaustedError(f"Codex provider retry budget exhausted after {attempt} attempts") from e
                    wait_ms = 2000 * (1 << (attempt - 1))
                    logger.warning(f"Codex API error {e.response.status_code}, retrying {attempt}/{self.retry_max_attempts} after {wait_ms}ms")
                    time.sleep(wait_ms / 1000)
                else:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError, CodexStreamIncompleteError) as e:
                last_error = e
                if attempt >= self.retry_max_attempts:
                    raise CodexRetryExhaustedError(f"Codex provider retry budget exhausted after {attempt} attempts") from e
                wait_ms = 1000 * (1 << (attempt - 1))
                logger.warning(f"Codex API connection/stream error, retrying {attempt}/{self.retry_max_attempts} after {wait_ms}ms: {e}")
                time.sleep(wait_ms / 1000)
            except Exception:
                raise

        raise last_error

    def _stream_response(self, headers: dict, payload: dict) -> dict:
        """Stream SSE from Codex API and collect the final response."""
        completed_response = None
        terminal_error_detail = None
        streamed_output_items: dict[int, dict[str, Any]] = {}

        with httpx.Client(timeout=300) as client:
            with client.stream("POST", f"{CODEX_BASE_URL}/responses", headers=headers, json=payload) as resp:
                if getattr(resp, "status_code", 200) >= 400:
                    resp.read()
                    logger.error(
                        "Codex API request failed: status=%s request_id=%s detail=%s",
                        resp.status_code,
                        resp.headers.get("x-request-id", "unknown"),
                        _safe_error_detail(resp),
                    )
                resp.raise_for_status()
                for line in resp.iter_lines():
                    data = self._parse_sse_data_line(line)
                    if not data:
                        continue

                    event_type = data.get("type")
                    if event_type == "response.output_item.done":
                        output_index = data.get("output_index")
                        output_item = data.get("item")
                        if isinstance(output_index, int) and isinstance(output_item, dict):
                            streamed_output_items[output_index] = output_item
                    elif event_type == "response.completed":
                        completed_response = data["response"]
                    elif event_type in _TERMINAL_RESPONSE_EVENTS:
                        terminal_error_detail = _safe_response_event_detail(data)
                        logger.warning(
                            "Codex API stream ended with terminal event: request_id=%s detail=%s",
                            getattr(resp, "headers", {}).get("x-request-id", "unknown"),
                            terminal_error_detail,
                        )

        if not completed_response:
            if terminal_error_detail:
                raise CodexResponseTerminalError(terminal_error_detail)
            raise CodexStreamIncompleteError("Codex API stream ended without response.completed event")

        # ChatGPT Codex can emit the final assistant content only in stream events.
        # When response.completed arrives, response.output may still be empty.
        if streamed_output_items:
            merged_output = []
            response_output = completed_response.get("output")
            if isinstance(response_output, list):
                merged_output = list(response_output)

            max_index = max(max(streamed_output_items), len(merged_output) - 1)
            if max_index >= 0 and len(merged_output) <= max_index:
                merged_output.extend([None] * (max_index + 1 - len(merged_output)))

            for output_index, output_item in streamed_output_items.items():
                existing_item = merged_output[output_index]
                if not isinstance(existing_item, dict):
                    merged_output[output_index] = output_item

            completed_response = dict(completed_response)
            completed_response["output"] = [item for item in merged_output if isinstance(item, dict)]

        return completed_response

    @staticmethod
    def _parse_sse_data_line(line: str) -> dict[str, Any] | None:
        """Parse a data line from the SSE stream, skipping terminal markers."""
        if not line.startswith("data:"):
            return None

        raw_data = line[5:].strip()
        if not raw_data or raw_data == "[DONE]":
            return None

        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.debug(f"Skipping non-JSON Codex SSE frame: {raw_data}")
            return None

        return data if isinstance(data, dict) else None

    def _parse_tool_call_arguments(self, output_item: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Parse function-call arguments, surfacing malformed payloads safely."""
        raw_arguments = output_item.get("arguments", "{}")
        if isinstance(raw_arguments, dict):
            return raw_arguments, None

        normalized_arguments = raw_arguments or "{}"
        try:
            parsed_arguments = json.loads(normalized_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            return None, {
                "type": "invalid_tool_call",
                "name": output_item.get("name"),
                "args": str(raw_arguments),
                "id": output_item.get("call_id"),
                "error": f"Failed to parse tool arguments: {exc}",
            }

        if not isinstance(parsed_arguments, dict):
            return None, {
                "type": "invalid_tool_call",
                "name": output_item.get("name"),
                "args": str(raw_arguments),
                "id": output_item.get("call_id"),
                "error": "Tool arguments must decode to a JSON object.",
            }

        return parsed_arguments, None

    def _parse_response(self, response: dict) -> ChatResult:
        """Parse Codex Responses API response into LangChain ChatResult."""
        content = ""
        tool_calls = []
        invalid_tool_calls = []
        reasoning_content = ""

        for output_item in response.get("output", []):
            if output_item.get("type") == "reasoning":
                # Extract reasoning summary text
                for summary_item in output_item.get("summary", []):
                    if isinstance(summary_item, dict) and summary_item.get("type") == "summary_text":
                        reasoning_content += summary_item.get("text", "")
                    elif isinstance(summary_item, str):
                        reasoning_content += summary_item
            elif output_item.get("type") == "message":
                for part in output_item.get("content", []):
                    if part.get("type") == "output_text":
                        content += part.get("text", "")
            elif output_item.get("type") == "function_call":
                parsed_arguments, invalid_tool_call = self._parse_tool_call_arguments(output_item)
                if invalid_tool_call:
                    invalid_tool_calls.append(invalid_tool_call)
                    continue

                tool_calls.append(
                    {
                        "name": output_item["name"],
                        "args": parsed_arguments or {},
                        "id": output_item.get("call_id", ""),
                        "type": "tool_call",
                    }
                )

        usage = response.get("usage", {})
        usage_metadata = _build_usage_metadata(usage) if usage else None
        additional_kwargs = {}
        if reasoning_content:
            additional_kwargs["reasoning_content"] = reasoning_content

        message = AIMessage(
            content=content,
            tool_calls=tool_calls if tool_calls else [],
            invalid_tool_calls=invalid_tool_calls,
            additional_kwargs=additional_kwargs,
            usage_metadata=usage_metadata,
            response_metadata={
                "model": response.get("model", self.model),
                "usage": usage,
            },
        )

        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={
                "token_usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "model_name": response.get("model", self.model),
            },
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a response using Codex Responses API."""
        tools = kwargs.get("tools", None)
        response = self._call_codex_api(messages, tools=tools)
        return self._parse_response(response)

    def bind_tools(self, tools: list, **kwargs: Any) -> Any:
        """Bind tools for function calling."""
        from langchain_core.runnables import RunnableBinding
        from langchain_core.tools import BaseTool
        from langchain_core.utils.function_calling import convert_to_openai_function

        formatted_tools = []
        for tool in tools:
            if isinstance(tool, BaseTool):
                try:
                    fn = convert_to_openai_function(tool)
                    formatted_tools.append(
                        {
                            "type": "function",
                            "name": fn["name"],
                            "description": fn.get("description", ""),
                            "parameters": fn.get("parameters", {}),
                        }
                    )
                except Exception:
                    formatted_tools.append(
                        {
                            "type": "function",
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": {"type": "object", "properties": {}},
                        }
                    )
            elif isinstance(tool, dict):
                if "function" in tool:
                    fn = tool["function"]
                    formatted_tools.append(
                        {
                            "type": "function",
                            "name": fn["name"],
                            "description": fn.get("description", ""),
                            "parameters": fn.get("parameters", {}),
                        }
                    )
                else:
                    formatted_tools.append(tool)

        return RunnableBinding(bound=self, kwargs={"tools": formatted_tools}, **kwargs)
