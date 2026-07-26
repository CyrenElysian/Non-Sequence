"""Detect loop steps and retain only records containing a valid loop."""

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
        call_chat,
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
        call_chat,
        configure_logging,
        create_client,
        load_json,
        load_text,
        parse_json_array,
        require_api_key,
        save_json,
    )

LOGGER = logging.getLogger(__name__)
DEFAULT_CHECKPOINT = DEFAULT_CHECKPOINT_DIR / "filter_loop_checkpoint.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_DATA_DIR / "filter_dev.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA_DIR / "filter_loop_dev.json")
    parser.add_argument(
        "--prompt",
        type=Path,
        default=DEFAULT_FILTER_PROMPT_DIR / "prompt_filter.txt",
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
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def _load_progress(path: Path) -> tuple[int, list[dict[str, Any]], int]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("format") != "ltj-filter-loop-v1":
        raise ValueError(f"Not an LTJ loop-filter checkpoint: {path}")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Checkpoint results must be a list: {path}")
    return int(payload.get("next_batch", 0)), results, int(payload.get("skipped_batches", 0))


def _normalized_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": index,
            "scenario": item["scenario"],
            "steps": item["steps"],
            "loop_idx": item["loop_idx"],
            "loop_step": item["loop_step"],
        }
        for index, item in enumerate(results, start=1)
    ]


def run(arguments: argparse.Namespace) -> None:
    """Run batched loop detection."""
    if arguments.batch_size < 1 or arguments.max_retries < 1:
        raise ValueError("Batch size and retry count must be positive.")
    if arguments.retry_delay < 0 or arguments.sleep < 0:
        raise ValueError("Delay values cannot be negative.")

    data = load_json(arguments.input)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {arguments.input}.")
    prompt = load_text(arguments.prompt)
    client = create_client(
        require_api_key(arguments.api_key_env),
        arguments.base_url,
    )
    if arguments.resume:
        start_batch, results, skipped_batches = _load_progress(arguments.resume)
        LOGGER.info("Resuming at batch %d with %d records.", start_batch + 1, len(results))
    else:
        start_batch, results, skipped_batches = 0, [], 0

    total_batches = (len(data) + arguments.batch_size - 1) // arguments.batch_size
    for batch_index in range(start_batch, total_batches):
        start = batch_index * arguments.batch_size
        batch = data[start : start + arguments.batch_size]
        response = call_chat(
            client,
            model=arguments.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
            ],
            max_retries=arguments.max_retries,
            retry_delay=arguments.retry_delay,
            reasoning_effort="high",
        )
        parsed = parse_json_array(response)
        if parsed is None:
            skipped_batches += 1
            LOGGER.error("Skipping batch %d/%d after failed or invalid output.", batch_index + 1, total_batches)
        else:
            valid = [
                item
                for item in parsed
                if isinstance(item, dict)
                and isinstance(item.get("loop_idx"), int)
                and item["loop_idx"] >= 0
            ]
            results.extend(valid)
            LOGGER.info(
                "Batch %d/%d retained %d of %d returned records.",
                batch_index + 1,
                total_batches,
                len(valid),
                len(parsed),
            )
        save_json(
            arguments.checkpoint,
            {
                "format": "ltj-filter-loop-v1",
                "next_batch": batch_index + 1,
                "skipped_batches": skipped_batches,
                "results": results,
            },
        )
        if batch_index + 1 < total_batches:
            time.sleep(arguments.sleep)

    final_results = _normalized_results(results)
    save_json(arguments.output, final_results)
    LOGGER.info(
        "Saved %d loop records to %s; skipped %d batches.",
        len(final_results),
        arguments.output,
        skipped_batches,
    )


def main() -> None:
    """Parse arguments and run loop detection."""
    arguments = build_parser().parse_args()
    configure_logging(arguments.verbose)
    try:
        run(arguments)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
