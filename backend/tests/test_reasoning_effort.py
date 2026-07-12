from types import SimpleNamespace

from deerflow.config.model_config import ModelConfig
from deerflow.models.reasoning_effort import resolve_reasoning_effort


def _model() -> ModelConfig:
    return ModelConfig(
        name="terra",
        display_name="Terra",
        description=None,
        use="deerflow.models.openai_codex_provider:CodexChatModel",
        model="gpt-5.6-terra",
        supports_thinking=True,
        supports_reasoning_effort=True,
        reasoning_efforts=["medium", "high", "xhigh", "max"],
        default_reasoning_effort="max",
    )


def test_resolve_reasoning_effort_uses_the_model_default_for_missing_or_stale_values():
    model = _model()

    assert resolve_reasoning_effort(model, None, thinking_enabled=True) == "max"
    assert resolve_reasoning_effort(model, "ultra", thinking_enabled=True) == "max"
    assert resolve_reasoning_effort(model, "low", thinking_enabled=True) == "max"


def test_resolve_reasoning_effort_keeps_a_valid_explicit_value():
    assert resolve_reasoning_effort(_model(), "high", thinking_enabled=True) == "high"


def test_resolve_reasoning_effort_disables_provider_reasoning_when_thinking_is_disabled():
    assert resolve_reasoning_effort(_model(), "max", thinking_enabled=False) == "none"


def test_resolve_reasoning_effort_treats_legacy_model_config_without_capability_as_unsupported():
    legacy_model = SimpleNamespace(supports_thinking=False)

    assert resolve_reasoning_effort(legacy_model, None, thinking_enabled=True) is None
