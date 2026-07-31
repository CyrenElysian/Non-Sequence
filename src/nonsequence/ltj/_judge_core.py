"""Shared implementation for the LTJ judge command-line programs."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from ._shared import (
        DEFAULT_DATA_DIR,
        DEFAULT_JUDGE_PROMPT_DIR,
        DEFAULT_RESULTS_DIR,
        call_chat,
        configure_logging,
        create_client,
        load_json,
        load_text,
        new_run_directory,
        require_api_key,
        save_json,
    )
except ImportError:
    from _shared import (  # type: ignore[no-redef]
        DEFAULT_DATA_DIR,
        DEFAULT_JUDGE_PROMPT_DIR,
        DEFAULT_RESULTS_DIR,
        call_chat,
        configure_logging,
        create_client,
        load_json,
        load_text,
        new_run_directory,
        require_api_key,
        save_json,
    )

LOGGER = logging.getLogger(__name__)
DIFFICULTIES = ("easy", "medium", "hard", "na")


def build_parser(include_reason: bool) -> argparse.ArgumentParser:
    """Build the judge CLI parser."""
    command = "judge_reason" if include_reason else "judge"
    parser = argparse.ArgumentParser(
        prog=command,
        description="Evaluate LTJ descriptions with labels 0, 1, and 2.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_DATA_DIR / "descriptions_dev.json",
        help="Description dataset JSON.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=DEFAULT_JUDGE_PROMPT_DIR
        / ("prompt_judge_reason.txt" if include_reason else "prompt_judge.txt"),
        help="System prompt file.",
    )
    parser.add_argument("--model", default="deepseek-v4-pro", help="Model name.")
    parser.add_argument("--base-url", default="https://api.deepseek.com", help="API base URL.")
    parser.add_argument(
        "--api-key-env",
        default="DEEPSEEK_API_KEY",
        help="Environment variable containing the API key.",
    )
    parser.add_argument("--max-retries", type=int, default=3, help="Retries per prediction.")
    parser.add_argument("--retry-delay", type=float, default=3.0, help="Retry delay in seconds.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Delay between input items.")
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Parent directory for a new run.",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="Resume only from a checkpoint.json created by this command.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def parse_prediction(raw: str) -> int | None:
    """Extract a label whose meaning remains: 0 stop, 1 continue, 2 not applicable."""
    cleaned = raw.strip()
    try:
        value = int(cleaned)
    except ValueError:
        match = re.search(r"\b([012])\b", cleaned)
        return int(match.group(1)) if match else None
    return value if value in (0, 1, 2) else None


def parse_prediction_with_reason(raw: str) -> tuple[int | None, str]:
    """Extract a first-line prediction and the remaining explanation."""
    cleaned = raw.strip()
    lines = cleaned.splitlines()
    prediction = parse_prediction(lines[0]) if lines else None
    reason = " ".join(line.strip() for line in lines[1:]).strip()
    if prediction is None:
        prediction = parse_prediction(cleaned)
        reason = cleaned
    return prediction, reason


def _prediction_key(item_id: Any, description_index: int) -> str:
    return f"{item_id}:{description_index}"


def _load_resume(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    checkpoint = path / "checkpoint.json" if path.is_dir() else path
    payload = load_json(checkpoint)
    if not isinstance(payload, dict) or payload.get("format") != "ltj-judge-v1":
        raise ValueError(f"Not an LTJ judge checkpoint: {checkpoint}")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Checkpoint results must be a list: {checkpoint}")
    return checkpoint.parent, results


def _save_checkpoint(
    run_directory: Path,
    results: list[dict[str, Any]],
    include_reason: bool,
) -> None:
    save_json(
        run_directory / "checkpoint.json",
        {
            "format": "ltj-judge-v1",
            "include_reason": include_reason,
            "results": results,
        },
    )


def _build_statistics(
    results: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    statistics: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    confusion: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for result in results:
        true_answer = int(result["true_answer"])
        predicted = int(result["predicted"])
        difficulty = str(result["difficulty"])
        correct = bool(result["correct"])
        for key in (
            "overall",
            f"answer_{true_answer}",
            f"difficulty_{difficulty}",
            f"answer_{true_answer}_{difficulty}",
        ):
            statistics[key]["total"] += 1
            if correct:
                statistics[key]["correct"] += 1
        confusion[f"{true_answer}:{difficulty}"][str(predicted)] += 1
    return (
        {key: dict(value) for key, value in statistics.items()},
        {key: dict(value) for key, value in confusion.items()},
    )


def _write_outputs(
    run_directory: Path,
    results: list[dict[str, Any]],
    include_reason: bool,
) -> None:
    statistics, confusion = _build_statistics(results)
    total = len(results)
    correct = sum(bool(result["correct"]) for result in results)
    save_json(run_directory / "details.json", results)
    save_json(
        run_directory / "summary.json",
        {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total else None,
            "statistics": statistics,
            "confusion": confusion,
        },
    )
    if include_reason:
        save_json(
            run_directory / "reasons.json",
            [
                {
                    key: result[key]
                    for key in (
                        "item_id",
                        "scenario",
                        "desc_idx",
                        "description",
                        "true_answer",
                        "predicted",
                        "reason",
                    )
                }
                for result in results
            ],
        )
    LOGGER.info(
        "Saved %d predictions (%d correct, %.2f%%) to %s.",
        total,
        correct,
        correct / total * 100 if total else 0.0,
        run_directory,
    )


def run_judge(arguments: argparse.Namespace, include_reason: bool) -> None:
    """Execute one judge variant."""
    if arguments.max_retries < 1:
        raise ValueError("--max-retries must be at least 1.")
    if arguments.retry_delay < 0 or arguments.sleep < 0:
        raise ValueError("Delay values cannot be negative.")

    configure_logging(arguments.verbose)
    data = load_json(arguments.input)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {arguments.input}.")
    prompt = load_text(arguments.prompt)
    api_key = require_api_key(arguments.api_key_env)
    client = create_client(api_key, arguments.base_url)

    if arguments.resume:
        run_directory, results = _load_resume(arguments.resume)
        LOGGER.info("Resuming %d existing predictions from %s.", len(results), run_directory)
    else:
        run_directory = new_run_directory(
            arguments.results_root,
            "judge-reason" if include_reason else "judge",
        )
        results = []

    completed = {
        _prediction_key(result["item_id"], int(result["desc_idx"])) for result in results
    }
    for item_index, item in enumerate(data, start=1):
        descriptions = item["descriptions"]
        steps_text = json.dumps(item["steps"], ensure_ascii=False)
        LOGGER.info("Processing item %d/%d: %s", item_index, len(data), item["scenario"])
        for description_index, description_entry in enumerate(descriptions):
            key = _prediction_key(item["id"], description_index)
            if key in completed:
                continue
            description, true_answer, difficulty = description_entry
            user_prompt = (
                f'Scenario: "{item["scenario"]}"\n'
                f"Steps: {steps_text}\n"
                f'Loop step index: {item["loop_idx"]}, '
                f'loop step content: "{item["loop_step"]}"\n'
                f'Description: "{description}"'
            )
            response = call_chat(
                client,
                model=arguments.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_retries=arguments.max_retries,
                retry_delay=arguments.retry_delay,
            )
            if response is None:
                LOGGER.error("Skipping item %s description %d after API failures.", item["id"], description_index)
                continue
            prediction, reason = (
                parse_prediction_with_reason(response)
                if include_reason
                else (parse_prediction(response), "")
            )
            if prediction is None:
                LOGGER.error("Could not parse item %s description %d.", item["id"], description_index)
                continue
            result: dict[str, Any] = {
                "item_id": item["id"],
                "scenario": item["scenario"],
                "desc_idx": description_index,
                "description": description,
                "true_answer": true_answer,
                "predicted": prediction,
                "difficulty": difficulty,
                "correct": prediction == true_answer,
            }
            if include_reason:
                result["reason"] = reason
            results.append(result)
            completed.add(key)
            _save_checkpoint(run_directory, results, include_reason)
        if item_index < len(data):
            time.sleep(arguments.sleep)
    _write_outputs(run_directory, results, include_reason)


def cli(include_reason: bool) -> None:
    """Parse command-line arguments and run a judge variant."""
    arguments = build_parser(include_reason).parse_args()
    try:
        run_judge(arguments, include_reason)
    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc
