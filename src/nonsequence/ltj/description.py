"""Generate labeled LTJ descriptions for continue, stop, and not-applicable cases."""

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
        DEFAULT_GENERATION_PROMPT_DIR,
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
        DEFAULT_GENERATION_PROMPT_DIR,
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
DEFAULT_CHECKPOINT = DEFAULT_CHECKPOINT_DIR / "description_checkpoint.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_DATA_DIR / "filter_loop_quality_dev.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DATA_DIR / "descriptions_dev.json",
    )
    parser.add_argument(
        "--prompt-dir",
        type=Path,
        default=DEFAULT_GENERATION_PROMPT_DIR,
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
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=3.0)
    parser.add_argument("--extra-attempts", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def _load_templates(prompt_directory: Path) -> dict[str, str]:
    names = {
        "cont1": "prompt_continue_1.txt",
        "cont2": "prompt_continue_2.txt",
        "cont3": "prompt_continue_3.txt",
        "stop1": "prompt_stop_1.txt",
        "stop2": "prompt_stop_2.txt",
        "stop3": "prompt_stop_3.txt",
        "na": "prompt_na.txt",
    }
    return {key: load_text(prompt_directory / filename) for key, filename in names.items()}


def _clean(text: str | None) -> str:
    return (text or "").strip().strip('"').strip("'").strip()


def _load_progress(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("format") != "ltj-description-v1":
        raise ValueError(f"Not an LTJ description checkpoint: {path}")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Checkpoint results must be a list: {path}")
    return results


def _generate_chain(
    *,
    client: Any,
    model: str,
    templates: dict[str, str],
    prefix: str,
    label: int,
    fields: dict[str, Any],
    max_retries: int,
    retry_delay: float,
    extra_attempts: int,
) -> list[list[Any]]:
    descriptions: list[list[Any]] = []
    messages: list[dict[str, str]] = [
        {"role": "user", "content": templates[f"{prefix}1"].format(**fields)}
    ]
    first = _clean(
        call_chat_stubborn(
            client,
            model=model,
            messages=messages,
            max_retries=max_retries,
            retry_delay=retry_delay,
            extra_attempts=extra_attempts,
        )
    )
    if not first:
        LOGGER.error("%s chain failed at easy difficulty.", prefix)
        return descriptions
    descriptions.append([first, label, "easy"])

    second_prompt = templates[f"{prefix}2"].format(**fields, reason_one=first)
    messages.extend(
        [{"role": "assistant", "content": first}, {"role": "user", "content": second_prompt}]
    )
    second = _clean(
        call_chat_stubborn(
            client,
            model=model,
            messages=messages,
            max_retries=max_retries,
            retry_delay=retry_delay,
            extra_attempts=extra_attempts,
        )
    )
    if not second:
        return descriptions
    descriptions.append([second, label, "medium"])

    third_prompt = templates[f"{prefix}3"].format(**fields, reason_two=second)
    messages.extend(
        [{"role": "assistant", "content": second}, {"role": "user", "content": third_prompt}]
    )
    third = _clean(
        call_chat_stubborn(
            client,
            model=model,
            messages=messages,
            max_retries=max_retries,
            retry_delay=retry_delay,
            extra_attempts=extra_attempts,
        )
    )
    if third:
        descriptions.append([third, label, "hard"])
    return descriptions


def _generate_item(
    item: dict[str, Any],
    templates: dict[str, str],
    client: Any,
    arguments: argparse.Namespace,
) -> list[list[Any]]:
    fields = {
        "scenario": item["scenario"],
        "steps": json.dumps(item["steps"], ensure_ascii=False),
        "loop_idx": item["loop_idx"],
        "loop_step": item["loop_step"],
    }
    descriptions = _generate_chain(
        client=client,
        model=arguments.model,
        templates=templates,
        prefix="cont",
        label=1,
        fields=fields,
        max_retries=arguments.max_retries,
        retry_delay=arguments.retry_delay,
        extra_attempts=arguments.extra_attempts,
    )
    descriptions.extend(
        _generate_chain(
            client=client,
            model=arguments.model,
            templates=templates,
            prefix="stop",
            label=0,
            fields=fields,
            max_retries=arguments.max_retries,
            retry_delay=arguments.retry_delay,
            extra_attempts=arguments.extra_attempts,
        )
    )
    raw_na = call_chat_stubborn(
        client,
        model=arguments.model,
        messages=[{"role": "user", "content": templates["na"].format(**fields)}],
        max_retries=arguments.max_retries,
        retry_delay=arguments.retry_delay,
        extra_attempts=arguments.extra_attempts,
    )
    na_values = parse_json_array(raw_na)
    if na_values is not None and len(na_values) >= 2:
        descriptions.extend([[na_values[0], 2, "na"], [na_values[1], 2, "na"]])
    else:
        LOGGER.error("Not-applicable generation returned fewer than two values.")
    return descriptions


def run(arguments: argparse.Namespace) -> None:
    """Generate descriptions while preserving label semantics."""
    if arguments.max_retries < 1 or arguments.extra_attempts < 0:
        raise ValueError("Retry count must be positive and extra attempts cannot be negative.")
    if arguments.retry_delay < 0 or arguments.sleep < 0:
        raise ValueError("Delay values cannot be negative.")
    data = load_json(arguments.input)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {arguments.input}.")
    templates = _load_templates(arguments.prompt_dir)
    client = create_client(
        require_api_key(arguments.api_key_env),
        arguments.base_url,
    )
    results = _load_progress(arguments.resume) if arguments.resume else []
    completed_ids = {
        result["id"] for result in results if isinstance(result, dict) and "id" in result
    }

    for item_index, item in enumerate(data, start=1):
        if item["id"] in completed_ids:
            continue
        LOGGER.info("Generating item %d/%d: %s", item_index, len(data), item["scenario"])
        descriptions = _generate_item(item, templates, client, arguments)
        if descriptions:
            results.append(
                {
                    "id": item["id"],
                    "scenario": item["scenario"],
                    "steps": item["steps"],
                    "loop_idx": item["loop_idx"],
                    "loop_step": item["loop_step"],
                    "descriptions": descriptions,
                }
            )
            completed_ids.add(item["id"])
        else:
            LOGGER.error("No descriptions were generated for item %s.", item["id"])
        save_json(
            arguments.checkpoint,
            {"format": "ltj-description-v1", "results": results},
        )
        if item_index < len(data):
            time.sleep(arguments.sleep)

    final_results = [dict(item, id=index) for index, item in enumerate(results, start=1)]
    save_json(arguments.output, final_results)
    LOGGER.info("Saved %d described records to %s.", len(final_results), arguments.output)


def main() -> None:
    """Parse arguments and generate descriptions."""
    arguments = build_parser().parse_args()
    configure_logging(arguments.verbose)
    try:
        run(arguments)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
