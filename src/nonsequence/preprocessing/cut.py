"""Simplify ProScript splits and remove placeholder events."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from nonsequence.common import atomic_write_json, load_json

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "raw" / "proscript"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "ctrlscript"
SPLITS = ("dev", "train", "test")
KEYS_TO_REMOVE = (
    "context",
    "minutes",
    "events_minutes",
    "flatten_input_for_edge_prediction",
    "flatten_input_for_script_generation",
    "flatten_output_for_edge_prediction",
    "flatten_output_for_script_generation",
)


def clean_data(input_path: Path, output_path: Path) -> None:
    """Simplify one dataset split while preserving edge relationships."""
    data = load_json(input_path)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {input_path}")

    cleaned_data: list[dict[str, Any]] = []
    for source_item in data:
        if not isinstance(source_item, dict):
            raise ValueError("Every dataset item must be a JSON object")
        item = dict(source_item)
        for key in KEYS_TO_REMOVE:
            item.pop(key, None)

        events = item.get("events", {})
        if not isinstance(events, dict):
            raise ValueError("The 'events' field must be a JSON object")
        old_to_new: dict[str, str] = {}
        new_events: dict[str, Any] = {}
        for old_index in sorted(events, key=int):
            if events[old_index] != "NONE":
                new_index = str(len(new_events))
                old_to_new[old_index] = new_index
                new_events[new_index] = events[old_index]
        item["events"] = new_events

        new_edges: list[str] = []
        for edge in item.get("gold_edges_for_prediction", []):
            parts = edge.split("->")
            if len(parts) != 2:
                continue
            source, target = parts
            if source in old_to_new and target in old_to_new:
                new_edges.append(f"{old_to_new[source]}->{old_to_new[target]}")
        item["gold_edges_for_prediction"] = new_edges
        cleaned_data.append(item)

    atomic_write_json(output_path, cleaned_data)
    LOGGER.info("Wrote %d items to %s", len(cleaned_data), output_path)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--splits", nargs="+", default=SPLITS)
    return parser.parse_args()


def main() -> int:
    """Run the simplification pipeline."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        for split in args.splits:
            clean_data(
                args.input_dir / f"{split}.json",
                args.output_dir / f"proscript_simple_{split}.json",
            )
    except (OSError, ValueError, TypeError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
