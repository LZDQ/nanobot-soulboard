"""Provider factory helpers for soulboard."""

from __future__ import annotations

from nanobot.config.schema import Config
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.providers.azure_openai_provider import AzureOpenAIProvider
from nanobot.providers.openai_codex_provider import OpenAICodexProvider


def make_provider(config: Config) -> LLMProvider:
    """Create a provider using the same selection rules as upstream nanobot."""
    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    provider_config = config.get_provider(model)

    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        provider: LLMProvider = OpenAICodexProvider(default_model=model)
    elif provider_name == "custom":
        from nanobot.providers.custom_provider import CustomProvider

        provider = CustomProvider(
            api_key=provider_config.api_key if provider_config else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
            extra_headers=provider_config.extra_headers if provider_config else None,
        )
    elif provider_name == "azure_openai":
        if not provider_config or not provider_config.api_key or not provider_config.api_base:
            raise ValueError("Azure OpenAI requires both api_key and api_base in the base nanobot config")
        provider = AzureOpenAIProvider(
            api_key=provider_config.api_key,
            api_base=provider_config.api_base,
            default_model=model,
        )
    else:
        from nanobot.providers.litellm_provider import LiteLLMProvider
        from nanobot.providers.registry import find_by_name

        spec = find_by_name(provider_name)
        if (
            not model.startswith("bedrock/")
            and not (provider_config and provider_config.api_key)
            and not (spec and (spec.is_oauth or spec.is_local))
        ):
            raise ValueError("No API key configured for the selected model/provider")
        provider = LiteLLMProvider(
            api_key=provider_config.api_key if provider_config else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=provider_config.extra_headers if provider_config else None,
            provider_name=provider_name,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider
