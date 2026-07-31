"""Correct event-graph edges with an OpenAI-compatible model."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from nonsequence.common import (
    api_error_types,
    atomic_write_json,
    create_client,
    load_checkpoint,
    load_json,
    parse_fenced_json,
)

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTERIM_DIR = PROJECT_ROOT / "data" / "interim" / "ctrlscript"
DEFAULT_PROMPT = PROJECT_ROOT / "prompts" / "preprocessing" / "prompt_correct.txt"
DEFAULT_INPUT = INTERIM_DIR / "convert_dev.json"
DEFAULT_OUTPUT = INTERIM_DIR / "correct_dev.json"
DEFAULT_CHANGE_LOG = INTERIM_DIR / "change_log.json"


def call_llm(
    client: Any,
    prompt_template: str,
    item: dict[str, Any],
    *,
    model: str,
    retries: int,
    retry_delay: float,
    max_tokens: int,
) -> tuple[dict[str, Any] | None, list[Any]]:
    """Request one correction and return corrected data plus its change log."""
    prompt = prompt_template.format(
        scenario=item["scenario"],
        events_json=json.dumps(item["unordered_nodes"], ensure_ascii=False, indent=2),
        edges_json=json.dumps(item["edges"], ensure_ascii=False, indent=2),
    )
    retryable_api_errors = api_error_types()
    for attempt in range(1, retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise JSON generator. Output only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
                max_tokens=max_tokens,
                stream=False,
            )
            content = response.choices[0].message.content
            if not isinstance(content, str):
                raise ValueError("The API response has no textual content")
            result = parse_fenced_json(content)
            corrected = result.get("corrected_data")
            change_log = result.get("change_log", [])
            if not isinstance(corrected, dict) or "edges" not in corrected:
                raise ValueError("Response is missing corrected_data.edges")
            corrected["id"] = item["id"]
            return corrected, change_log if isinstance(change_log, list) else []
        except (ValueError, KeyError, AttributeError, TypeError) as error:
            LOGGER.warning("Attempt %d/%d failed: %s", attempt, retries, error)
        except retryable_api_errors as error:
            LOGGER.warning("API attempt %d/%d failed: %s", attempt, retries, error)
        if attempt < retries:
            time.sleep(retry_delay * (2 ** (attempt - 1)))
    return None, []


def original_entry(item: dict[str, Any]) -> dict[str, Any]:
    """Return the original entry fields used by the output schema."""
    return {
        "id": item["id"],
        "scenario": item["scenario"],
        "unordered_nodes": item["unordered_nodes"],
        "edges": item["edges"],
        "script_graph": item["script_graph"],
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments without loading files or creating a client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--change-log", type=Path, default=DEFAULT_CHANGE_LOG)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--save-interval", type=int, default=10)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2)
    parser.add_argument("--max-tokens", type=int, default=8192)
    return parser.parse_args()


def main() -> int:
    """Run edge correction with resumable atomic output writes."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        data = load_json(args.input)
        prompt_template = args.prompt.read_text(encoding="utf-8")
        finished = load_checkpoint(args.output, [])
        change_logs = load_checkpoint(args.change_log, {})
        if not isinstance(data, list) or not isinstance(finished, list):
            raise ValueError("Input and output checkpoint JSON must be arrays")
        if not isinstance(change_logs, dict):
            change_logs = {}
        client = create_client(api_key_env=args.api_key_env, base_url=args.base_url)
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("%s", error)
        return 1

    finished_ids = {
        item["id"] for item in finished if isinstance(item, dict) and "id" in item
    }
    for index, item in enumerate(data, start=1):
        item_id = item["id"]
        if item_id in finished_ids:
            LOGGER.info("Skipping %d/%d (ID %s)", index, len(data), item_id)
            continue
        LOGGER.info("Processing %d/%d (ID %s)", index, len(data), item_id)
        corrected, changes = call_llm(
            client,
            prompt_template,
            item,
            model=args.model,
            retries=args.retries,
            retry_delay=args.retry_delay,
            max_tokens=args.max_tokens,
        )
        finished.append(corrected if corrected is not None else original_entry(item))
        finished_ids.add(item_id)
        if changes:
            change_logs[str(item_id)] = changes
        if len(finished) % args.save_interval == 0:
            atomic_write_json(args.output, finished)
            atomic_write_json(args.change_log, change_logs)

    atomic_write_json(args.output, finished)
    atomic_write_json(args.change_log, change_logs)
    LOGGER.info("Wrote %d results to %s", len(finished), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
