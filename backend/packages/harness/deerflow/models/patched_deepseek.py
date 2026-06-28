"""Patched ChatDeepSeek that preserves reasoning_content in multi-turn conversations.

This module provides a patched version of ChatDeepSeek that properly handles
reasoning_content when sending messages back to the API. The original implementation
stores reasoning_content in additional_kwargs but doesn't include it when making
subsequent API calls, which causes errors with APIs that require reasoning_content
on all assistant messages when thinking mode is enabled.
"""

import logging
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import openai
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_deepseek import ChatDeepSeek
from pydantic import Field, PrivateAttr, SecretStr, model_validator

from deerflow.models.assistant_payload_replay import restore_assistant_payloads, restore_reasoning_content

logger = logging.getLogger(__name__)

_KEY_SPLIT_RE = re.compile(r"[\s,;]+")
_ROTATION_STATUS_CODES = {401, 403, 408, 409, 425, 429, 500, 502, 503, 504, 529}


def _parse_api_keys(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_keys = _KEY_SPLIT_RE.split(value.strip())
    elif isinstance(value, (list, tuple)):
        raw_keys = []
        for item in value:
            raw_keys.extend(_parse_api_keys(item))
    else:
        raw_keys = [str(value)]

    keys: list[str] = []
    for key in raw_keys:
        key = key.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value).strip()


def _rotation_status_code(exc: BaseException) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


class PatchedChatDeepSeek(ChatDeepSeek):
    """ChatDeepSeek with proper reasoning_content preservation.

    When using thinking/reasoning enabled models, the API expects reasoning_content
    to be present on ALL assistant messages in multi-turn conversations. This patched
    version ensures reasoning_content from additional_kwargs is included in the
    request payload.

    Optional ``api_keys`` enables a small credential pool. Configure it as a list
    or as a comma/newline separated env value; the model starts with the first key
    and rotates to the next key when the provider returns rate-limit/auth/server
    status errors.
    """

    api_keys: list[str] | str | None = Field(default=None, repr=False)

    _api_key_values: list[str] = PrivateAttr(default_factory=list)
    _api_key_index: int = PrivateAttr(default=0)

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {
            "api_key": "DEEPSEEK_API_KEY",
            "api_keys": "DEEPSEEK_API_KEYS",
            "openai_api_key": "DEEPSEEK_API_KEY",
        }

    @model_validator(mode="before")
    @classmethod
    def _seed_api_key_from_pool(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("api_key"):
            keys = _parse_api_keys(data.get("api_keys"))
            if keys:
                data = dict(data)
                data["api_key"] = keys[0]
        return data

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        keys = _parse_api_keys(self.api_keys)
        current_key = _secret_value(self.api_key)
        if current_key:
            keys = _parse_api_keys([current_key, *keys])
        self._api_key_values = keys
        self._api_key_index = 0

    def _client_params(self, api_key: str) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "api_key": api_key,
                "base_url": self.api_base,
                "timeout": self.request_timeout,
                "max_retries": self.max_retries,
                "default_headers": self.default_headers,
                "default_query": self.default_query,
            }.items()
            if v is not None
        }

    def _activate_api_key(self, index: int) -> None:
        self._api_key_index = index
        api_key = self._api_key_values[index]
        self.api_key = SecretStr(api_key)
        self.root_client = openai.OpenAI(**self._client_params(api_key), http_client=self.http_client)
        self.client = self.root_client.chat.completions
        self.root_async_client = openai.AsyncOpenAI(**self._client_params(api_key), http_client=self.http_async_client)
        self.async_client = self.root_async_client.chat.completions

    def _rotate_api_key(self, attempted: set[int], exc: BaseException) -> bool:
        if len(self._api_key_values) <= 1:
            return False

        for offset in range(1, len(self._api_key_values) + 1):
            next_index = (self._api_key_index + offset) % len(self._api_key_values)
            if next_index in attempted:
                continue
            attempted.add(next_index)
            self._activate_api_key(next_index)
            logger.warning(
                "DeepSeek credential rotated to pool index %s/%s after %s",
                next_index + 1,
                len(self._api_key_values),
                type(exc).__name__,
            )
            return True
        return False

    def _should_rotate(self, exc: BaseException) -> bool:
        status_code = _rotation_status_code(exc)
        if status_code in _ROTATION_STATUS_CODES:
            return True
        return isinstance(exc, (openai.RateLimitError, openai.AuthenticationError, openai.PermissionDeniedError))

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with reasoning_content preserved.

        Overrides the parent method to inject reasoning_content from
        additional_kwargs into assistant messages in the payload.
        """
        # Get the original messages before conversion
        original_messages = self._convert_input(input_).to_messages()

        # Call parent to get the base payload
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        restore_assistant_payloads(
            payload.get("messages", []),
            original_messages,
            restore_reasoning_content,
        )

        return payload

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        attempted = {self._api_key_index}
        while True:
            try:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:
                if not self._should_rotate(exc) or not self._rotate_api_key(attempted, exc):
                    raise

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        attempted = {self._api_key_index}
        while True:
            try:
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:
                if not self._should_rotate(exc) or not self._rotate_api_key(attempted, exc):
                    raise

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        attempted = {self._api_key_index}
        while True:
            yielded = False
            try:
                for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yielded = True
                    yield chunk
                return
            except Exception as exc:
                if yielded or not self._should_rotate(exc) or not self._rotate_api_key(attempted, exc):
                    raise

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        attempted = {self._api_key_index}
        while True:
            yielded = False
            try:
                async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yielded = True
                    yield chunk
                return
            except Exception as exc:
                if yielded or not self._should_rotate(exc) or not self._rotate_api_key(attempted, exc):
                    raise
