"""Offline evaluation CLI for saved graph predictions."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Sequence

from nonsequence.common import atomic_write_json, load_json
from nonsequence.evaluation.metrics import compute_statistics, refresh_record_metrics

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "ctrlscript" / "CtrlScript_with_stats.json"
DEFAULT_PREDICTIONS = (
    PROJECT_ROOT / "results" / "ctrlscript" / "results_v4-flash.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "results" / "ctrlscript" / "eval_summary_v4-flash.json"
)


def evaluate_files(
    predictions_path: Path,
    dataset_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Recompute saved predictions against references without API access."""
    predictions = load_json(predictions_path)
    dataset = load_json(dataset_path)
    if not isinstance(predictions, list) or not isinstance(dataset, list):
        raise ValueError("Predictions and dataset JSON roots must both be arrays.")

    reference_graphs = {
        item["id"]: {
            "edges": item["edges"],
            "script_graph": item["script_graph"],
        }
        for item in dataset
    }
    reference_stats = {
        item["id"]: {
            "max_depth": item.get("max_depth", 0),
            "type_cnt": item.get("type_cnt", {}),
        }
        for item in dataset
    }
    records = refresh_record_metrics(predictions, reference_graphs)
    skipped = len(predictions) - len(records)
    if skipped:
        LOGGER.warning("Skipped %d predictions without references.", skipped)

    summary = compute_statistics(records, reference_stats)
    atomic_write_json(output_path, summary)
    LOGGER.info("Evaluated %d predictions.", len(records))
    LOGGER.info("Wrote summary to %s", output_path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    """Build the offline evaluation argument parser."""
    parser = argparse.ArgumentParser(
        description="Recompute graph-prediction metrics from saved JSON; no API is used."
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=DEFAULT_PREDICTIONS,
        help=f"Saved prediction records (default: {DEFAULT_PREDICTIONS})",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Reference dataset with stats (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Summary JSON destination (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run offline evaluation."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        evaluate_files(args.predictions, args.dataset, args.output)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        LOGGER.error("Evaluation failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
