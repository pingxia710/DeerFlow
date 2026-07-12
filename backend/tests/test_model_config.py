import pytest

from deerflow.config.model_config import ModelConfig


def _make_model(**overrides) -> ModelConfig:
    return ModelConfig(
        name="openai-responses",
        display_name="OpenAI Responses",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="gpt-5",
        **overrides,
    )


def test_responses_api_fields_are_declared_in_model_schema():
    assert "provider" in ModelConfig.model_fields
    assert "use_responses_api" in ModelConfig.model_fields
    assert "output_version" in ModelConfig.model_fields


def test_responses_api_fields_round_trip_in_model_dump():
    config = _make_model(
        provider="OpenAI",
        api_key="$OPENAI_API_KEY",
        use_responses_api=True,
        output_version="responses/v1",
    )

    dumped = config.model_dump(exclude_none=True)

    assert dumped["provider"] == "OpenAI"
    assert dumped["use_responses_api"] is True
    assert dumped["output_version"] == "responses/v1"


def test_reasoning_capability_fields_round_trip_in_model_dump():
    config = _make_model(
        supports_reasoning_effort=True,
        reasoning_efforts=["medium", "high", "xhigh", "max"],
        default_reasoning_effort="max",
    )

    dumped = config.model_dump(exclude_none=True)

    assert dumped["reasoning_efforts"] == ["medium", "high", "xhigh", "max"]
    assert dumped["default_reasoning_effort"] == "max"


def test_reasoning_capability_default_must_be_selectable():
    with pytest.raises(ValueError, match="default_reasoning_effort"):
        _make_model(
            supports_reasoning_effort=True,
            reasoning_efforts=["medium", "high", "xhigh"],
            default_reasoning_effort="max",
        )


def test_reasoning_capability_requires_a_default_when_options_are_configured():
    with pytest.raises(ValueError, match="default_reasoning_effort is required"):
        _make_model(
            supports_reasoning_effort=True,
            reasoning_efforts=["medium", "high", "xhigh"],
        )
