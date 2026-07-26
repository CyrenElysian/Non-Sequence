"""Apply model-based quality filtering to detected loop records."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

try:
    from ._shared import (
        DEFAULT_CHECKPOINT_DIR,
        DEFAULT_DATA_DIR,
        DEFAULT_FILTER_PROMPT_DIR,
        call_chat_stubborn,
        configure_logging,
        create_client,
        load_json,
        load_text,
        parse_json_array,
        require_api_key,
        save_json,
    )
except ImportError:
    from _shared import (  # type: ignore[no-redef]
        DEFAULT_CHECKPOINT_DIR,
        DEFAULT_DATA_DIR,
        DEFAULT_FILTER_PROMPT_DIR,
        call_chat_stubborn,
        configure_logging,
        create_client,
        load_json,
        load_text,
        parse_json_array,
        require_api_key,
        save_json,
    )

LOGGER = logging.getLogger(__name__)
DEFAULT_CHECKPOINT = DEFAULT_CHECKPOINT_DIR / "filter_loop_quality_checkpoint.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_DATA_DIR / "filter_loop_dev.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DATA_DIR / "filter_loop_quality_dev.json",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=DEFAULT_FILTER_PROMPT_DIR / "prompt_filter_quality.txt",
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument(
        "--resume",
        nargs="?",
        const=DEFAULT_CHECKPOINT,
        type=Path,
        help="Resume from a checkpoint, optionally at a custom path.",
    )
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=3.0)
    parser.add_argument("--extra-attempts", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def _load_progress(path: Path) -> tuple[list[dict[str, Any]], set[Any]]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("format") != "ltj-quality-filter-v1":
        raise ValueError(f"Not an LTJ quality-filter checkpoint: {path}")
    results = payload.get("results")
    processed_ids = payload.get("processed_ids")
    if not isinstance(results, list) or not isinstance(processed_ids, list):
        raise ValueError(f"Checkpoint lists are invalid: {path}")
    return results, set(processed_ids)


def run(arguments: argparse.Namespace) -> None:
    """Run quality filtering with item-level checkpointing."""
    if arguments.batch_size < 1 or arguments.max_retries < 1:
        raise ValueError("Batch size and retry count must be positive.")
    if arguments.extra_attempts < 0:
        raise ValueError("--extra-attempts cannot be negative.")
    if arguments.retry_delay < 0 or arguments.sleep < 0:
        raise ValueError("Delay values cannot be negative.")

    data = load_json(arguments.input)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {arguments.input}.")
    prompt_template = load_text(arguments.prompt)
    client = create_client(
        require_api_key(arguments.api_key_env),
        arguments.base_url,
    )
    if arguments.resume:
        results, processed_ids = _load_progress(arguments.resume)
    else:
        results, processed_ids = [], set()
    result_ids = {
        item["id"] for item in results if isinstance(item, dict) and "id" in item
    }
    batches = [
        data[index : index + arguments.batch_size]
        for index in range(0, len(data), arguments.batch_size)
    ]

    for batch_index, batch in enumerate(batches, start=1):
        pending = [
            item
            for item in batch
            if isinstance(item, dict) and item.get("id") not in processed_ids
        ]
        if not pending:
            LOGGER.info("Skipping completed batch %d/%d.", batch_index, len(batches))
            continue
        response = call_chat_stubborn(
            client,
            model=arguments.model,
            messages=[
                {
                    "role": "user",
                    "content": prompt_template
                    + "\n"
                    + json.dumps(pending, ensure_ascii=False, indent=2),
                }
            ],
            max_retries=arguments.max_retries,
            retry_delay=arguments.retry_delay,
            extra_attempts=arguments.extra_attempts,
        )
        parsed = parse_json_array(response)
        if parsed is None:
            LOGGER.error("Skipping batch %d/%d after invalid output.", batch_index, len(batches))
        else:
            added = 0
            for item in parsed:
                if isinstance(item, dict) and "id" in item and item["id"] not in result_ids:
                    results.append(item)
                    result_ids.add(item["id"])
                    added += 1
            processed_ids.update(item["id"] for item in pending)
            LOGGER.info("Batch %d/%d retained %d records.", batch_index, len(batches), added)
        save_json(
            arguments.checkpoint,
            {
                "format": "ltj-quality-filter-v1",
                "processed_ids": sorted(processed_ids),
                "results": results,
            },
        )
        if batch_index < len(batches):
            time.sleep(arguments.sleep)

    final_results = [dict(item, id=index) for index, item in enumerate(results, start=1)]
    save_json(arguments.output, final_results)
    LOGGER.info("Saved %d quality records to %s.", len(final_results), arguments.output)


def main() -> None:
    """Parse arguments and run quality filtering."""
    arguments = build_parser().parse_args()
    configure_logging(arguments.verbose)
    try:
        run(arguments)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
