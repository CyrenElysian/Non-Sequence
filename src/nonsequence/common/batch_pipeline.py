"""Reusable batch pipeline for OpenAI-compatible JSON transformations."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .json_utils import atomic_write_json, load_checkpoint, load_json, parse_fenced_json
from .openai_client import api_error_types, create_client

LOGGER = logging.getLogger(__name__)
FORMAT_INSTRUCTION = (
    "\n\nIMPORTANT: Output a JSON object enclosed in a ```json code block. "
    'It must contain exactly "processed_data" (array) and "change_log" (array). '
    "Do not include text outside the code block."
)


def run_batch_pipeline(
    *,
    input_path: Path,
    prompt_path: Path,
    output_path: Path,
    log_path: Path,
    checkpoint_path: Path,
    api_key_env: str,
    base_url: str,
    model: str,
    batch_size: int,
    retries: int,
    retry_delay: float,
    batch_delay: float,
    overwrite_checkpoint: bool,
    user_instruction: str,
    reasoning: bool,
) -> None:
    """Process a dataset in resumable batches and atomically save all outputs."""
    dataset = load_json(input_path)
    if not isinstance(dataset, list):
        raise ValueError("Input JSON must be an array")
    system_prompt = prompt_path.read_text(encoding="utf-8") + FORMAT_INSTRUCTION
    batches = [
        dataset[index : index + batch_size]
        for index in range(0, len(dataset), batch_size)
    ]

    checkpoint = (
        {}
        if overwrite_checkpoint
        else load_checkpoint(checkpoint_path, {})
    )
    processed_data = checkpoint.get("processed_data", [])
    change_log = checkpoint.get("change_log", [])
    start_batch = checkpoint.get("next_batch_index", 0)
    if not isinstance(processed_data, list) or not isinstance(change_log, list):
        raise ValueError("Checkpoint result fields must be arrays")
    if not isinstance(start_batch, int):
        raise ValueError("Checkpoint next_batch_index must be an integer")

    client = create_client(api_key_env=api_key_env, base_url=base_url)
    retryable_api_errors = api_error_types()
    LOGGER.info(
        "Loaded %d items in %d batches; starting at batch %d",
        len(dataset),
        len(batches),
        start_batch + 1,
    )
    for index in range(start_batch, len(batches)):
        batch = batches[index]
        user_message = (
            f"{user_instruction}\n```json\n"
            f"{json.dumps(batch, ensure_ascii=False, indent=2)}\n```"
        )
        success = False
        for attempt in range(1, retries + 1):
            try:
                request: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "stream": False,
                }
                if reasoning:
                    request.update(
                        reasoning_effort="high",
                        extra_body={"thinking": {"type": "enabled"}},
                    )
                response = client.chat.completions.create(**request)
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    raise ValueError("The API response has no textual content")
                result = parse_fenced_json(content)
                batch_data = result["processed_data"]
                batch_log = result["change_log"]
                if not isinstance(batch_data, list) or not isinstance(batch_log, list):
                    raise ValueError("Response result fields must be arrays")
                processed_data.extend(batch_data)
                change_log.extend(batch_log)
                success = True
                break
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                LOGGER.warning("Batch %d attempt %d failed: %s", index + 1, attempt, error)
                if attempt < retries:
                    time.sleep(retry_delay)
            except retryable_api_errors as error:
                LOGGER.warning(
                    "Batch %d API attempt %d failed: %s", index + 1, attempt, error
                )
                if attempt < retries:
                    time.sleep(retry_delay)

        next_index = index + 1 if success else index
        atomic_write_json(
            checkpoint_path,
            {
                "processed_data": processed_data,
                "change_log": change_log,
                "next_batch_index": next_index,
            },
        )
        if not success:
            raise RuntimeError(f"Batch {index + 1} failed after {retries} attempts")
        LOGGER.info(
            "Completed batch %d/%d; %d items accumulated",
            index + 1,
            len(batches),
            len(processed_data),
        )
        if index + 1 < len(batches):
            time.sleep(batch_delay)

    atomic_write_json(output_path, processed_data)
    atomic_write_json(log_path, change_log)
