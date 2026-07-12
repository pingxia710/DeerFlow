from typing import cast

from deerflow.config.model_config import ModelConfig, ReasoningEffort

_LEGACY_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})


def resolve_reasoning_effort(
    model_config: ModelConfig,
    requested_effort: str | None,
    *,
    thinking_enabled: bool,
) -> ReasoningEffort | None:
    if not model_config.supports_reasoning_effort:
        return None
    if not thinking_enabled:
        return "none"
    if model_config.reasoning_efforts:
        if requested_effort in model_config.reasoning_efforts:
            return cast(ReasoningEffort, requested_effort)
        return model_config.default_reasoning_effort
    if requested_effort in _LEGACY_REASONING_EFFORTS:
        return cast(ReasoningEffort, requested_effort)
    return None
