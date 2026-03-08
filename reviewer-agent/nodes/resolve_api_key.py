"""
Node: resolve_api_key

Determines which LLM API key to use for the review:
  1. Poster's key (if under max_reviews limit)
  2. Freelancer's key (fallback)
  3. Platform default key (env var fallback for testing)
  4. None — skip automated review

Calls GET /api/v1/tasks/:id/review-config to get the resolved key.
"""

from __future__ import annotations
import os
import re
import httpx
from state import ReviewerState


def _is_temp_anthropic_mode() -> bool:
    return os.environ.get("AGENTIC_TEMP_USE_ANTHROPIC_OPUS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _get_temp_anthropic_model() -> str:
    model = os.environ.get("AGENTIC_TEMP_ANTHROPIC_MODEL", "claude-opus-4-6")
    if model.startswith("anthropic/"):
        model = model[len("anthropic/"):]
    return re.sub(r"^claude-(opus|sonnet|haiku)-(\d+)\.(\d+)$", r"claude-\1-\2-\3", model)


def resolve_api_key(state: ReviewerState) -> dict:
    """Resolve which LLM API key to use for this review."""
    if state.get("error") and state.get("skip_review"):
        return {}

    if state.get("error"):
        return {}

    task_id = state["task_id"]
    base_url = os.environ["ORIEXA_BASE_URL"]
    api_key = os.environ["ORIEXA_REVIEWER_API_KEY"]

    try:
        resp = httpx.get(
            f"{base_url}/api/v1/tasks/{task_id}/review-config",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        # Fall back to platform default key
        default_key = _get_default_key()
        if default_key:
            return {
                "llm_api_key": default_key["key"],
                "llm_provider": default_key["provider"],
                "llm_model": default_key["model"],
                "key_source": "none",
                "poster_reviews_used": 0,
                "poster_max_reviews": None,
            }
        return {
            "error": f"Failed to fetch review config and no default key available: {exc}",
            "skip_review": True,
        }

    if not data.get("ok"):
        default_key = _get_default_key()
        if default_key:
            return {
                "llm_api_key": default_key["key"],
                "llm_provider": default_key["provider"],
                "llm_model": default_key["model"],
                "key_source": "none",
                "poster_reviews_used": 0,
                "poster_max_reviews": None,
            }
        return {"skip_review": True, "key_source": "none"}

    config = data["data"]
    resolved_key = config.get("resolved_key")
    key_source = config.get("key_source", "none")

    # If API resolved a key (poster or freelancer), use it
    if resolved_key:
        provider = config.get("resolved_provider") or "openrouter"
        model = _model_for_provider(provider)
        return {
            "llm_api_key": resolved_key,
            "llm_provider": provider,
            "llm_model": model,
            "key_source": key_source,
            "poster_reviews_used": config.get("poster_reviews_used", 0),
            "poster_max_reviews": config.get("poster_max_reviews"),
        }

    # No key from API — try platform default
    default_key = _get_default_key()
    if default_key:
        return {
            "llm_api_key": default_key["key"],
            "llm_provider": default_key["provider"],
            "llm_model": default_key["model"],
            "key_source": "none",
            "poster_reviews_used": config.get("poster_reviews_used", 0),
            "poster_max_reviews": config.get("poster_max_reviews"),
        }

    # No key available — skip automated review
    print(f"  [resolve_api_key] No LLM key available for task {task_id} — skipping automated review")
    return {
        "skip_review": True,
        "key_source": "none",
        "poster_reviews_used": config.get("poster_reviews_used", 0),
        "poster_max_reviews": config.get("poster_max_reviews"),
    }


def _get_default_key() -> dict | None:
    """Get the platform default LLM key from environment variables."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_KEY")
    if _is_temp_anthropic_mode() and anthropic_key:
        return {"key": anthropic_key, "provider": "anthropic", "model": _get_temp_anthropic_model()}

    if key := os.environ.get("OPENROUTER_API_KEY"):
        model = os.environ.get("DEFAULT_LLM_MODEL", "anthropic/claude-sonnet-4-6")
        return {"key": key, "provider": "openrouter", "model": model}
    if key := anthropic_key:
        model = os.environ.get("DEFAULT_LLM_MODEL", "claude-sonnet-4-6")
        return {"key": key, "provider": "anthropic", "model": model}
    if key := os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("DEFAULT_LLM_MODEL", "gpt-4o-mini")
        return {"key": key, "provider": "openai", "model": model}
    return None


def _model_for_provider(provider: str) -> str:
    """Return the default model for a given provider."""
    anthropic_default = _get_temp_anthropic_model() if _is_temp_anthropic_mode() else "claude-sonnet-4-6"
    defaults = {
        "openrouter": os.environ.get("DEFAULT_LLM_MODEL", "anthropic/claude-sonnet-4-6"),
        "openai": "gpt-4o-mini",
        "anthropic": anthropic_default,
    }
    return defaults.get(provider, anthropic_default)
