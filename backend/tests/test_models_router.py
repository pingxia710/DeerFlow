import asyncio

from app.gateway.routers.models import get_model, list_models
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig


def test_models_api_exposes_configured_reasoning_capabilities():
    config = AppConfig(
        models=[
            ModelConfig(
                name="terra",
                display_name="GPT-5.6 Terra",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="gpt-5.6-terra",
                supports_thinking=True,
                supports_reasoning_effort=True,
                reasoning_efforts=["medium", "high", "xhigh", "max"],
                default_reasoning_effort="max",
            )
        ],
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )

    listed = asyncio.run(list_models(config))
    detail = asyncio.run(get_model("terra", config))

    assert listed.models[0].reasoning_efforts == ["medium", "high", "xhigh", "max"]
    assert listed.models[0].default_reasoning_effort == "max"
    assert detail.reasoning_efforts == ["medium", "high", "xhigh", "max"]
    assert detail.default_reasoning_effort == "max"
