"""Shared utilities for the Loop Termination Judgment data pipeline."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "ltj"
DEFAULT_INTERIM_CTRLSCRIPT_DIR = PROJECT_ROOT / "data" / "interim" / "ctrlscript"
DEFAULT_CHECKPOINT_DIR = DEFAULT_INTERIM_CTRLSCRIPT_DIR / "ltj_checkpoints"
DEFAULT_PROMPT_DIR = PROJECT_ROOT / "prompts" / "ltj"
DEFAULT_FILTER_PROMPT_DIR = DEFAULT_PROMPT_DIR / "filter"
DEFAULT_GENERATION_PROMPT_DIR = DEFAULT_PROMPT_DIR / "generation"
DEFAULT_JUDGE_PROMPT_DIR = DEFAULT_PROMPT_DIR / "judge"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results" / "ltj"

JsonObject = dict[str, Any]
Messages = Sequence[Mapping[str, str]]


def configure_logging(verbose: bool = False) -> None:
    """Configure consistent command-line logging."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def load_json(path: Path) -> Any:
    """Load UTF-8 JSON from a path."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"JSON input does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def save_json(path: Path, value: Any) -> None:
    """Atomically save a JSON-compatible value as UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def load_text(path: Path) -> str:
    """Load a UTF-8 text file."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Prompt file does not exist: {path}") from exc


def require_api_key(environment_variable: str) -> str:
    """Return an API key or raise an actionable error."""
    api_key = os.environ.get(environment_variable)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {environment_variable} must be set before API use."
        )
    return api_key


def create_client(api_key: str, base_url: str) -> Any:
    """Create an OpenAI-compatible client without import-time side effects."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The 'openai' package is required for API commands.") from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def call_chat(
    client: Any,
    *,
    model: str,
    messages: Messages,
    max_retries: int,
    retry_delay: float,
    reasoning_effort: str | None = None,
) -> str | None:
    """Call an OpenAI-compatible chat endpoint with bounded retries."""
    try:
        from openai import OpenAIError
    except ImportError as exc:
        raise RuntimeError("The 'openai' package is required for API commands.") from exc

    logger = logging.getLogger(__name__)
    for attempt in range(1, max_retries + 1):
        try:
            options: dict[str, Any] = {
                "model": model,
                "messages": list(messages),
                "stream": False,
                "extra_body": {"thinking": {"type": "enabled"}},
            }
            if reasoning_effort:
                options["reasoning_effort"] = reasoning_effort
            response = client.chat.completions.create(**options)
            content = response.choices[0].message.content
            if content and content.strip():
                return content.strip()
            logger.warning("API returned empty content (attempt %d/%d).", attempt, max_retries)
        except OpenAIError as exc:
            logger.warning("API request failed (attempt %d/%d): %s", attempt, max_retries, exc)
        if attempt < max_retries:
            time.sleep(retry_delay)
    return None


def call_chat_stubborn(
    client: Any,
    *,
    model: str,
    messages: Messages,
    max_retries: int,
    retry_delay: float,
    extra_attempts: int,
) -> str | None:
    """Retry a bounded chat call with increasing outer delays."""
    for outer_attempt in range(extra_attempts + 1):
        result = call_chat(
            client,
            model=model,
            messages=messages,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        if result is not None:
            return result
        if outer_attempt < extra_attempts:
            wait = retry_delay * (outer_attempt + 1) * 2
            logging.getLogger(__name__).warning(
                "Outer retry %d/%d will start in %.1f seconds.",
                outer_attempt + 1,
                extra_attempts,
                wait,
            )
            time.sleep(wait)
    return None


def parse_json_array(raw: str | None) -> list[Any] | None:
    """Parse a JSON array, tolerating fenced or surrounding model text."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        cleaned = "\n".join(lines).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, list) else None


def new_run_directory(root: Path, name: str) -> Path:
    """Create a unique timestamped result directory."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = root / f"{stamp}-{name}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{stamp}-{name}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate
