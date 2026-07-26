"""Run graph prediction inference with checkpointed API calls."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from nonsequence.common import (
    api_error_types,
    atomic_write_json,
    create_client,
    load_json,
    parse_fenced_json,
)
from nonsequence.evaluation.metrics import compare_graphs, compute_edge_metrics

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "ctrlscript" / "CtrlScript_with_stats.json"
DEFAULT_PROMPT = (
    PROJECT_ROOT / "prompts" / "graph_prediction" / "prompt_predict.txt"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "ctrlscript" / "results_v4-flash.json"
DEFAULT_CHECKPOINT = (
    PROJECT_ROOT
    / "results"
    / "ctrlscript"
    / "checkpoints"
    / "results_v4-flash.checkpoint.json"
)


def build_user_message(item: Mapping[str, Any]) -> str:
    """Serialize the model input fields."""
    payload = {
        "id": item["id"],
        "scenario": item["scenario"],
        "unordered_nodes": item["unordered_nodes"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def call_model(
    client: Any,
    model: str,
    system_prompt: str,
    user_message: str,
) -> str:
    """Call the configured OpenAI-compatible chat-completions endpoint."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        stream=False,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("The model returned an empty response.")
    return content


def collect_script_nodes(structure: Any, used_ids: set[str]) -> None:
    """Collect node IDs from a generated script graph."""
    if isinstance(structure, str):
        if structure != "continue":
            used_ids.add(structure)
    elif isinstance(structure, list):
        for element in structure:
            collect_script_nodes(element, used_ids)
    elif isinstance(structure, dict):
        if "script" in structure:
            collect_script_nodes(structure["script"], used_ids)
        elif "options" in structure:
            collect_script_nodes(structure["options"], used_ids)
        elif "entry" in structure:
            used_ids.add(structure["entry"])
            collect_script_nodes(structure.get("retry", []), used_ids)
            used_ids.add(structure["exit"])
        elif "branches_set" in structure:
            collect_script_nodes(list(structure["branches_set"].values()), used_ids)


def predict_item(
    item: Mapping[str, Any],
    reference: Mapping[str, Any],
    client: Any,
    model: str,
    system_prompt: str,
) -> dict[str, Any]:
    """Predict one graph and return a self-contained result record."""
    raw_response = call_model(
        client, model, system_prompt, build_user_message(item)
    )
    generated = parse_fenced_json(raw_response)
    if "edges" not in generated or "script_graph" not in generated:
        raise ValueError("Model response is missing edges or script_graph.")

    predicted_edges = generated["edges"]
    predicted_script_graph = generated["script_graph"]
    edges_match, script_graph_match = compare_graphs(
        predicted_edges,
        predicted_script_graph,
        reference["edges"],
        reference["script_graph"],
    )
    used_ids: set[str] = set()
    collect_script_nodes(predicted_script_graph, used_ids)
    node_ids = set(item["unordered_nodes"].keys())
    return {
        "id": item["id"],
        "scenario": item["scenario"],
        "unordered_nodes": item["unordered_nodes"],
        "edges": predicted_edges,
        "script_graph": predicted_script_graph,
        "edges_match": edges_match,
        "sg_match": script_graph_match,
        "nodes_valid": used_ids == node_ids,
        **compute_edge_metrics(predicted_edges, reference["edges"]),
    }


def run_inference(
    dataset_path: Path,
    prompt_path: Path,
    output_path: Path,
    checkpoint_path: Path,
    client: Any,
    model: str,
    resume: bool,
    delay_seconds: float,
    api_errors: tuple[type[Exception], ...],
) -> list[dict[str, Any]]:
    """Run checkpointed inference over a graph dataset."""
    dataset = load_json(dataset_path)
    if not isinstance(dataset, list):
        raise ValueError("Dataset JSON root must be an array.")
    system_prompt = prompt_path.read_text(encoding="utf-8")
    references = {
        item["id"]: {
            "edges": item["edges"],
            "script_graph": item["script_graph"],
        }
        for item in dataset
    }

    records: list[dict[str, Any]] = []
    if resume and checkpoint_path.exists():
        checkpoint = load_json(checkpoint_path)
        if not isinstance(checkpoint, list):
            raise ValueError("Checkpoint JSON root must be an array.")
        records = [
            record for record in checkpoint if record.get("id") in references
        ]
        LOGGER.info("Resumed %d records from %s", len(records), checkpoint_path)

    processed_ids = {record["id"] for record in records}
    for item in dataset:
        record_id = item["id"]
        if record_id in processed_ids:
            LOGGER.debug("Skipping completed record %s", record_id)
            continue
        LOGGER.info("Processing record %s", record_id)
        try:
            record = predict_item(
                item,
                references[record_id],
                client,
                model,
                system_prompt,
            )
        except api_errors:
            atomic_write_json(checkpoint_path, records)
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            LOGGER.error("Record %s failed: %s", record_id, error)
            record = {
                "id": record_id,
                "scenario": item["scenario"],
                "unordered_nodes": item["unordered_nodes"],
                "edges": [],
                "script_graph": {"type": "sequence", "script": []},
                "edges_match": False,
                "sg_match": False,
                "nodes_valid": False,
                "error": str(error),
                **compute_edge_metrics([], references[record_id]["edges"]),
            }
        records.append(record)
        processed_ids.add(record_id)
        atomic_write_json(checkpoint_path, records)
        if delay_seconds:
            time.sleep(delay_seconds)

    atomic_write_json(output_path, records)
    atomic_write_json(checkpoint_path, records)
    LOGGER.info("Wrote %d predictions to %s", len(records), output_path)
    return records


def build_parser() -> argparse.ArgumentParser:
    """Build the inference argument parser."""
    parser = argparse.ArgumentParser(
        description="Run checkpointed graph prediction inference."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument(
        "--api-key-env",
        default="DEEPSEEK_API_KEY",
        help="Environment variable containing the API key.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume from the checkpoint when it exists (default: enabled).",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Delay between API calls (default: 1.0).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration and run inference."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.delay_seconds < 0:
        LOGGER.error("--delay-seconds cannot be negative.")
        return 2
    try:
        client = create_client(
            api_key_env=args.api_key_env,
            base_url=args.base_url,
        )
        api_errors = api_error_types()
    except RuntimeError as error:
        LOGGER.error("Inference configuration failed: %s", error)
        return 2
    try:
        run_inference(
            dataset_path=args.dataset,
            prompt_path=args.prompt,
            output_path=args.output,
            checkpoint_path=args.checkpoint,
            client=client,
            model=args.model,
            resume=args.resume,
            delay_seconds=args.delay_seconds,
            api_errors=api_errors,
        )
    except api_errors as error:
        LOGGER.error("Inference API call failed: %s", error)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        LOGGER.error("Inference failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
