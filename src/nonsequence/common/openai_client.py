"""Helpers for OpenAI-compatible chat-completions APIs."""

from __future__ import annotations

import os
from typing import Any


def create_client(*, api_key_env: str, base_url: str) -> Any:
    """Create an OpenAI-compatible client using a key from the environment."""
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {api_key_env} is required")
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "The 'openai' package is required only when making API calls"
        ) from error
    return OpenAI(api_key=api_key, base_url=base_url)


def api_error_types() -> tuple[type[Exception], ...]:
    """Return retryable OpenAI SDK exception types using a delayed import."""
    try:
        from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError
    except ImportError as error:
        raise RuntimeError(
            "The 'openai' package is required only when making API calls"
        ) from error
    return APIConnectionError, APIError, APITimeoutError, RateLimitError
