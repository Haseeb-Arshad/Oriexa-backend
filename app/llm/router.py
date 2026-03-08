"""LLM router — multi-provider model access (OpenRouter, Anthropic, Moonshot).

Model ID format: "provider/model-name"
  - openrouter/...  → OpenRouter API (free and paid models)
  - anthropic/...   → Direct Anthropic API
  - moonshot/...    → Moonshot/Kimi API

Examples:
  - openrouter/arcee-ai/trinity-large-preview:free
  - openrouter/stepfun/step-3.5-flash:free
  - anthropic/claude-opus-4-5-20250514
  - moonshot/kimi-k2.5-thinking
"""

from __future__ import annotations

import enum
import logging

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from app.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"

_model_cache: dict[str, ChatOpenAI] = {}


class ModelTier(str, enum.Enum):
    FAST = "fast"
    DEFAULT = "default"
    STRONG = "strong"
    THINKING = "thinking"
    # Frontend coding pipeline tiers
    CODING = "coding"              # primary execution: z-ai/glm-5
    CODING_STRONG = "coding_strong"    # complex tasks:   openai/gpt-5.3-codex
    CODING_PLANNING = "coding_planning"  # planning stage:  anthropic/claude-sonnet-4.6


def _get_model_config(tier: ModelTier) -> str:
    """Return the full model ID string (provider/model) for a tier."""
    mapping = {
        ModelTier.FAST: settings.FAST_MODEL,
        ModelTier.DEFAULT: settings.DEFAULT_MODEL,
        ModelTier.STRONG: settings.STRONG_MODEL,
        ModelTier.THINKING: settings.THINKING_MODEL,
        ModelTier.CODING: settings.CODING_MODEL,
        ModelTier.CODING_STRONG: settings.CODING_STRONG_MODEL,
        ModelTier.CODING_PLANNING: settings.CODING_PLANNING_MODEL,
    }
    return mapping[tier]


def _parse_provider(model_config: str) -> tuple[str, str]:
    """Parse 'provider/model-id' into (provider, model_id).

    For OpenRouter models with nested slashes like
    'openrouter/arcee-ai/trinity-large-preview:free',
    the provider is 'openrouter' and the model_id is everything after.
    """
    if model_config.startswith("openrouter/"):
        return "openrouter", model_config[len("openrouter/"):]
    elif model_config.startswith("anthropic/"):
        return "anthropic", model_config[len("anthropic/"):]
    elif model_config.startswith("moonshot/"):
        return "moonshot", model_config[len("moonshot/"):]
    else:
        # Default to OpenRouter for backward compatibility
        return "openrouter", model_config


def _build_model(
    provider: str,
    model_id: str,
    temperature: float,
    max_tokens: int,
) -> ChatOpenAI:
    """Build a ChatOpenAI instance for the given provider."""

    if provider == "openrouter":
        return ChatOpenAI(
            model=model_id,
            openai_api_key=settings.OPENROUTER_API_KEY,
            openai_api_base=OPENROUTER_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
            model_kwargs={
                "extra_headers": {
                    "HTTP-Referer": "https://oriexa.dev",
                    "X-Title": "Oriexa Orchestrator",
                },
            },
        )

    elif provider == "anthropic":
        # Use langchain-openai with Anthropic's OpenAI-compatible endpoint
        # Or use langchain-anthropic if available — here we use the OpenAI compat layer
        return ChatOpenAI(
            model=model_id,
            openai_api_key=settings.ANTHROPIC_API_KEY,
            openai_api_base=ANTHROPIC_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
            model_kwargs={
                "extra_headers": {
                    "anthropic-version": "2023-06-01",
                },
            },
        )

    elif provider == "moonshot":
        return ChatOpenAI(
            model=model_id,
            openai_api_key=settings.MOONSHOT_API_KEY,
            openai_api_base=MOONSHOT_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def get_model(
    tier: ModelTier | str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> ChatOpenAI:
    """Return a ChatOpenAI instance for the given tier.

    Supports multiple providers based on the model ID prefix:
    - openrouter/... → OpenRouter (free models like arcee-ai, stepfun)
    - anthropic/...  → Direct Anthropic API (opus-4.5)
    - moonshot/...   → Moonshot Kimi API (kimi-k2-thinking)
    """
    if isinstance(tier, str):
        tier = ModelTier(tier)

    model_config = _get_model_config(tier)
    provider, model_id = _parse_provider(model_config)
    cache_key = f"{provider}:{model_id}:{temperature}:{max_tokens}"

    if cache_key not in _model_cache:
        _model_cache[cache_key] = _build_model(provider, model_id, temperature, max_tokens)
        logger.info(
            "Created model: tier=%s provider=%s model=%s",
            tier.value, provider, model_id,
        )

    return _model_cache[cache_key]


def get_model_by_id(
    model_config: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> ChatOpenAI:
    """Return a ChatOpenAI instance for an explicit model ID string.

    Useful when agents need a specific model outside the tier system.
    """
    provider, model_id = _parse_provider(model_config)
    cache_key = f"{provider}:{model_id}:{temperature}:{max_tokens}"

    if cache_key not in _model_cache:
        _model_cache[cache_key] = _build_model(provider, model_id, temperature, max_tokens)
        logger.info("Created model by ID: provider=%s model=%s", provider, model_id)

    return _model_cache[cache_key]


def get_model_with_fallback(
    tier: ModelTier | str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> BaseChatModel:
    """Return a model with automatic fallback to other tiers.

    General tiers:
      THINKING  → STRONG → DEFAULT
      STRONG    → DEFAULT → FAST
      DEFAULT   → FAST

    Frontend coding tiers:
      CODING          → minimax-m2.5 → gemini-3-flash → CODING_STRONG → DEFAULT
      CODING_STRONG   → STRONG
      CODING_PLANNING → STRONG → DEFAULT
    """
    if isinstance(tier, str):
        tier = ModelTier(tier)

    primary = get_model(tier, temperature, max_tokens)

    # Define fallback sequence based on tier
    fallbacks: list[BaseChatModel] = []
    if tier == ModelTier.THINKING:
        fallbacks = [
            get_model(ModelTier.STRONG, temperature, max_tokens),
            get_model(ModelTier.DEFAULT, temperature, max_tokens),
        ]
    elif tier == ModelTier.STRONG:
        fallbacks = [
            get_model(ModelTier.DEFAULT, temperature, max_tokens),
            get_model(ModelTier.FAST, 0, 1024),
        ]
    elif tier == ModelTier.DEFAULT:
        fallbacks = [
            get_model(ModelTier.FAST, 0, 1024),
        ]
    elif tier == ModelTier.CODING:
        # Prioritize glm-5; fall through alt models then escalate to gpt-5.3-codex
        fallbacks = [
            get_model_by_id(settings.CODING_ALT_MODEL_1, temperature, max_tokens),
            get_model_by_id(settings.CODING_ALT_MODEL_2, temperature, max_tokens),
            get_model(ModelTier.CODING_STRONG, temperature, max_tokens),
            get_model(ModelTier.DEFAULT, temperature, max_tokens),
        ]
    elif tier == ModelTier.CODING_STRONG:
        # gpt-5.3-codex → fall back to the first alt OpenRouter model
        fallbacks = [
            get_model_by_id(settings.CODING_ALT_MODEL_1, temperature, max_tokens),
        ]
    elif tier == ModelTier.CODING_PLANNING:
        # claude-opus-4-6 → fall back to gpt-5.3-codex
        fallbacks = [
            get_model(ModelTier.CODING_STRONG, temperature, max_tokens),
        ]

    if not fallbacks:
        return primary

    return primary.with_fallbacks(fallbacks)
