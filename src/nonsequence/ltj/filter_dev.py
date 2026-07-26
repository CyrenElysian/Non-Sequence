"""Extract pure sequence examples from the control-script dataset."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

try:
    from ._shared import (
        DEFAULT_DATA_DIR,
        DEFAULT_INTERIM_CTRLSCRIPT_DIR,
        configure_logging,
        load_json,
        save_json,
    )
except ImportError:
    from _shared import (  # type: ignore[no-redef]
        DEFAULT_DATA_DIR,
        DEFAULT_INTERIM_CTRLSCRIPT_DIR,
        configure_logging,
        load_json,
        save_json,
    )

LOGGER = logging.getLogger(__name__)


def is_pure_sequence(script_graph: Any) -> bool:
    """Return whether a graph is a sequence containing only node identifiers."""
    if not isinstance(script_graph, dict) or script_graph.get("type") != "sequence":
        return False
    script = script_graph.get("script", [])
    return isinstance(script, list) and all(isinstance(node, str) for node in script)


def filter_and_transform(data: list[Any]) -> list[dict[str, Any]]:
    """Preserve scenario data while converting graph order to step text."""
    filtered: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not is_pure_sequence(item.get("script_graph")):
            continue
        nodes = item.get("unordered_nodes", {})
        if not isinstance(nodes, dict):
            continue
        script = item["script_graph"]["script"]
        filtered.append(
            {
                "id": len(filtered) + 1,
                "scenario": item["scenario"],
                "steps": [nodes.get(node_id, "") for node_id in script],
            }
        )
    return filtered


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INTERIM_CTRLSCRIPT_DIR / "llm_fixed_dev.json",
        help="Source control-script JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DATA_DIR / "filter_dev.json",
        help="Filtered sequence JSON.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main() -> None:
    """Run the pure-sequence filter."""
    arguments = build_parser().parse_args()
    configure_logging(arguments.verbose)
    try:
        data = load_json(arguments.input)
        if not isinstance(data, list):
            raise ValueError(f"Expected a JSON list in {arguments.input}.")
        filtered = filter_and_transform(data)
        save_json(arguments.output, filtered)
        LOGGER.info("Saved %d pure sequence records to %s.", len(filtered), arguments.output)
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
