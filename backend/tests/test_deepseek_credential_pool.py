from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_deepseek import ChatDeepSeek

from deerflow.models.patched_deepseek import PatchedChatDeepSeek, _parse_api_keys


class _StatusError(Exception):
    status_code = 429


def test_parse_api_keys_splits_common_pool_formats():
    assert _parse_api_keys(" sk-a,sk-b\nsk-c ; sk-a ") == ["sk-a", "sk-b", "sk-c"]


def test_deepseek_pool_rotates_after_status_error(monkeypatch):
    calls: list[str] = []

    def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append(self.api_key.get_secret_value())
        if len(calls) == 1:
            raise _StatusError("rate limited")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    monkeypatch.setattr(ChatDeepSeek, "_generate", fake_generate)

    model = PatchedChatDeepSeek(
        model="deepseek-reasoner",
        api_keys=["sk-first", "sk-second"],
        max_retries=0,
    )

    result = model._generate([])

    assert result.generations[0].message.content == "ok"
    assert calls == ["sk-first", "sk-second"]
